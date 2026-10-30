"""Regional QwenCloud API with an explicitly configured model catalog."""

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

_PROFILE = OpenAIChatProfile(
    OpenAIChatRequestPolicy(
        provider_name="QWENCLOUD_API",
        reasoning_replay=ReasoningReplayMode.REASONING_CONTENT,
    ),
    NO_REASONING,
)


class QwenCloudAPIProvider(OpenAIChatProvider):
    """Use Chat Completions without probing an unsupported GET /models route."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        admission: ProviderAdmissionController,
        model_ids: tuple[str, ...],
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model_ids = model_ids
        super().__init__(config, profile=_PROFILE, admission=admission, client=client)

    async def list_model_infos(self) -> frozenset[ProviderModelInfo]:
        # This is a configured catalog, not an assertion of account entitlements.
        return model_infos_from_ids(self._model_ids)
