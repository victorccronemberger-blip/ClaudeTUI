"""Google account access through the official Antigravity CLI and keyring."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.config.paths import launcher_temp_dir_path
from free_claude_code.core.model_capabilities import ModelInputModality
from free_claude_code.providers.gemini_cli.transport import (
    ToolBridge,
    conversation_prompt,
)

from .auth import (
    NativeAccountError,
    NativeEligibilityError,
    NativeLoginRequired,
    NativeRequestError,
)
from .environment import native_environment


def google_command(configured: str | None = None) -> list[str]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [local / "agy/bin/agy.exe", Path(shutil.which("agy") or "__missing_agy__")]
    )
    for path in candidates:
        if path.is_file():
            return [str(path.resolve())]
    raise NativeAccountError(
        "Install the official Antigravity CLI, or set ANTIGRAVITY_CLI_PATH to agy.exe."
    )


def native_error(message: object) -> NativeAccountError:
    # Never publish native stderr or errors containing account/verification URLs.
    text = str(message).lower()
    if "not eligible" in text or "verify your account" in text:
        return NativeEligibilityError(
            "Google sign-in succeeded, but Google requires account verification before generation. "
            "Open the official Antigravity client and complete its verification in your browser."
        )
    if any(
        x in text
        for x in ("please sign in", "not signed in", "authentication required")
    ):
        return NativeLoginRequired("Sign in with your Google account in Antigravity.")
    if any(x in text for x in ("quota", "rate limit", "resource_exhausted")):
        return NativeAccountError(
            "Google account quota is exhausted. Check Antigravity usage and try again later."
        )
    return NativeAccountError(
        "Antigravity could not complete the turn. Check the selected model and account in the official client."
    )


def text_model(model_id: str) -> ProviderModelInfo:
    # The CLI's documented streaming input accepts text blocks only.
    return ProviderModelInfo(
        model_id=model_id, input_modalities=frozenset({ModelInputModality.TEXT})
    )


def session_settings(tool_names: list[str]) -> dict[str, Any]:
    return {
        "modelProvider": "",
        "useG1Credits": False,
        "disableSlashCommands": True,
        "statusLine": {"enabled": False},
        "title": {"enabled": False},
        "permissions": {
            "allow": [f"mcp(fcc/{name})" for name in tool_names],
            "ask": [],
            "deny": [
                "read_file(*)",
                "write_file(*)",
                "command(*)",
                "read_url(*)",
                "execute_url(*)",
            ],
        },
    }


class GoogleAccountEngine:
    provider_id = "google_account"
    display_name = "Google / Antigravity"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable
        self.models: tuple[ProviderModelInfo, ...] = ()

    def login_command(self) -> list[str]:
        return google_command(self.executable)

    async def discover(self) -> tuple[ProviderModelInfo, ...]:
        process = await asyncio.create_subprocess_exec(
            *self.login_command(),
            "models",
            env=native_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
            if process.returncode:
                raise native_error((stdout + stderr).decode("utf-8", "replace"))
            models = tuple(
                dict.fromkeys(
                    line.split("\t", 1)[0].strip()
                    for line in stdout.decode("utf-8", "replace").splitlines()
                    if "\t" in line
                    and re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._/-]*", line.split("\t", 1)[0].strip()
                    )
                )
            )
            if not models:
                raise NativeAccountError(
                    "Antigravity returned an empty or invalid model catalog."
                )
            self.models = tuple(text_model(name) for name in models)
            return self.models
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    def session(self, body: dict[str, Any], bridge: ToolBridge, timeout: float):
        return GoogleSession(self.login_command(), body, bridge, timeout)

    async def validate_account(self) -> None:
        models = self.models or await self.discover()
        model = next(
            (x.model_id for x in models if "flash-low" in x.model_id),
            models[0].model_id,
        )
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply only OK."}],
        }
        async with self.session(body, ToolBridge([]), 60) as session:
            text = ""
            async for event in session.events():
                text += event.get("text", "")
            if not text.strip():
                raise NativeAccountError(
                    "Google account validation returned no answer."
                )


class GoogleSession:
    def __init__(
        self,
        command: list[str],
        body: dict[str, Any],
        bridge: ToolBridge,
        timeout: float,
    ):
        self.command, self.body, self.bridge, self.timeout = (
            command,
            body,
            bridge,
            timeout,
        )
        self.process: asyncio.subprocess.Process | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._stderr: asyncio.Task | None = None
        self._server = None
        self._closing: asyncio.Task | None = None

    async def __aenter__(self):
        for message in self.body.get("messages", []):
            content = message.get("content")
            if isinstance(content, list) and any(
                block.get("type") != "text" for block in content
            ):
                raise NativeRequestError(
                    "Antigravity CLI input currently supports text only. Select another provider for images."
                )
        prompt = conversation_prompt(self.body)
        if len(prompt) != 1:
            raise NativeRequestError(
                "Antigravity CLI input currently supports text only. Select another provider for images."
            )
        root = launcher_temp_dir_path()
        root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="google-account-", dir=root, ignore_cleanup_errors=True
        )
        cwd = Path(self._temporary.name)
        agents = cwd / ".agents/agents"
        agents.mkdir(parents=True)
        (agents / "fcc.md").write_text(
            "---\nname: fcc\ndescription: Continue the caller conversation with caller-owned MCP tools.\n"
            "tools: []\nmainAgent: true\nsubagent: false\ncommandExecutionPolicy: off\n---\n"
            "Continue the supplied conversation. Use only the fcc MCP tools for actions. "
            "Do not use built-in tools, subagents, browser, filesystem, or commands.\n",
            encoding="utf-8",
        )
        try:
            servers: dict[str, Any] = {}
            # Shadow global server entries so this private workspace cannot start
            # unrelated user MCP processes or reuse their access tokens.
            global_config = Path.home() / ".gemini/config/mcp_config.json"
            if global_config.is_file():
                try:
                    source = global_config.read_text("utf-8-sig").strip()
                    global_servers = json.loads(source or "{}").get("mcpServers", {})
                    servers.update(
                        {name: {"disabled": True} for name in global_servers}
                    )
                except (ValueError, AttributeError, TypeError) as exc:
                    raise NativeAccountError(
                        "The native global MCP configuration is invalid."
                    ) from exc
            if self.bridge.tools:
                self._server = self.bridge.server()
                server = await self._server.__aenter__()
                servers["fcc"] = {
                    "serverUrl": server["url"],
                    "headers": {h["name"]: h["value"] for h in server["headers"]},
                    "enabledTools": list(self.bridge.tools),
                }
            (cwd / ".agents/mcp_config.json").write_text(
                json.dumps({"mcpServers": servers}), encoding="utf-8"
            )
            settings = cwd / "session-settings.json"
            settings.write_text(
                json.dumps(session_settings(list(self.bridge.tools))), encoding="utf-8"
            )
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                "--agent",
                "fcc",
                "--model",
                self.body["model"],
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--disable-slash-commands",
                "--settings",
                str(settings),
                "--print-timeout",
                f"{max(1, int(self.timeout))}s",
                cwd=cwd,
                env=native_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=8 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            async def drain_stderr():
                assert self.process and self.process.stderr
                while await self.process.stderr.read(65536):
                    pass

            self._stderr = asyncio.create_task(drain_stderr())
            assert self.process.stdin
            self.process.stdin.write(
                (
                    json.dumps(
                        {"event": "user", "message": {"content": prompt[0]["text"]}},
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
            )
            await self.process.stdin.drain()
            self.process.stdin.close()
            return self
        except BaseException:
            await self.close()
            raise

    async def events(self):
        assert self.process and self.process.stdout
        text_seen = False
        async with asyncio.timeout(self.timeout):
            while line := await self.process.stdout.readline():
                try:
                    packet = json.loads(line)
                except ValueError:
                    continue
                if packet.get("event") == "step_update":
                    step = packet.get("step_update", {})
                    if step.get("step_type") == "agent_response" and step.get(
                        "text_delta"
                    ):
                        text_seen = True
                        yield {"text": step["text_delta"]}
                elif packet.get("event") == "result":
                    result = packet.get("result", {})
                    if result.get("status") != "SUCCESS":
                        raise native_error(result.get("error", ""))
                    if not text_seen and result.get("response"):
                        yield {"text": result["response"]}
                    yield {"usage": result.get("usage", {})}
                    return
            raise NativeAccountError(
                "Antigravity stopped before completing the requested turn."
            )

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            if self.process:
                if self.process.returncode is None:
                    with suppress(ProcessLookupError):
                        self.process.terminate()
                if self.process.stdout:
                    while await self.process.stdout.read(65536):
                        pass
                await self.process.wait()
                if self.process.stdin:
                    self.process.stdin.close()
                    with suppress(OSError):
                        await self.process.stdin.wait_closed()
            if self._stderr:
                await self._stderr
        finally:
            self.bridge.release.set()
            if self._server:
                await self._server.__aexit__(None, None, None)
            if self._temporary:
                self._temporary.cleanup()
