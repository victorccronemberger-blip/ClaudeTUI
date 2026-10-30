"""FCC provider backed by the official OAuth-authenticated Gemini CLI agent."""

import httpx2
from openai import AsyncOpenAI

from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.core.anthropic import ReasoningReplayMode
from free_claude_code.providers.admission import ProviderAdmissionController
from free_claude_code.providers.base import ProviderConfig
from free_claude_code.providers.model_listing import model_infos_from_ids
from free_claude_code.providers.openai_chat import (
    NO_REASONING,
    OpenAIChatProfile,
    OpenAIChatProvider,
    OpenAIChatRequestPolicy,
)

from .auth import GeminiCLIAuthManager
from .transport import GeminiACPTransport

_PROFILE = OpenAIChatProfile(
    OpenAIChatRequestPolicy(
        provider_name="GEMINI_CLI", reasoning_replay=ReasoningReplayMode.DISABLED
    ),
    NO_REASONING,
)


class GeminiCLIProvider(OpenAIChatProvider):
    def __init__(
        self,
        config: ProviderConfig,
        *,
        auth: GeminiCLIAuthManager,
        admission: ProviderAdmissionController,
    ) -> None:
        self._auth = auth
        client = AsyncOpenAI(
            api_key="fcc-native-cli",
            base_url=config.base_url,
            max_retries=0,
            http_client=httpx2.AsyncClient(
                transport=GeminiACPTransport(auth, config.http_read_timeout),
                follow_redirects=False,
                timeout=config.http_read_timeout,
            ),
        )
        super().__init__(
            config,
            profile=_PROFILE,
            admission=admission,
            client=client,
        )

    async def cleanup(self) -> None:
        await self._client.close()

    async def list_model_infos(self) -> frozenset[ProviderModelInfo]:
        return model_infos_from_ids(self._auth.model_ids)
