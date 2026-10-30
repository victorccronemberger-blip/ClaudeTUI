"""Service discovery and terminal launch using installed sibling executables."""

import json
import subprocess
import time
from urllib.request import Request

from free_claude_code.cli.local_http import open_local_request
from free_claude_code.config.loader import clear_settings_cache, get_settings
from free_claude_code.config.server_urls import local_proxy_root_url

from .runtime import application_dir


def read_api(path: str) -> dict:
    # Applying configuration can move the service to a new port. A GUI process
    # must not keep probing the settings cached at its first launch.
    clear_settings_cache()
    request = Request(local_proxy_root_url(get_settings()) + path)
    with open_local_request(request, timeout=3) as response:
        return json.load(response)


def service_status() -> dict | None:
    try:
        status = read_api("/admin/api/status")
        if (
            isinstance(status, dict)
            and status.get("status") == "running"
            and isinstance(status.get("instance_id"), str)
            and len(status["instance_id"]) == 32
            and isinstance(status.get("provider_status"), list)
        ):
            return status
    except OSError, ValueError:
        pass
    return None


def provider_ready(status: dict, account: dict | None = None) -> bool:
    """A cached model list does not imply that credentials have been configured."""
    provider = status.get("provider")
    entry = next(
        (
            row
            for row in status.get("provider_status", [])
            if row.get("provider_id") == provider
        ),
        None,
    )
    if entry is None:
        return False
    discovery = status.get("startup", {}).get("providers", {}).get(provider)
    if discovery is not None and discovery != "ready":
        return False
    if entry.get("kind") == "connected_account":
        return bool(
            account and account.get("connected") and account.get("model_count", 0) > 0
        )
    return entry.get("status") == "configured"


def provider_problem(status: dict, account: dict | None = None) -> str | None:
    """Use credential-free status diagnostics instead of claiming readiness."""
    provider = status.get("provider")
    row = next(
        (
            r
            for r in status.get("provider_status", [])
            if r.get("provider_id") == provider
        ),
        {},
    )
    if row.get("kind") != "connected_account" and row.get("status") != "configured":
        return None
    error = status.get("startup", {}).get("provider_errors", {}).get(provider)
    if error:
        return str(error)
    if account and account.get("state") == "error":
        return account.get("message") or "A conexão com o provedor falhou."
    if account and account.get("connected") and not account.get("model_count"):
        return "A conta conectou, mas os modelos ainda não carregaram. Atualize os modelos no painel."
    return None


def ensure_service() -> None:
    if service_status() is not None:
        return
    subprocess.Popen(
        [str(application_dir() / "ClaudeTUI-Service.exe")],
        cwd=application_dir(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if service_status() is not None:
            return
        time.sleep(0.5)
    raise RuntimeError(
        "O serviço não iniciou. Use Tentar novamente ou consulte os logs no painel."
    )


def launch_terminal() -> None:
    from contextlib import ExitStack

    from free_claude_code.cli.launchers.claude import _configure
    from free_claude_code.cli.launchers.common import run_client_process
    from free_claude_code.cli.launchers.runner import LaunchContext
    from free_claude_code.harnesses.resources import LaunchResources

    from .runtime import configure_environment, tool_path, tools_ready

    configure_environment()
    if not tools_ready():
        raise RuntimeError("Abra o ClaudeTUI e aguarde a preparação das ferramentas.")
    ensure_service()
    settings = get_settings()
    import os

    context = LaunchContext(
        str(tool_path("claude")),
        settings,
        local_proxy_root_url(settings),
        settings.proxy_auth_token,
        dict(os.environ),
        None,
    )
    with ExitStack() as stack:
        prepared = _configure(context, [], LaunchResources(stack))
        run_client_process(
            command=prepared.command,
            env=prepared.env,
            binary_name="claude",
            display_name="Claude Code",
            install_hint="Abra o ClaudeTUI para reparar as ferramentas.",
        )
