"""Stdio client for the official Gemini CLI; OAuth remains owned by the CLI."""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from free_claude_code.config.paths import launcher_temp_dir_path


class GeminiCLIError(RuntimeError):
    """A native CLI operation failed without exposing its credential output."""


def _rpc_error(error: object) -> GeminiCLIError:
    """Expose only recognized diagnostics, never arbitrary native OAuth output."""
    message = error.get("message", "") if isinstance(error, dict) else ""
    if isinstance(message, str):
        if (
            "This client is no longer supported for Gemini Code Assist for individuals"
            in message
        ):
            return GeminiCLIError(
                "Google retired Gemini CLI sign-in for individual, Google AI Pro, "
                "and Google AI Ultra accounts on June 18, 2026. "
                "Those accounts must use Google's supported Antigravity products. "
                "Gemini Code Assist organization subscriptions have separate eligibility. "
                "See https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals"
            )
        if message == "Gemini API key is missing or not configured.":
            return GeminiCLIError(
                "Gemini CLI has no selected Google account for this session. "
                "Reconnect an eligible Gemini Code Assist organization account. "
                "This connection does not use a Gemini API key."
            )
    return GeminiCLIError(
        "Gemini CLI rejected the operation. Check the native account login and model access."
    )


def cli_command(configured: str | None = None) -> list[str]:
    executable = configured or shutil.which("gemini")
    if not executable:
        raise GeminiCLIError(
            "Install Gemini CLI with npm install -g @google/gemini-cli, then connect it in FCC."
        )
    path = Path(executable).expanduser().resolve()
    if path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
        # Resolve npm's JS entry point instead of passing arguments through cmd.exe.
        package_root = path.parent / "node_modules/@google/gemini-cli"
        try:
            package = json.loads(
                (package_root / "package.json").read_text(encoding="utf-8")
            )
            binary = package["bin"]
            entry = binary["gemini"] if isinstance(binary, dict) else binary
            path = (package_root / entry).resolve()
            if not path.is_relative_to(package_root.resolve()):
                raise ValueError("Invalid native CLI entry point")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise GeminiCLIError(
                "Gemini CLI package metadata is invalid. Reinstall the CLI or set GEMINI_CLI_PATH to its JS entry point."
            ) from exc
    if not path.is_file():
        raise GeminiCLIError(
            "Gemini CLI entry point was not found. Check GEMINI_CLI_PATH."
        )
    if path.suffix.lower() == ".js":
        node = shutil.which("node")
        if not node:
            raise GeminiCLIError("Node.js is required to run Gemini CLI.")
        return [node, str(path)]
    return [str(path)]


class ACPConnection:
    """One isolated native agent process, with no built-in or user MCP tools."""

    def __init__(self, command: list[str], *, timeout: float = 120) -> None:
        self.command = command
        self.timeout = timeout
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._counter = 0
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._closing: asyncio.Task[None] | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._root: Path | None = None
        self.cwd = ""
        self.initialized: dict[str, Any] = {}
        self.allowed_mcp_tools: frozenset[str] = frozenset()
        self.mcp_servers: list[dict[str, Any]] = []

    async def __aenter__(self) -> ACPConnection:
        self._root = launcher_temp_dir_path().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="gemini-acp-", dir=self._root, ignore_cleanup_errors=True
        )
        directory = Path(self._temporary.name).resolve()
        assert directory.is_relative_to(self._root)
        self.cwd = str(directory)
        (directory / ".gemini").mkdir()
        settings = directory / ".gemini/settings.json"
        settings.write_text(
            json.dumps(
                {
                    # Gemini applies core's allowlist to MCP tools too. Explicit
                    # native-qualified caller names keep MCP available while
                    # excluding every built-in tool.
                    "tools": {
                        "core": [
                            f"mcp_fcc_{name}" for name in sorted(self.allowed_mcp_tools)
                        ],
                        "discoveryCommand": "",
                        "callCommand": "",
                    },
                    "mcp": {"allowed": ["fcc"]},
                    "mcpServers": {
                        server["name"]: {
                            "httpUrl": server["url"],
                            "headers": {
                                header["name"]: header["value"]
                                for header in server["headers"]
                            },
                        }
                        for server in self.mcp_servers
                    },
                    "hooksConfig": {"enabled": False},
                    "skills": {"enabled": False},
                    "context": {"fileName": []},
                    "security": {"auth": {"selectedType": "oauth-personal"}},
                }
            ),
            encoding="utf-8",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "GOOGLE_GENAI_USE_VERTEXAI",
                "GOOGLE_GEMINI_BASE_URL",
                "GEMINI_API_BASE_URL",
                "GOOGLE_APPLICATION_CREDENTIALS",
            }
        }
        env["GEMINI_TELEMETRY_ENABLED"] = "false"
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                "--acp",
                "--skip-trust",
                "--approval-mode",
                "default",
                "--extensions",
                "none",
                "--allowed-mcp-server-names",
                "fcc",
                cwd=self.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=8 * 1024 * 1024,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            self._tasks = [
                asyncio.create_task(self._read()),
                asyncio.create_task(self._drain_stderr()),
            ]
            self.initialized = await self.call(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "free-claude-code", "version": "1"},
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                },
            )
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise GeminiCLIError("Gemini CLI is not running.")
        self._counter += 1
        request_id = self._counter
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            process.stdin.write(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )
                    + "\n"
                ).encode()
            )
            await process.stdin.drain()
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError as exc:
            raise GeminiCLIError(
                "Gemini CLI timed out. Check the native login or retry the request."
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while raw := await self._process.stdout.readline():
                packet = json.loads(raw)
                if packet.get("method") == "session/update":
                    await self.events.put(packet["params"]["update"])
                elif "method" in packet and "id" in packet:
                    # Built-in tools are disabled; never grant an unexpected native
                    # filesystem/terminal/permission request from the agent.
                    assert self._process.stdin is not None
                    response = {"jsonrpc": "2.0", "id": packet["id"]}
                    if packet["method"] == "session/request_permission":
                        params = packet.get("params", {})
                        mcp = (
                            params.get("toolCall", {})
                            .get("_meta", {})
                            .get("gemini-cli", {})
                            .get("mcp", {})
                        )
                        option = next(
                            (
                                item
                                for item in params.get("options", [])
                                if item.get("kind") == "allow_once"
                            ),
                            None,
                        )
                        if (
                            mcp.get("server_name") == "fcc"
                            and mcp.get("tool_name") in self.allowed_mcp_tools
                            and option is not None
                        ):
                            response["result"] = {
                                "outcome": {
                                    "outcome": "selected",
                                    "optionId": option["optionId"],
                                }
                            }
                        else:
                            response["result"] = {"outcome": {"outcome": "cancelled"}}
                    else:
                        response["error"] = {
                            "code": -32601,
                            "message": "Unsupported client operation",
                        }
                    self._process.stdin.write((json.dumps(response) + "\n").encode())
                    await self._process.stdin.drain()
                elif (
                    future := self._pending.get(packet.get("id"))
                ) is not None and not future.done():
                    if "error" in packet:
                        future.set_exception(_rpc_error(packet["error"]))
                    else:
                        future.set_result(packet.get("result", {}))
        except ValueError, OSError:
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        GeminiCLIError("Gemini CLI closed the ACP connection.")
                    )

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        # Native OAuth and SDK diagnostics can contain sensitive data; do not log them.
        while await self._process.stderr.read(8192):
            pass

    async def new_session(
        self, servers: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return await self.call(
            "session/new", {"cwd": self.cwd, "mcpServers": servers or []}
        )

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await killer.wait()
                else:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await process.wait()
        if process is not None and process.stdin is not None:
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=2)
            for task in pending:
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if process is not None:
            # communicate closes read-pipe transports, too. Waiting for the PID
            # alone can leave Windows pipe handles open after native wrappers.
            with suppress(TimeoutError, BrokenPipeError, ConnectionResetError):
                await asyncio.wait_for(process.communicate(), 2)
        self._process = None
        if self._temporary is not None:
            assert self._root is not None and Path(
                self._temporary.name
            ).resolve().is_relative_to(self._root)
            self._temporary.cleanup()
            self._temporary = None
