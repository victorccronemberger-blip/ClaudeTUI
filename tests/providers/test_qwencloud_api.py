"""QwenCloud API settings, region routing, catalog and real Chat conversion."""

import json

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from free_claude_code.config.provider_catalog import (
    PROVIDER_CATALOG,
    QWENCLOUD_API_DEFAULT_BASE,
)
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic import aggregate_anthropic_sse_to_message
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.providers.qwencloud_api import QwenCloudAPIProvider
from free_claude_code.providers.runtime.config import build_provider_config
from tests.providers.support import immediate_admission


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base",
    [
        QWENCLOUD_API_DEFAULT_BASE,
        "https://eu-central-1.maas.qwencloudapi.com/compatible-mode/v1",
    ],
)
async def test_region_key_catalog_and_completion_use_correct_route(base):
    settings = Settings(
        QWENCLOUD_PAYG_API_KEY="synthetic-payg",
        QWENCLOUD_API_KEY="synthetic-token-plan",
        QWENCLOUD_PAYG_BASE_URL=base,
        QWENCLOUD_PAYG_MODELS="qwen3.7-plus, qwen3.8-flash,qwen3.7-plus",
    )
    config = build_provider_config(PROVIDER_CATALOG["qwencloud_api"], settings)
    assert config.api_key == "synthetic-payg"
    assert (
        build_provider_config(PROVIDER_CATALOG["qwencloud"], settings).api_key
        == "synthetic-token-plan"
    )
    bodies = []

    def respond(request):
        assert str(request.url) == base + "/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-payg"
        bodies.append(json.loads(request.content))
        chunks = [
            {
                "id": "chat_test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "qwen3.7-plus",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Qwen answer"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chat_test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "qwen3.7-plus",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
            + "data: [DONE]\n\n",
        )

    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    )
    provider = QwenCloudAPIProvider(
        config,
        admission=immediate_admission(max_attempts=1),
        model_ids=tuple(settings.qwencloud_payg_models.split(",")),
        client=client,
    )
    try:
        assert {m.model_id for m in await provider.list_model_infos()} == {
            "qwen3.7-plus",
            "qwen3.8-flash",
        }
        assert bodies == []  # The real service does not expose GET /models.
        request = MessagesRequest(
            model="qwen3.7-plus",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello"}],
        )
        message, error, done = await aggregate_anthropic_sse_to_message(
            provider.stream_messages(request)
        )
        assert done and error is None
        assert message["content"] == [{"type": "text", "text": "Qwen answer"}]
    finally:
        await provider.cleanup()
        await client.close()


@pytest.mark.parametrize("value", ["", "one,,two", ",one", "one,", "model with spaces"])
def test_configured_catalog_rejects_empty_or_malformed_ids(value):
    with pytest.raises(ValidationError):
        Settings(QWENCLOUD_PAYG_MODELS=value)
