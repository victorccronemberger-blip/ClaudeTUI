"""Prevent a native account client from inheriting the gateway connection."""

import os
from collections.abc import Mapping


def native_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    excluded = {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GEMINI_BASE_URL",
        "GEMINI_API_BASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
    return {
        key: value
        for key, value in source.items()
        if not key.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))
        and key.upper() not in excluded
    }
