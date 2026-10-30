"""Drael wire contract: account models, caller tools and reasoning levels."""

from unittest.mock import AsyncMock

import httpx2
import pytest
from openai import AsyncOpenAI

from free_claude_code.config.constants import ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS
from free_claude_code.config.provider_catalog import (
    DRAEL_DEFAULT_BASE,
    PROVIDER_CATALOG,
)
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.core.model_capabilities import ModelInputModality
from free_claude_code.core.reasoning import ReasoningEffort, ReasoningPolicy
from tests.providers.support import (
    REASONING_DEFAULT,
    REASONING_OFF,
    REASONING_ON,
    immediate_admission,
    make_provider_config,
    profiled_provider,
)


@pytest.fixture
def provider():
    return profiled_provider(
        "drael",
        make_provider_config("fixture-key", DRAEL_DEFAULT_BASE),
        admission=immediate_admission(provider_name="drael", max_attempts=1),
    )


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (REASONING_DEFAULT, None),
        (REASONING_OFF, "low"),
        (REASONING_ON, "medium"),
        (ReasoningPolicy.on(effort=ReasoningEffort.MINIMAL), "low"),
        (ReasoningPolicy.on(effort=ReasoningEffort.LOW), "low"),
        (ReasoningPolicy.on(effort=ReasoningEffort.MEDIUM), "medium"),
        (ReasoningPolicy.on(effort=ReasoningEffort.HIGH), "high"),
        (ReasoningPolicy.on(effort=ReasoningEffort.XHIGH), "high"),
        (ReasoningPolicy.on(effort=ReasoningEffort.MAX), "high"),
    ],
)
def test_reasoning_levels_match_the_documented_api(provider, policy, expected):
    body = provider._chat._build_request_body(
        MessagesRequest(
            model="drael-v1",
            max_tokens=64,
            messages=[{"role": "user", "content": "Hello"}],
        ),
        reasoning=policy,
    )
    assert body.get("reasoning_effort") == expected
    # The API ignores this ceiling; the adapter preserves the caller request.
    assert body.get("max_tokens", ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS) == 64
    assert body["tools"] == [] and body["tool_choice"] == "none"


def test_caller_tools_and_continuation_are_preserved(provider):
    request = MessagesRequest.model_validate(
        {
            "model": "drael-v1",
            "max_tokens": 100,
            "tools": [
                {
                    "name": "Read",
                    "description": "Read a caller file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "Read"},
            "messages": [
                {"role": "user", "content": "Read fixture.txt"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "fixture reasoning"},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "Read",
                            "input": {"path": "fixture.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "fixture output",
                        }
                    ],
                },
            ],
        }
    )
    body = provider._chat._build_request_body(request, reasoning=REASONING_ON)
    assert body["tools"][0]["function"]["name"] == "Read"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "Read"}}
    assert body["messages"][1]["reasoning_content"] == "fixture reasoning"
    assert body["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "fixture output",
    }


@pytest.mark.asyncio
async def test_discovery_reads_live_model_capabilities(provider):
    provider._client.get = AsyncMock(
        return_value={
            "data": [
                {
                    "id": "drael-v1",
                    "context_length": 122880,
                    "reasoning": True,
                    "tool_call": True,
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                }
            ]
        }
    )
    models = await provider.list_model_infos()
    assert len(models) == 1
    info = next(iter(models))
    assert info.model_id == "drael-v1" and info.context_window_tokens == 122880
    assert info.supports_thinking is True
    assert info.input_modalities == frozenset(
        {ModelInputModality.TEXT, ModelInputModality.IMAGE}
    )


@pytest.mark.asyncio
async def test_model_discovery_uses_documented_url_and_bearer_auth(provider):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json={"data": [{"id": "drael-v1"}]})

    await provider._client.close()
    provider._client = AsyncOpenAI(
        api_key="fixture-wire-key",
        base_url=DRAEL_DEFAULT_BASE,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )
    try:
        await provider.list_model_infos()
    finally:
        await provider.cleanup()
    assert len(requests) == 1
    assert str(requests[0].url) == "https://drael.sh/v1/models"
    assert requests[0].headers["authorization"] == "Bearer fixture-wire-key"


def test_api_keys_are_secret_settings_and_endpoint_is_configurable():
    settings = Settings(
        DRAEL_API_KEY="fixture-private-key", DRAEL_BASE_URL="https://example.test/v1"
    )
    assert "fixture-private-key" not in repr(settings)
    assert settings.drael_base_url == "https://example.test/v1"
    assert PROVIDER_CATALOG["drael"].credential_attr == "drael_api_keys"
