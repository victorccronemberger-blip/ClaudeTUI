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
    assert not control.provider_ready(status, {"connected": True, "model_count": 0})
    assert control.provider_ready(status, {"connected": True, "model_count": 1})
    status["provider_status"] = [
        {"provider_id": "gemini_cli", "kind": "remote", "status": "missing_key"}
    ]
    assert not control.provider_ready(status, {"connected": True})


def test_connected_account_with_failed_discovery_does_not_enable_terminal():
    status = {
        "provider": "openai",
        "provider_status": [{"provider_id": "openai", "kind": "connected_account"}],
        "startup": {
            "providers": {"openai": "failed"},
            "provider_errors": {
                "openai": "The provider returned an invalid model list."
            },
        },
    }
    account = {"connected": True, "model_count": 1}
    assert not control.provider_ready(status, account)
    assert (
        control.provider_problem(status, account)
        == "The provider returned an invalid model list."
    )


def test_other_program_on_service_port_is_not_mistaken_for_claudetui(monkeypatch):
    monkeypatch.setattr(control, "read_api", lambda _path: ["unrelated JSON response"])
    assert control.service_status() is None


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


def test_official_user_antigravity_installation_is_preferred(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installed = tmp_path / "agy/bin/agy.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"official-user-install")
    assert runtime.tool_path("antigravity") == installed


def test_missing_antigravity_is_installed_from_verified_pinned_binary(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(runtime, "user_dir", lambda: tmp_path / "managed")
    config = {name: {"version": "1"} for name in ("claude", "git")}
    config["antigravity"] = {
        "version": "1",
        "url": "https://official.example/agy.exe",
        "sha256": hashlib.sha256(b"native-google").hexdigest(),
    }
    monkeypatch.setattr(runtime, "manifest", lambda: config)
    for name in ("claude", "git"):
        path = runtime.tool_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing")

    def verified(spec, destination, *_args):
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"native-google")
        runtime.verify_file(destination, spec)

    monkeypatch.setattr(runtime, "download_verified", verified)
    assert not runtime.tools_ready()
    runtime.install_tools(lambda _: None)
    assert runtime.tool_path("antigravity").read_bytes() == b"native-google"
    assert runtime.tools_ready()


def test_terminal_forwards_native_arguments_through_gateway_launcher(monkeypatch):
    from free_claude_code.cli.launchers import claude, common
    from free_claude_code.harnesses.launch import PreparedLaunch

    monkeypatch.setattr(runtime, "configure_environment", lambda: None)
    monkeypatch.setattr(runtime, "tools_ready", lambda: True)
    monkeypatch.setattr(control, "ensure_service", lambda: None)
    monkeypatch.setattr(runtime, "tool_path", lambda _name: "claude")
    prepare = MagicMock(return_value=PreparedLaunch(["claude", "-p", "check"], {}))
    execute = MagicMock()
    monkeypatch.setattr(claude, "_configure", prepare)
    monkeypatch.setattr(common, "run_client_process", execute)
    args = ["-p", "check", "--model", "anthropic/drael/drael-v1"]
    control.launch_terminal(args)
    assert prepare.call_args.args[1] == args
    assert execute.call_args.kwargs["command"] == ["claude", "-p", "check"]
