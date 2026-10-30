"""Expose native account turns through the existing OpenAI Chat conversions."""

import asyncio
import json
import uuid
from typing import Any

import httpx2

from free_claude_code.providers.gemini_cli.transport import ToolBridge

from .auth import NativeAccountAuthManager, NativeAccountError, NativeRequestError
from .google import GoogleAccountEngine


class NativeChatStream(httpx2.AsyncByteStream):
    def __init__(self, body, session, bridge, on_closed):
        self.body, self.session, self.bridge, self.on_closed = (
            body,
            session,
            bridge,
            on_closed,
        )
        self.identifier = "chatcmpl_" + uuid.uuid4().hex
        self._tasks: list[asyncio.Task] = []
        self._closing = None
        self._iterator = None

    def frame(
        self, delta: dict[str, Any], finish: str | None = None, usage=None
    ) -> bytes:
        payload = {
            "id": self.identifier,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": self.body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            payload["usage"] = usage
        return ("data: " + json.dumps(payload) + "\n\n").encode()

    def __aiter__(self):
        # The SDK may re-enter the transport iterator to drain after [DONE].
        # A response owns exactly one native turn and one stdout reader.
        if self._iterator is None:
            self._iterator = self._iterate()
        return self._iterator

    async def _iterate(self):
        events = self.session.events().__aiter__()
        event = asyncio.create_task(anext(events, None))
        call = asyncio.create_task(self.bridge.calls.get())
        self._tasks = [event, call]
        try:
            yield self.frame({"role": "assistant"})
            while True:
                done, _ = await asyncio.wait(
                    [event, call], return_when=asyncio.FIRST_COMPLETED
                )
                if call in done:
                    value = call.result()
                    yield self.frame(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_" + uuid.uuid4().hex,
                                    "type": "function",
                                    "function": {
                                        "name": value["name"],
                                        "arguments": json.dumps(value["arguments"]),
                                    },
                                }
                            ]
                        }
                    )
                    yield self.frame({}, "tool_calls")
                    break
                item = event.result()
                if item is None:
                    yield self.frame({}, "stop")
                    break
                if "text" in item:
                    yield self.frame({"content": item["text"]})
                if "usage" in item:
                    native = item["usage"]
                    usage = {
                        "prompt_tokens": native.get("input_tokens", 0),
                        "completion_tokens": native.get("output_tokens", 0),
                        "total_tokens": native.get("total_tokens", 0),
                    }
                    yield self.frame({}, "stop", usage)
                    break
                event = asyncio.create_task(anext(events, None))
                self._tasks[0] = event
            yield b"data: [DONE]\n\n"
        finally:
            await self.aclose()

    async def aclose(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.session.close()
        finally:
            self.on_closed(self)


class NativeChatTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        auth: NativeAccountAuthManager,
        engine: GoogleAccountEngine,
        timeout: float,
    ):
        self.auth, self.engine, self.timeout = auth, engine, timeout
        self._streams: set[NativeChatStream] = set()
        self._setups: set[asyncio.Task] = set()
        self._closed = False
        self._closing = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self._closed:
            return httpx2.Response(
                503,
                json={"error": {"message": "The native account transport is closed."}},
            )
        task = asyncio.create_task(self._open_response(request))
        self._setups.add(task)
        task.add_done_callback(self._setups.discard)
        return await task

    async def _open_response(self, request: httpx2.Request) -> httpx2.Response:
        if not self.auth.is_connected():
            return httpx2.Response(
                401,
                json={
                    "error": {
                        "message": "Connect your Google account in ClaudeTUI first."
                    }
                },
            )
        session = None
        try:
            body = json.loads(request.content)
            if body.get("tool_choice", "auto") not in ("auto", "none"):
                return httpx2.Response(
                    400,
                    json={
                        "error": {
                            "message": "The native account bridge supports auto or none tool selection."
                        }
                    },
                )
            bridge = ToolBridge(
                body.get("tools", []) if body.get("tool_choice") != "none" else []
            )
            session = self.engine.session(body, bridge, self.timeout)
            await session.__aenter__()
            stream = NativeChatStream(body, session, bridge, self._streams.discard)
            self._streams.add(stream)
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        except NativeAccountError as error:
            if session:
                await session.close()
            return httpx2.Response(
                400 if isinstance(error, NativeRequestError) else 503,
                json={"error": {"message": str(error)}},
            )
        except BaseException:
            if session:
                await session.close()
            raise

    async def aclose(self):
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        for task in tuple(self._setups):
            task.cancel()
        await asyncio.gather(*tuple(self._setups), return_exceptions=True)
        await asyncio.gather(*(stream.aclose() for stream in tuple(self._streams)))
