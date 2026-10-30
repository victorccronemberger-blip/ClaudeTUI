"""Windows distribution boundaries: integrity, isolation and real readiness."""

import hashlib
import io
import tarfile
import threading
from unittest.mock import MagicMock

import pytest

from free_claude_code.windows_app import control, runtime


def test_cached_models_are_not_provider_credentials():
    status = {
        "provider": "gemini_cli",
        "cached_models": {"gemini_cli": ["gemini-pro"]},
        "provider_status": [
            {
                "provider_id": "gemini_cli",
                "kind": "connected_account",
                "status": "disconnected",
            }
        ],
    }
    assert not control.provider_ready(status)
    assert not control.provider_ready(status, {"connected": False})
    assert control.provider_ready(status, {"connected": True})
    status["provider_status"] = [
        {"provider_id": "gemini_cli", "kind": "remote", "status": "missing_key"}
    ]
    assert not control.provider_ready(status, {"connected": True})


def test_tampered_download_never_becomes_an_install(monkeypatch, tmp_path):
    destination = tmp_path / "archive.exe"
    response = MagicMock()
    response.headers = {"Content-Length": "6"}
    response.read.side_effect = [b"broken", b""]
    response.__enter__.return_value = response
    monkeypatch.setattr(runtime, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match="verificação"):
        runtime.download_verified(
            {"url": "https://example.com/tool", "sha256": "0" * 64}, destination
        )
    assert not destination.exists()
    assert not destination.with_suffix(".exe.part").exists()


def test_cancelled_download_removes_partial(monkeypatch, tmp_path):
    response = MagicMock()
    response.headers = {}
    response.read.return_value = b"content"
    response.__enter__.return_value = response
    monkeypatch.setattr(runtime, "urlopen", lambda *_args, **_kwargs: response)
    cancelled = threading.Event()
    cancelled.set()
    destination = tmp_path / "tool.tgz"
    with pytest.raises(InterruptedError):
        runtime.download_verified(
            {"url": "https://example.com/tool"}, destination, cancel=cancelled
        )
    assert list(tmp_path.iterdir()) == []


def test_native_extraction_is_staged_and_ignores_unrelated_archive_members(
    monkeypatch, tmp_path
):
    root = tmp_path / "User With Spaces"
    monkeypatch.setattr(runtime, "user_dir", lambda: root)
    source = tmp_path / "claude.tgz"
    with tarfile.open(source, "w:gz") as archive:
        for name, content in [
            ("package/claude.exe", b"native"),
            ("../escaped", b"bad"),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    config = {
        "claude": {
            "version": "1",
            "url": "https://example.com/claude.tgz",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "git": {"version": "1"},
    }
    monkeypatch.setattr(runtime, "manifest", lambda: config)
    bash = root / "runtime/git-1/bin/bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"git")

    def cached(_spec, destination, *_args):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(runtime, "download_verified", cached)
    runtime.install_tools(lambda _message: None)
    assert runtime.tool_path("claude").read_bytes() == b"native"
    assert not (root / "runtime/escaped").exists()
    assert not list((root / "runtime").glob(".claude-*"))


def test_frozen_service_uses_absolute_sibling_not_python_or_path(monkeypatch, tmp_path):
    installed = tmp_path / "Installed App"
    monkeypatch.setattr(control, "application_dir", lambda: installed)
    monkeypatch.setattr(
        control, "service_status", MagicMock(side_effect=[None, {"status": "running"}])
    )
    spawn = MagicMock()
    monkeypatch.setattr(control.subprocess, "Popen", spawn)
    control.ensure_service()
    assert spawn.call_args.args[0] == [str(installed / "ClaudeTUI-Service.exe")]
    assert spawn.call_args.kwargs["cwd"] == installed


def test_managed_git_is_available_to_child_commands_without_global_install(
    monkeypatch, tmp_path
):
    import os

    monkeypatch.setattr(runtime, "resources_dir", lambda: tmp_path / "App With Spaces")
    monkeypatch.setattr(
        runtime, "tool_path", lambda _name: tmp_path / "git/bin/bash.exe"
    )
    monkeypatch.setenv("PATH", "original-path")
    runtime.configure_environment()
    assert os.environ["PATH"].split(os.pathsep)[:3] == [
        str(tmp_path / "App With Spaces/runtime/node"),
        str(tmp_path / "git/cmd"),
        str(tmp_path / "git/usr/bin"),
    ]
    assert os.environ["PATH"].endswith("original-path")
    assert os.environ["CLAUDE_CODE_GIT_BASH_PATH"] == str(tmp_path / "git/bin/bash.exe")


def test_cancel_during_extraction_waits_for_child_and_leaves_no_published_git(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runtime, "user_dir", lambda: tmp_path)
    monkeypatch.setattr(
        runtime,
        "manifest",
        lambda: {
            "claude": {"version": "1"},
            "git": {"version": "1", "url": "https://example.com/git.exe"},
        },
    )
    claude = runtime.tool_path("claude")
    claude.parent.mkdir(parents=True)
    claude.write_bytes(b"existing-version")

    def cached(_spec, destination, *_args):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"verified-extractor")

    monkeypatch.setattr(runtime, "download_verified", cached)
    cancel = threading.Event()
    child = MagicMock()
    child.poll.return_value = None

    def extracting(*_args, **_kwargs):
        cancel.set()
        return child

    monkeypatch.setattr(runtime.subprocess, "Popen", extracting)
    with pytest.raises(InterruptedError):
        runtime.install_tools(lambda _message: None, cancel)
    child.kill.assert_called_once()
    child.wait.assert_called_once()
    assert not runtime.tool_path("git").exists()
    assert claude.read_bytes() == b"existing-version"
    assert not list((tmp_path / "runtime").glob(".git-*"))
