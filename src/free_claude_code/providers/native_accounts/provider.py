"""Native Google account provider sharing FCC's API and tool conversions."""

import httpx2
from openai import AsyncOpenAI

from free_claude_code.core.anthropic import ReasoningReplayMode
from free_claude_code.providers.openai_chat import (
    NO_REASONING,
    OpenAIChatProfile,
    OpenAIChatProvider,
    OpenAIChatRequestPolicy,
)

from .google import text_model
from .transport import NativeChatTransport


class GoogleAccountProvider(OpenAIChatProvider):
    def __init__(self, config, *, auth, engine, admission):
        self._auth = auth
        client = AsyncOpenAI(
            api_key="fcc-native-account",
            base_url=config.base_url,
            max_retries=0,
            http_client=httpx2.AsyncClient(
                transport=NativeChatTransport(auth, engine, config.http_read_timeout),
                follow_redirects=False,
                timeout=config.http_read_timeout,
            ),
        )
        profile = OpenAIChatProfile(
            OpenAIChatRequestPolicy(
                provider_name="GOOGLE_ACCOUNT",
                reasoning_replay=ReasoningReplayMode.DISABLED,
            ),
            NO_REASONING,
        )
        super().__init__(config, profile=profile, admission=admission, client=client)

    async def cleanup(self):
        await self._client.close()

    async def list_model_infos(self):
        return frozenset(text_model(name) for name in self._auth.model_ids)
