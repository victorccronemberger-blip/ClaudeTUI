"""Adapt ACP output and caller-owned MCP tools to FCC's shared Chat transport."""

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx2
from aiohttp import web

from .acp import ACPConnection, GeminiCLIError
from .auth import GeminiCLIAuthManager


class ToolBridge:
    """Advertise caller tools; return calls to FCC without executing them here."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = {tool["function"]["name"]: tool["function"] for tool in tools}
        self.calls: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.release = asyncio.Event()
        self.token = secrets.token_urlsafe(32)

    async def handle(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer " + self.token:
            return web.Response(status=401)
        try:
            packet = await request.json()
            method = packet.get("method")
            request_id = packet.get("id")
            if request_id is None:
                return web.Response(status=202)
            if method == "initialize":
                result = {
                    "protocolVersion": packet.get("params", {}).get(
                        "protocolVersion", "2024-11-05"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fcc", "version": "1"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": name,
                            "description": tool.get("description", ""),
                            "inputSchema": tool.get(
                                "parameters", {"type": "object", "properties": {}}
                            ),
                        }
                        for name, tool in self.tools.items()
                    ]
                }
            elif method == "tools/call":
                params = packet.get("params", {})
                name = params.get("name")
                arguments = params.get("arguments", {})
                if name not in self.tools or not isinstance(arguments, dict):
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32602,
                                "message": "Unknown caller tool or invalid arguments",
                            },
                        }
                    )
                await self.calls.put({"name": name, "arguments": arguments})
                # Keep the native agent waiting. FCC will finish this response with
                # tool_use, let its caller execute the tool, and replay the returned
                # result as conversation context on the next request.
                await self.release.wait()
                result = {
                    "isError": True,
                    "content": [
                        {"type": "text", "text": "Control returned to FCC caller."}
                    ],
                }
            else:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id, "result": result}
            )
        except ValueError, AttributeError, TypeError:
            return web.Response(status=400)

    @asynccontextmanager
    async def server(self):
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_post("/mcp", self.handle)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            yield {
                "type": "http",
                "name": "fcc",
                "url": f"http://127.0.0.1:{port}/mcp",
                "headers": [{"name": "Authorization", "value": "Bearer " + self.token}],
            }
        finally:
            self.release.set()
            await runner.cleanup()


def conversation_prompt(body: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []

    def transform(value: Any) -> Any:
        if isinstance(value, list):
            return [transform(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                image = value.get("image_url", {})
                url = image.get("url", "") if isinstance(image, dict) else image
                if (
                    not isinstance(url, str)
                    or not url.startswith("data:image/")
                    or ";base64," not in url
                ):
                    raise GeminiCLIError(
                        "Gemini CLI bridge accepts inline base64 images, not remote image URLs."
                    )
                mime, data = url[5:].split(";base64,", 1)
                images.append({"type": "image", "mimeType": mime, "data": data})
                return {"type": "image", "attachment": len(images)}
            return {key: transform(item) for key, item in value.items()}
        return value

    history = transform(body.get("messages", []))
    text = (
        "Continue the following FCC conversation. JSON roles describe the existing conversation; "
        "answer the final user/tool turn. Use only the provided FCC MCP tools when an action is needed. "
        "Prior tool results are already present in the history. Images follow in attachment order.\n"
        + json.dumps(history, ensure_ascii=False)
    )
    return [{"type": "text", "text": text}, *images]


class ACPChatStream(httpx2.AsyncByteStream):
    def __init__(
        self,
        stack: AsyncExitStack,
        connection: ACPConnection,
        bridge: ToolBridge,
        session_id: str,
        body: dict[str, Any],
        prompt: list[dict[str, Any]],
        on_closed: Callable[[ACPChatStream], None],
    ) -> None:
        self.stack, self.connection, self.bridge = stack, connection, bridge
        self.session_id, self.body, self.prompt = session_id, body, prompt
        self.identifier = "chatcmpl_" + uuid.uuid4().hex
        self._tasks: list[asyncio.Task[Any]] = []
        self._closing: asyncio.Task[None] | None = None
        self._on_closed = on_closed

    def frame(
        self,
        delta: dict[str, Any],
        finish: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> bytes:
        payload: dict[str, Any] = {
            "id": self.identifier,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": self.body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            payload["usage"] = usage
        return ("data: " + json.dumps(payload) + "\n\n").encode()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        prompt_task = asyncio.create_task(
            self.connection.call(
                "session/prompt", {"sessionId": self.session_id, "prompt": self.prompt}
            )
        )
        update = asyncio.create_task(self.connection.events.get())
        tool = asyncio.create_task(self.bridge.calls.get())
        self._tasks = [prompt_task, update, tool]
        try:
            yield self.frame({"role": "assistant"})
            while True:
                done, _ = await asyncio.wait(
                    [prompt_task, update, tool], return_when=asyncio.FIRST_COMPLETED
                )
                if update in done:
                    event = update.result()
                    if (
                        event.get("sessionUpdate") == "agent_message_chunk"
                        and event.get("content", {}).get("type") == "text"
                    ):
                        yield self.frame({"content": event["content"].get("text", "")})
                    update = asyncio.create_task(self.connection.events.get())
                    self._tasks[1] = update
                if tool in done:
                    call = tool.result()
                    yield self.frame(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_" + uuid.uuid4().hex,
                                    "type": "function",
                                    "function": {
                                        "name": call["name"],
                                        "arguments": json.dumps(call["arguments"]),
                                    },
                                }
                            ]
                        }
                    )
                    yield self.frame({}, "tool_calls")
                    break
                if prompt_task in done:
                    result = prompt_task.result()
                    # Drain notifications already read before the terminal RPC reply.
                    if update.done():
                        event = update.result()
                        if (
                            event.get("sessionUpdate") == "agent_message_chunk"
                            and event.get("content", {}).get("type") == "text"
                        ):
                            yield self.frame(
                                {"content": event["content"].get("text", "")}
                            )
                    while not self.connection.events.empty():
                        event = self.connection.events.get_nowait()
                        if (
                            event.get("sessionUpdate") == "agent_message_chunk"
                            and event.get("content", {}).get("type") == "text"
                        ):
                            yield self.frame(
                                {"content": event["content"].get("text", "")}
                            )
                    reason = result.get("stopReason")
                    if reason not in {"end_turn", "max_tokens"}:
                        raise GeminiCLIError(
                            "Gemini CLI did not complete the requested turn."
                        )
                    native_usage = result.get("usage")
                    usage = None
                    if isinstance(native_usage, dict):
                        usage = {
                            "prompt_tokens": native_usage.get("inputTokens", 0),
                            "completion_tokens": native_usage.get("outputTokens", 0),
                            "total_tokens": native_usage.get("totalTokens", 0),
                        }
                    yield self.frame(
                        {}, "length" if reason == "max_tokens" else "stop", usage
                    )
                    break
            yield b"data: [DONE]\n\n"
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        try:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.connection.close()
        finally:
            self.bridge.release.set()
            await self.stack.aclose()
            self._on_closed(self)


class GeminiACPTransport(httpx2.AsyncBaseTransport):
    """Local SDK transport; no requests or OAuth tokens go to an emulated API."""

    def __init__(self, auth: GeminiCLIAuthManager, timeout: float) -> None:
        self.auth, self.timeout = auth, timeout
        self._streams: set[ACPChatStream] = set()
        self._setups: set[asyncio.Task[httpx2.Response]] = set()
        self._closed = False
        self._closing: asyncio.Task[None] | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self._closed:
            return httpx2.Response(
                503, json={"error": {"message": "Gemini CLI transport is closed."}}
            )
        setup = asyncio.create_task(self._open_response(request))
        self._setups.add(setup)
        setup.add_done_callback(self._setups.discard)
        return await setup

    async def _open_response(self, request: httpx2.Request) -> httpx2.Response:
        stack = AsyncExitStack()
        try:
            if not self.auth.is_connected():
                return httpx2.Response(
                    401,
                    json={
                        "error": {
                            "message": "Connect Gemini CLI in the FCC Admin UI first."
                        }
                    },
                )
            body = json.loads(request.content)
            prompt = conversation_prompt(body)
            choice = body.get("tool_choice", "auto")
            if choice not in ("auto", "none"):
                return httpx2.Response(
                    400,
                    json={
                        "error": {
                            "message": "Gemini CLI bridge supports automatic or disabled tool selection only."
                        }
                    },
                )
            tools = body.get("tools", []) if choice != "none" else []
            bridge = ToolBridge(tools)
            servers = (
                [await stack.enter_async_context(bridge.server())] if tools else []
            )
            native_connection = self.auth.connection(timeout=self.timeout)
            native_connection.mcp_servers = servers
            native_connection.allowed_mcp_tools = frozenset(bridge.tools)
            connection = await stack.enter_async_context(native_connection)
            if tools and not connection.initialized.get("agentCapabilities", {}).get(
                "mcpCapabilities", {}
            ).get("http"):
                raise GeminiCLIError(
                    "Update Gemini CLI: this ACP version does not support HTTP MCP tools."
                )
            session = await connection.new_session(servers)
            session_id = session["sessionId"]
            await connection.call(
                "session/set_model", {"sessionId": session_id, "modelId": body["model"]}
            )
            # Only this request's FCC MCP tools can receive one-time approval.
            # The native agent cannot approve filesystem or terminal operations.
            stream = ACPChatStream(
                stack,
                connection,
                bridge,
                session_id,
                body,
                prompt,
                self._streams.discard,
            )
            self._streams.add(stream)
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        except BaseException as exc:
            await stack.aclose()
            if isinstance(exc, GeminiCLIError):
                return httpx2.Response(503, json={"error": {"message": str(exc)}})
            raise

    async def aclose(self) -> None:
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        pending = tuple(self._setups)
        for setup in pending:
            if not setup.cancelling():
                setup.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(stream.aclose() for stream in tuple(self._streams)))
