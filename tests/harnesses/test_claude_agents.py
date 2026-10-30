"""Model identity, reviewer boundaries and bounded native agent definitions."""

import json

from free_claude_code.application.model_catalog import CatalogModel, ModelCatalog
from free_claude_code.harnesses.claude_agents import (
    MAX_AGENT_JSON_LENGTH,
    build_model_agents,
    model_agents_json,
)


def model(ref, *, thinking=None):
    return CatalogModel(
        wire_slug=ref,
        provider_model_ref=ref,
        display_name=ref,
        supports_reasoning=thinking,
    )


def test_friendly_agents_keep_exact_models_and_reviewers_have_no_write_tools():
    catalog = ModelCatalog(
        (
            model("drael/drael-v1"),
            model("qwencloud/deepseek-v4-pro"),
            model("google_account/gemini-4-argon-medium"),
        ),
        "google_account/gemini-4-argon-medium",
    )
    agents = build_model_agents(catalog)
    assert agents["drael"]["model"] == "anthropic/drael/drael-v1"
    assert agents["deepseek-pro"]["model"] == "anthropic/qwencloud/deepseek-v4-pro"
    assert set(agents["deepseek-pro-reviewer"]["tools"]) == {"Read", "Grep", "Glob"}
    assert "tools" not in agents["drael"]
    assert "Do not modify files" in agents["deepseek-pro-reviewer"]["prompt"]


def test_unavailable_named_models_are_not_advertised():
    agents = build_model_agents(
        ModelCatalog(
            (model("google_account/gemini-4-argon-medium"),),
            "google_account/gemini-4-argon-medium",
        )
    )
    assert "drael" not in agents and "deepseek-pro" not in agents
    assert len(agents) == 1


def test_direct_deepseek_is_preferred_to_reseller_and_pro_never_selects_flash():
    catalog = ModelCatalog(
        tuple(
            model(ref)
            for ref in (
                "qwencloud/deepseek-v4-pro",
                "deepseek/deepseek-v4-pro",
                "deepseek/deepseek-v4-flash",
            )
        ),
        "qwencloud/deepseek-v4-pro",
    )
    assert (
        build_model_agents(catalog)["deepseek-pro"]["model"]
        == "anthropic/deepseek/deepseek-v4-pro"
    )
    flash = model("deepseek/deepseek-v4-flash")
    assert "deepseek-pro" not in build_model_agents(
        ModelCatalog((flash,), flash.wire_slug)
    )


def test_names_are_stable_and_punctuation_collisions_remain_distinct():
    catalog = ModelCatalog(
        tuple(
            model(ref) for ref in ("p/a.b", "p/a-b", "p/A-B", "p/" + "long-model-" * 20)
        ),
        "p/a.b",
    )
    agents = build_model_agents(catalog)
    assert len(agents) == 4 and all(len(name) <= 64 for name in agents)
    assert agents == build_model_agents(catalog)
    assert len({definition["model"] for definition in agents.values()}) == 4


def test_large_catalog_respects_windows_budget_and_keeps_priority_agents():
    refs = ["provider/" + str(i) + "-" + "x" * 150 for i in range(1000)]
    catalog = ModelCatalog(
        tuple(
            model(ref) for ref in (*refs, "drael/drael-v1", "qwencloud/deepseek-v4-pro")
        ),
        refs[-1],
    )
    encoded = model_agents_json(catalog)
    agents = json.loads(encoded)
    assert len(encoded) <= MAX_AGENT_JSON_LENGTH
    assert {"drael", "deepseek-pro", "deepseek-pro-reviewer"}.issubset(agents)
    assert any(value["model"] == "anthropic/" + refs[-1] for value in agents.values())


def test_non_reasoning_model_preserves_gateway_capability_encoding():
    value = model("p/plain", thinking=False)
    agents = build_model_agents(ModelCatalog((value,), value.wire_slug))
    assert next(iter(agents.values()))["model"] == "claude-3-freecc-no-thinking/p/plain"
