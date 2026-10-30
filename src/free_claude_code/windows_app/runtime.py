"""Versioned, verified, per-user native tools for the Windows application."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

from free_claude_code.core.interprocess_lock import InterprocessFileLock


def application_dir() -> Path:
    return Path(sys.executable).resolve().parent


def resources_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", application_dir()))


def user_dir() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        / "ClaudeTUI"
    )


def manifest() -> dict:
    return json.loads((resources_dir() / "runtime-lock.json").read_text("utf-8"))


def tool_path(name: str) -> Path:
    if name == "antigravity":
        # Let the user's official installation and updater stay in control.
        installed = (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "agy/bin/agy.exe"
        )
        if installed.is_file():
            return installed
    version = manifest()[name]["version"]
    root = user_dir() / "runtime" / f"{name}-{version}"
    return (
        root
        / {"claude": "claude.exe", "git": "bin/bash.exe", "antigravity": "agy.exe"}[
            name
        ]
    )


def tools_ready() -> bool:
    names = ["claude", "git"]
    if "antigravity" in manifest():
        names.append("antigravity")
    return all(tool_path(name).is_file() for name in names)


def verify_file(path: Path, spec: dict) -> None:
    algorithm = "sha512" if "sha512_base64" in spec else "sha256"
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, algorithm)
    actual = (
        base64.b64encode(digest.digest()).decode()
        if algorithm == "sha512"
        else digest.hexdigest()
    )
    expected = spec.get("sha512_base64", spec.get("sha256"))
    if actual != expected:
        raise ValueError(f"Falha na verificação do arquivo: {path.name}")


def download_verified(
    spec: dict,
    destination: Path,
    progress: Callable[[str], None] = lambda _message: None,
    cancel: threading.Event | None = None,
) -> None:
    """Only promote a completed HTTPS download whose pinned digest matches."""
    if destination.exists():
        try:
            verify_file(destination, spec)
            return
        except ValueError:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        request = Request(spec["url"], headers={"User-Agent": "ClaudeTUI/0.1"})
        with urlopen(request, timeout=60) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            size = 0
            while chunk := response.read(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Preparação cancelada.")
                output.write(chunk)
                size += len(chunk)
                suffix = f" de {total // 1048576} MB" if total else " MB"
                progress(f"Baixando {size // 1048576}{suffix}")
        verify_file(partial, spec)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def install_tools(
    progress: Callable[[str], None], cancel: threading.Event | None = None
) -> None:
    """Install into a staging directory; an interrupted upgrade keeps old versions."""
    root = user_dir() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    lock = InterprocessFileLock(root / "install.lock")
    if not lock.acquire(wait=True, timeout=5):
        raise RuntimeError(
            "Outra janela está preparando as ferramentas. Tente novamente."
        )
    try:
        names = ["claude", "git"]
        if "antigravity" in manifest():
            names.append("antigravity")
        for name in names:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Preparação cancelada.")
            if tool_path(name).is_file():
                continue
            spec = manifest()[name]
            label = {
                "claude": "Claude Code",
                "git": "Git",
                "antigravity": "Antigravity",
            }[name]
            progress(f"Preparando {label}…")
            archive = root / "downloads" / Path(spec["url"]).name
            download_verified(spec, archive, progress, cancel)
            # TemporaryDirectory owns and removes only its freshly created subtree.
            with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=root) as folder:
                staging = Path(folder) / "tool"
                staging.mkdir()
                if name == "claude":
                    with tarfile.open(archive, "r:gz") as package:
                        member = package.getmember("package/claude.exe")
                        source = package.extractfile(member)
                        if source is None or not member.isfile():
                            raise ValueError("Pacote do Claude Code inválido.")
                        with source, (staging / "claude.exe").open("wb") as output:
                            shutil.copyfileobj(source, output)
                elif name == "antigravity":
                    shutil.copy2(archive, staging / "agy.exe")
                else:
                    process = subprocess.Popen(
                        [str(archive), f"-o{staging}", "-y", "-gm2"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    try:
                        deadline = time.monotonic() + 180
                        while process.poll() is None:
                            if cancel is not None and cancel.wait(0.1):
                                raise InterruptedError("Preparação cancelada.")
                            if time.monotonic() >= deadline:
                                raise TimeoutError(
                                    "A preparação do Git excedeu o tempo limite."
                                )
                            if cancel is None:
                                time.sleep(0.1)
                        if process.returncode:
                            raise RuntimeError(
                                "Não foi possível preparar o Git. Tente novamente."
                            )
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                expected = (
                    staging
                    / {
                        "claude": "claude.exe",
                        "git": "bin/bash.exe",
                        "antigravity": "agy.exe",
                    }[name]
                )
                if not expected.is_file():
                    raise ValueError(f"Pacote {name} incompleto.")
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Preparação cancelada.")
                target = root / f"{name}-{spec['version']}"
                if target.exists():
                    # Preserve an incomplete old directory rather than requiring
                    # manual cleanup or deleting unknown user-added files.
                    target.replace(
                        root / f".{target.name}-incomplete-{uuid.uuid4().hex}"
                    )
                staging.replace(target)
        progress("Ferramentas prontas")
    finally:
        lock.release()


def configure_environment() -> None:
    """Private child environment, without changing machine or account PATH."""
    os.environ["FCC_OPEN_BROWSER"] = "false"
    os.environ["HOST"] = "127.0.0.1"
    node = resources_dir() / "runtime/node"
    gemini = (
        resources_dir()
        / "runtime/gemini/node_modules/@google/gemini-cli/bundle/gemini.js"
    )
    os.environ["GEMINI_CLI_PATH"] = str(gemini)
    os.environ.setdefault("ANTIGRAVITY_CLI_PATH", str(tool_path("antigravity")))
    git = tool_path("git").parent.parent
    os.environ["PATH"] = os.pathsep.join(
        [str(node), str(git / "cmd"), str(git / "usr/bin"), os.environ.get("PATH", "")]
    )
    os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = str(tool_path("git"))


def diagnostics() -> dict:
    from free_claude_code.api.app import create_app
    from free_claude_code.cli.desktop_assets import app_icon_bytes
    from free_claude_code.core.version import UPSTREAM_BASE_VERSION, package_version

    checks = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "version": manifest()["version"],
        "proxy_version": package_version(),
        "upstream_version": UPSTREAM_BASE_VERSION,
        "api_import": callable(create_app),
        "icon": len(app_icon_bytes(".ico")) > 0,
        "node": (resources_dir() / "runtime/node/node.exe").is_file(),
        "gemini": (
            resources_dir()
            / "runtime/gemini/node_modules/@google/gemini-cli/bundle/gemini.js"
        ).is_file(),
        "executables": all(
            (application_dir() / name).is_file()
            for name in (
                "ClaudeTUI.exe",
                "ClaudeTUI-Service.exe",
                "ClaudeTUI-Terminal.exe",
            )
        ),
        "native_tools": tools_ready(),
    }
    return checks
