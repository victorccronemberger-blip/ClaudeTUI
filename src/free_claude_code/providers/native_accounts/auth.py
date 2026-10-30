"""Credential-free connection state; official clients own every account token."""

import asyncio
import json
import os
import subprocess
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from free_claude_code.application.connected_accounts import (
    ConnectedAccountLoginMode,
    ConnectedAccountState,
    ConnectedAccountStatus,
)
from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.config.paths import config_dir_path, launcher_temp_dir_path

from .environment import native_environment


class NativeAccountError(RuntimeError):
    """Only controlled, credential-free messages cross this boundary."""


class NativeLoginRequired(NativeAccountError):
    """No native client session exists; the user must sign in."""


class NativeEligibilityError(NativeAccountError):
    """Sign-in succeeded, but the provider has not granted model access."""


class NativeRequestError(NativeAccountError):
    """The public request uses a feature absent from the native client."""


class NativeEngine(Protocol):
    provider_id: str
    display_name: str

    def login_command(self) -> list[str]: ...
    async def discover(self) -> tuple[ProviderModelInfo, ...]: ...
    async def validate_account(self) -> None: ...


class NativeAccountAuthManager:
    def __init__(self, engine: NativeEngine, *, state_path: Path | None = None) -> None:
        self.engine = engine
        self.provider_id = engine.provider_id
        self._path = (
            state_path or config_dir_path() / f"auth/{self.provider_id}-connection.json"
        )
        self._models: tuple[str, ...] = ()
        self._revision = 0
        self._task: asyncio.Task[None] | None = None
        self._process: subprocess.Popen | None = None
        self._attempt: str | None = None
        self._error: str | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        try:
            data = json.loads(self._path.read_text("utf-8"))
            models = data.get("models")
            if (
                data.get("version") == 1
                and isinstance(models, list)
                and models
                and all(isinstance(x, str) and x for x in models)
            ):
                self._models = tuple(models)
                self._revision = 1
        except OSError, ValueError, AttributeError:
            pass

    @property
    def model_ids(self) -> tuple[str, ...]:
        return self._models

    def is_connected(self) -> bool:
        return bool(self._models)

    def connected_provider_ids(self) -> tuple[str, ...]:
        return (self.provider_id,) if self.is_connected() else ()

    def status(self) -> ConnectedAccountStatus:
        pending = self._task is not None and not self._task.done()
        state = (
            ConnectedAccountState.CONNECTING
            if pending
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
            attempt_id=self._attempt if pending else None,
            model_count=len(self._models),
            display_identity=self.engine.display_name if self.is_connected() else None,
            supported_login_modes=(ConnectedAccountLoginMode.BROWSER,),
            default_login_mode=ConnectedAccountLoginMode.BROWSER,
            message=self._error
            or ("Complete sign-in in the official client window." if pending else None),
        )

    async def start_login(
        self, mode: ConnectedAccountLoginMode
    ) -> ConnectedAccountStatus:
        async with self._lock:
            if self._closed:
                raise NativeAccountError("The account service is closed.")
            if mode is not ConnectedAccountLoginMode.BROWSER:
                raise NativeAccountError("This official client uses browser sign-in.")
            if self._task is None or self._task.done():
                self._error = None
                self._attempt = uuid.uuid4().hex
                self._task = asyncio.create_task(self._login())
            return self.status()

    async def _login(self) -> None:
        try:
            try:
                models = await self.engine.discover()
            except NativeLoginRequired:
                if os.name != "nt":
                    raise NativeAccountError(
                        "Sign in with the official client in a terminal, then reconnect here."
                    ) from None
                folder = launcher_temp_dir_path() / f"{self.provider_id}-login"
                folder.mkdir(parents=True, exist_ok=True)
                self._process = subprocess.Popen(
                    self.engine.login_command(),
                    cwd=folder,
                    env=native_environment(),
                    close_fds=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                deadline = asyncio.get_running_loop().time() + 15 * 60
                while True:
                    await asyncio.sleep(3)
                    try:
                        models = await self.engine.discover()
                        break
                    except NativeLoginRequired as error:
                        if (
                            self._process.poll() is not None
                            or asyncio.get_running_loop().time() >= deadline
                        ):
                            raise error from None
            if not models:
                raise NativeAccountError(
                    "The official client returned no available models."
                )
            await self.engine.validate_account()
            model_ids = tuple(dict.fromkeys(info.model_id for info in models))
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(
                self._path.name + "." + uuid.uuid4().hex + ".tmp"
            )
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({"version": 1, "models": model_ids}, output)
                temporary.replace(self._path)
            finally:
                temporary.unlink(missing_ok=True)
            self._models = model_ids
            self._revision += 1
        except NativeAccountError as error:
            self._error = str(error)
            self._models = ()
            self._revision += 1
            with suppress(OSError):
                self._path.unlink(missing_ok=True)
        except Exception:
            self._error = "The official account client could not complete sign-in."

    async def cancel_login(self) -> ConnectedAccountStatus:
        async with self._lock:
            if self._task is not None:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
                self._task = None
            # The visible native client remains user-owned; cancellation here
            # never logs it out or deletes its credential/profile directories.
            self._attempt = None
            return self.status()

    async def disconnect(self) -> ConnectedAccountStatus:
        await self.cancel_login()
        async with self._lock:
            self._path.unlink(missing_ok=True)
            self._models = ()
            self._error = None
            self._revision += 1
            return self.status()

    async def close(self) -> None:
        self._closed = True
        await self.cancel_login()
