"""Connection state for native Gemini CLI OAuth, without reading Google tokens."""

import asyncio
import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from free_claude_code.application.connected_accounts import (
    ConnectedAccountLoginMode,
    ConnectedAccountState,
    ConnectedAccountStatus,
)
from free_claude_code.config.paths import config_dir_path

from .acp import ACPConnection, GeminiCLIError, cli_command

CONSUMER_SIGNIN_NOTICE = (
    "Google ended Gemini CLI access for individual, Google AI Pro and Ultra accounts "
    "on June 18, 2026. Those subscriptions now use Antigravity. "
    "See https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals"
)


class GeminiCLIAuthManager:
    """Let the official CLI own browser login, token storage and token renewal."""

    provider_id = "gemini_cli"

    def __init__(
        self, *, executable: str | None = None, state_path: Path | None = None
    ) -> None:
        self.executable = executable
        self._path = state_path or config_dir_path() / "auth/gemini-cli-connection.json"
        self._models: tuple[str, ...] = ()
        self._revision = 0
        self._task: asyncio.Task[None] | None = None
        self._attempt: str | None = None
        self._error: str | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if payload.get("version") == 1:
                self._models = self._parse_models(payload.get("models"))
                self._revision = 1
        except FileNotFoundError:
            pass
        except OSError, ValueError, AttributeError, GeminiCLIError:
            self._error = (
                "Saved Gemini CLI connection is invalid. Disconnect and reconnect it."
            )

    @staticmethod
    def _parse_models(value: Any) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(x, str) or not x.strip() for x in value)
        ):
            raise GeminiCLIError(
                "Gemini CLI did not return an available model list. Update the CLI and reconnect."
            )
        return tuple(dict.fromkeys(value))

    def connection(self, *, timeout: float = 120) -> ACPConnection:
        return ACPConnection(cli_command(self.executable), timeout=timeout)

    def is_connected(self) -> bool:
        return bool(self._models)

    def connected_provider_ids(self) -> tuple[str, ...]:
        return (self.provider_id,) if self.is_connected() else ()

    @property
    def model_ids(self) -> tuple[str, ...]:
        return self._models

    def status(self) -> ConnectedAccountStatus:
        connecting = self._task is not None and not self._task.done()
        state = (
            ConnectedAccountState.CONNECTING
            if connecting
            else ConnectedAccountState.ERROR
            if self._error
            else ConnectedAccountState.CONNECTED
            if self.is_connected()
            else ConnectedAccountState.DISCONNECTED
        )
        return ConnectedAccountStatus(
            provider_id=self.provider_id,
            state=state,
            connected=self.is_connected(),
            revision=self._revision,
            attempt_id=self._attempt if connecting else None,
            mode=ConnectedAccountLoginMode.BROWSER if connecting else None,
            model_count=len(self._models) if self._models else None,
            display_identity="Native Gemini CLI" if self._models else None,
            supported_login_modes=(ConnectedAccountLoginMode.BROWSER,),
            default_login_mode=ConnectedAccountLoginMode.BROWSER,
            message=self._error
            or (
                CONSUMER_SIGNIN_NOTICE
                if state is ConnectedAccountState.DISCONNECTED
                else None
            ),
        )

    async def start_login(
        self, mode: ConnectedAccountLoginMode
    ) -> ConnectedAccountStatus:
        async with self._lock:
            if self._closed:
                raise GeminiCLIError("Gemini CLI authentication is closed.")
            if mode != ConnectedAccountLoginMode.BROWSER:
                raise GeminiCLIError("Gemini CLI supports browser sign-in only.")
            if self._task is not None and not self._task.done():
                return self.status()
            self._error = None
            self._attempt = uuid.uuid4().hex
            self._task = asyncio.create_task(self._login())
            return self.status()

    async def _login(self) -> None:
        try:
            async with self.connection(timeout=15 * 60) as connection:
                if not any(
                    x.get("id") == "oauth-personal"
                    for x in connection.initialized.get("authMethods", [])
                ):
                    raise GeminiCLIError(
                        "This Gemini CLI version does not support Google sign-in over ACP."
                    )
                await connection.call("authenticate", {"methodId": "oauth-personal"})
                session = await connection.new_session()
                models = self._parse_models(
                    [
                        item.get("modelId")
                        for item in session.get("models", {}).get("availableModels", [])
                    ]
                )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(
                self._path.name + "." + uuid.uuid4().hex + ".tmp"
            )
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"version": 1, "models": models}, handle)
                temporary.replace(self._path)
            finally:
                temporary.unlink(missing_ok=True)
            self._models = models
            self._revision += 1
        except GeminiCLIError as exc:
            self._error = str(exc)
        except Exception:
            self._error = (
                "Gemini CLI sign-in failed. Check the CLI installation and try again."
            )

    async def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._attempt = None

    async def cancel_login(self) -> ConnectedAccountStatus:
        async with self._lock:
            await self._cancel()
            return self.status()

    async def disconnect(self) -> ConnectedAccountStatus:
        async with self._lock:
            await self._cancel()
            # Remove FCC's connection only. The official CLI owns its Google session.
            self._path.unlink(missing_ok=True)
            self._models = ()
            self._error = None
            self._revision += 1
            return self.status()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._cancel()
