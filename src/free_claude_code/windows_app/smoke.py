"""Offline acceptance checks executed inside the frozen distribution."""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .runtime import configure_environment, diagnostics, resources_dir, tool_path


async def check_api() -> dict:
    import httpx

    from free_claude_code.config.settings import Settings
    from free_claude_code.runtime.bootstrap import build_asgi_app

    app = build_asgi_app(Settings())
    await app.runtime.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            status = await client.get("/admin/api/status")
            admin = await client.get("/admin")
            assert status.status_code == 200, status.status_code
            assert admin.status_code == 200, admin.status_code
            assert "<html" in admin.text.lower()
            return {"status": status.json()["status"], "admin_html": True}
    finally:
        await app.runtime.close()


def run() -> None:
    """No provider request or listener; production ASGI lifespan runs in process."""
    configure_environment()
    result = diagnostics()
    node = resources_dir() / "runtime/node/node.exe"
    gemini = (
        resources_dir()
        / "runtime/gemini/node_modules/@google/gemini-cli/bundle/gemini.js"
    )
    commands = {
        "node": [str(node), "--version"],
        "gemini": [str(node), str(gemini), "--version"],
    }
    if tool_path("claude").is_file():
        commands["claude"] = [str(tool_path("claude")), "--version"]
    if tool_path("antigravity").is_file():
        commands["antigravity"] = [str(tool_path("antigravity")), "--version"]
    if tool_path("git").is_file():
        commands["bash"] = [str(tool_path("git")), "--version"]
        commands["git_in_bash"] = [str(tool_path("git")), "-c", "git --version"]
    for name, command in commands.items():
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert process.returncode == 0, (
            f"{name}: exit {process.returncode}: {process.stderr}"
        )
        result[f"{name}_version"] = process.stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix="ClaudeTUI QA ") as folder:
        # Path.home on Windows uses USERPROFILE. Do not touch real provider state.
        original = dict(os.environ)
        try:
            os.environ["USERPROFILE"] = folder
            os.environ["HOME"] = folder
            os.environ["LOG_FILE"] = str(Path(folder) / "server.log")
            result["api"] = asyncio.run(check_api())
        finally:
            from loguru import logger

            logger.remove()
            os.environ.clear()
            os.environ.update(original)
    print(json.dumps(result))
