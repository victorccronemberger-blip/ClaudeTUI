"""Session-scoped native subagents for models advertised by the local gateway."""

import hashlib
import json
import re
from collections.abc import Iterable

from free_claude_code.application.model_catalog import CatalogModel, ModelCatalog
from free_claude_code.core.gateway_model_ids import (
    gateway_model_id,
    no_thinking_gateway_model_id,
)
from free_claude_code.core.json_types import JsonObject

# Interactive Claude accepts JSON inline, not a file. Leave room for the native
# executable, the user's arguments and prompt on Windows' 32767-character limit.
MAX_AGENT_JSON_LENGTH = 16000

DELEGATION_PROMPT = (
    "For delegated work, use the registered model subagent whose description names "
    "the provider/model requested by the user. Agent names are model selectors, "
    "not additional providers. Do not pass a model override on these Agent calls: "
    "each definition already selects its exact gateway model. "
    "For a requested execution/review/correction chain, wait for the executor, "
    "give the requested reviewer the task and artifact paths, wait for its review, "
    "then assess the findings, make needed corrections yourself and verify them. "
    "Use the review-only variant when the user asks another model to review work. "
    "Report which agents actually ran and any failures; never claim a delegation "
    "or a check occurred without its result."
)

_WORKER_PROMPT = (
    "Complete the delegated task in the shared project. Preserve unrelated work, "
    "verify changes, and return changed paths, evidence and limitations."
)
_REVIEW_PROMPT = (
    "Review the assigned work against the original task. Read the changed files. "
    "Do not modify files. Return concrete defects with file/line evidence, impact "
    "and suggested fixes. Distinguish verified findings from unchecked concerns."
)


def _model_name(model: CatalogModel) -> str:
    return (
        no_thinking_gateway_model_id(model.provider_model_ref)
        if model.supports_reasoning is False
        else gateway_model_id(model.provider_model_ref)
    )


def _agent_name(ref: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")
    # Include a stable suffix: punctuation/case and truncated names can collide.
    digest = hashlib.sha256(ref.encode()).hexdigest()[:8]
    return f"model-{slug[:45]}-{digest}"


def _deepseek_pro(models: Iterable[CatalogModel]) -> CatalogModel | None:
    candidates = [
        model
        for model in models
        if "deepseek" in model.provider_model_ref.lower()
        and "pro" in model.provider_model_ref.rsplit("/", 1)[-1].lower()
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda model: (
            not model.provider_model_ref.startswith("deepseek/"),
            model.provider_model_ref.rsplit("/", 1)[-1] != "deepseek-v4-pro",
            model.provider_model_ref,
        ),
    )


def build_model_agents(catalog: ModelCatalog) -> JsonObject:
    models = {model.provider_model_ref: model for model in catalog.models}
    definitions: JsonObject = {}
    named: set[str] = set()

    def add(name: str, model: CatalogModel, *, review: bool = False) -> bool:
        definition: JsonObject = {
            "description": ("Review only" if review else "Execute delegated tasks")
            + f" with {model.provider_model_ref}.",
            "prompt": _REVIEW_PROMPT if review else _WORKER_PROMPT,
            "model": _model_name(model),
        }
        if review:
            definition["tools"] = ["Read", "Glob", "Grep"]
        proposed = definitions | {name: definition}
        if (
            len(json.dumps(proposed, ensure_ascii=True, separators=(",", ":")))
            > MAX_AGENT_JSON_LENGTH
        ):
            return False
        definitions[name] = definition
        if not review:
            named.add(model.provider_model_ref)
        return True

    # Friendly names are created only when the exact underlying model is present.
    drael = models.get("drael/drael-v1")
    if drael:
        add("drael", drael)
        add("drael-reviewer", drael, review=True)
    deepseek = _deepseek_pro(models.values())
    if deepseek:
        add("deepseek-pro", deepseek)
        add("deepseek-pro-reviewer", deepseek, review=True)

    ordered = sorted(
        models.values(),
        key=lambda model: (
            model.wire_slug != catalog.default_model_id,
            model.provider_model_ref,
        ),
    )
    # First cover one model per provider, then fill the remaining payload budget.
    seen_providers: set[str] = set()
    prioritized: list[CatalogModel] = []
    remaining: list[CatalogModel] = []
    for model in ordered:
        provider = model.provider_model_ref.split("/", 1)[0]
        (remaining if provider in seen_providers else prioritized).append(model)
        seen_providers.add(provider)
    for model in (*prioritized, *remaining):
        if model.provider_model_ref not in named:
            add(_agent_name(model.provider_model_ref), model)
    return definitions


def model_agents_json(catalog: ModelCatalog) -> str:
    return json.dumps(
        build_model_agents(catalog), ensure_ascii=True, separators=(",", ":")
    )
