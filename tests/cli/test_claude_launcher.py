"""Clean Claude defaults preserve explicit native options and the FCC gateway."""

import os

import pytest

from free_claude_code.cli.launchers.claude import CLEAN_SYSTEM_PROMPT
from tests.cli.conftest import LaunchCapture
from tests.cli.test_launcher_workflow import launch


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--permission-mode", "auto", "fix tests"],
        ["--permission-mode=acceptEdits", "fix tests"],
        ["--dangerously-skip-permissions", "fix tests"],
        ["--permission-mode"],
        ["--", "--permission-mode=auto"],
        ["explain --permission-mode auto"],
    ],
)
def test_explicit_permission_selection_overrides_clean_default(
    args: list[str], launch_capture: LaunchCapture
) -> None:
    launch("claude", args)
    command = launch_capture.commands[0]
    assert command[0] == "claude"
    if args:
        assert command[-len(args) :] == args
    assert "--safe-mode" in command
    assert command[command.index("--system-prompt") + 1] == CLEAN_SYSTEM_PROMPT
    assert command[command.index("--system-prompt-snapshot") + 1] == "off"
    explicit_permission = args and args[0].startswith(
        ("--permission-mode", "--dangerously-skip-permissions")
    )
    if explicit_permission and args[0] != "--dangerously-skip-permissions":
        assert "--dangerously-skip-permissions" not in command
    else:
        assert command.count("--dangerously-skip-permissions") == 1
    env = launch_capture.environments[0]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8182"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "launcher-test-token"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"
    assert env["CLAUDE_CODE_AUTO_MODE_SERVER"] == "0"


@pytest.mark.parametrize(
    "args",
    [
        ["--system-prompt", "My prompt"],
        ["--system-prompt=My prompt"],
        ["--system-prompt-file", "prompt.txt"],
        ["--system-prompt-file=prompt.txt"],
    ],
)
def test_clean_launch_preserves_explicit_system_prompt(args, launch_capture):
    launch("claude", args)
    command = launch_capture.commands[0]
    assert CLEAN_SYSTEM_PROMPT not in command
    assert command[-len(args) :] == args


def test_clean_launch_keeps_literal_options_in_prompt(launch_capture):
    launch("claude", ["--", "--system-prompt", "--permission-mode"])
    command = launch_capture.commands[0]
    assert command[command.index("--system-prompt") + 1] == CLEAN_SYSTEM_PROMPT
    assert "--dangerously-skip-permissions" in command[: command.index("--")]


def test_clean_launch_keeps_all_provider_traffic_on_fcc(launch_capture, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "upstream-key")
    launch("claude", [])
    env = launch_capture.environments[0]
    assert not any(key.startswith("CLAUDE_CODE_USE_") for key in env)
    assert "ANTHROPIC_API_KEY" not in env
    assert os.environ["ANTHROPIC_API_KEY"] == "upstream-key"
    assert os.environ["CLAUDE_CODE_USE_VERTEX"] == "1"


def test_explicit_bare_mode_uses_only_the_gateway_key(launch_capture):
    launch("claude", ["--bare"])
    command = launch_capture.commands[0]
    assert "--safe-mode" not in command
    assert command.count("--bare") == 1
    assert launch_capture.environments[0]["ANTHROPIC_API_KEY"] == "launcher-test-token"


def test_native_mode_preserves_original_launch(launch_capture, monkeypatch):
    monkeypatch.setenv("FCC_CLAUDE_CLEAN", "0")
    launch("claude", ["--continue"])
    assert launch_capture.commands == [["claude", "--continue"]]
    assert "ANTHROPIC_API_KEY" not in launch_capture.environments[0]


def test_clean_mode_rejects_invalid_configuration(launch_capture, monkeypatch, capsys):
    monkeypatch.setenv("FCC_CLAUDE_CLEAN", "invalid")
    launch("claude", [], exit_code=1)
    assert not launch_capture.commands
    assert "FCC_CLAUDE_CLEAN must be 1 or 0" in capsys.readouterr().err


@pytest.mark.parametrize("retry_value", [None, "1"])
def test_claude_launch_extends_waiting_without_changing_retries(
    launch_capture: LaunchCapture,
    monkeypatch: pytest.MonkeyPatch,
    retry_value: str | None,
) -> None:
    expected = {
        "CLAUDE_ENABLE_STREAM_WATCHDOG": "0",
        "CLAUDE_ENABLE_BYTE_WATCHDOG": "0",
        "API_FORCE_IDLE_TIMEOUT": "0",
        "API_TIMEOUT_MS": "2147483647",
    }
    for name in expected:
        monkeypatch.setenv(name, "1")
    retry_keys = (
        "CLAUDE_CODE_MAX_RETRIES",
        "CLAUDE_CODE_RETRY_WATCHDOG",
        "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES",
    )
    for name in retry_keys:
        if retry_value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, retry_value)
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "120000")

    launch("claude", [])

    env = launch_capture.environments[0]
    assert {name: env[name] for name in expected} == expected
    assert env["BASH_DEFAULT_TIMEOUT_MS"] == "120000"
    for name in retry_keys:
        if retry_value is None:
            assert name not in env
        else:
            assert env[name] == retry_value
    for name in expected:
        assert os.environ[name] == "1"
