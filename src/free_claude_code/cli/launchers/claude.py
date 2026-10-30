"""Launch Claude with a minimal prompt and the FCC gateway connection."""

import json
from collections.abc import Sequence

from free_claude_code.harnesses.claude import CLAUDE_BINARY_NAME, build_claude_proxy_env
from free_claude_code.harnesses.claude_agents import (
    DELEGATION_PROMPT,
    model_agents_json,
)
from free_claude_code.harnesses.launch import PreparedLaunch
from free_claude_code.harnesses.resources import LaunchResources

from .catalog_http import fetch_proxy_model_catalog
from .runner import HarnessSpec, LaunchContext, launch_harness

CLEAN_SYSTEM_PROMPT = (
    "You are a coding assistant working in the user's project. "
    "Carry out the user's requests using the available tools. "
    "Inspect relevant files before making changes, preserve unrelated work, "
    "and verify results. Be direct and report what you actually did. "
    "Ask a concise question only when essential information is missing. "
    + DELEGATION_PROMPT
)


def _native_options(args: list[str]) -> set[str]:
    option_args = args[: args.index("--")] if "--" in args else args
    return {arg.partition("=")[0] for arg in option_args}


def _clean_arguments(args: list[str]) -> list[str]:
    """Apply clean defaults, preserving explicit native options and literal text."""
    options = _native_options(args)
    defaults: list[str] = []
    if not options.intersection({"--bare", "--safe-mode"}):
        if "--setting-sources" not in options:
            defaults.extend(["--setting-sources", ""])
        if "--settings" not in options:
            defaults.extend(
                [
                    "--settings",
                    json.dumps({"disableAllHooks": True, "autoMemoryEnabled": False}),
                ]
            )
        if "--mcp-config" not in options and "--strict-mcp-config" not in options:
            defaults.append("--strict-mcp-config")
    if not options.intersection({"--system-prompt", "--system-prompt-file"}):
        defaults.extend(["--system-prompt", CLEAN_SYSTEM_PROMPT])
    # Resumed conversations must not retain a previously recorded system prompt.
    if "--system-prompt-snapshot" not in options:
        defaults.extend(["--system-prompt-snapshot", "off"])
    if not options.intersection(
        {
            "--permission-mode",
            "--permission-prompts",
            "--permission-prompt-tool",
            "--dangerously-skip-permissions",
        }
    ):
        defaults.append("--dangerously-skip-permissions")
    return [*defaults, *args]


def _clean_mode(ctx: LaunchContext) -> bool:
    value = ctx.base_env.get("FCC_CLAUDE_CLEAN", "1").strip().casefold()
    if value not in {"0", "1"}:
        raise ValueError("FCC_CLAUDE_CLEAN must be 1 or 0.")
    return value == "1"


def _configure(
    ctx: LaunchContext, args: list[str], _files: LaunchResources
) -> PreparedLaunch:
    env = build_claude_proxy_env(
        proxy_root_url=ctx.proxy_root_url,
        auth_token=ctx.auth_token,
        base_env=ctx.base_env,
    )
    if _clean_mode(ctx):
        args = _clean_arguments(args)
        options = _native_options(args)
        if not options.intersection({"--safe-mode", "--bare"}):
            env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
            if not options.intersection(
                {"--agents", "--help", "-h", "--version", "-v"}
            ):
                catalog = ctx.catalog or fetch_proxy_model_catalog(
                    ctx.proxy_root_url, ctx.auth_token, view="messages"
                )
                args = ["--agents", model_agents_json(catalog), *args]
        if "--bare" in _native_options(args):
            # Explicit bare mode needs an API key. Use only the FCC-local token.
            env["ANTHROPIC_API_KEY"] = ctx.auth_token
        # Keep every model on the gateway even if the parent used a native
        # Bedrock/Vertex/Foundry connection before launching FCC.
        env = {
            key: value
            for key, value in env.items()
            if not key.startswith("CLAUDE_CODE_USE_")
        }
    return PreparedLaunch(
        [ctx.binary_path, *args],
        env,
    )


SPEC = HarnessSpec(
    binary_name=CLAUDE_BINARY_NAME,
    display_name="Claude Code",
    install_hint="Install Claude Code with: npm install -g @anthropic-ai/claude-code",
    configure=_configure,
)


def launch(argv: Sequence[str] | None = None) -> None:
    launch_harness(SPEC, argv)
