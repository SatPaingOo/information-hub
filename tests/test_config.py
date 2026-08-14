"""Tests for src.config — configuration loader (taxonomy engine)."""

from __future__ import annotations

from src.config import Config, Taxonomy

from .conftest import sample_record  # noqa: F401 (imports helpers for test session)


def test_config_loads_from_repo():
    cfg = Config.load()
    assert cfg.gemini.model.startswith("gemini-")          # current-gen model
    assert cfg.providers["gemini"].models[0].startswith("gemini-")
    assert cfg.storage.max_daily_items_total == 10
    assert cfg.targets.total_per_day == 10
    assert cfg.run_control.heartbeat_minutes == 15
    # nested taxonomy
    assert "ai-ml" in cfg.taxonomy.topics
    assert "llm" in cfg.taxonomy.children_of("ai-ml")
    assert "myanmar" in cfg.taxonomy.children_of("asia")
    assert "myanmar-news" in cfg.collections
    assert cfg.collections["myanmar-news"].content_type == "digest"
    assert cfg.collections["tech-news"].content_type == "briefing"
    # per-collection daily targets sum to the total
    assert sum(cfg.targets.collections.values()) == cfg.targets.total_per_day


def test_enabled_collections_only():
    cfg = Config.load()
    for c in cfg.enabled_collections():
        assert c.enabled
    # five collections cover world/tech/politics/products + myanmar
    names = {c.name for c in cfg.enabled_collections()}
    assert {"myanmar-news", "world-news", "tech-news",
            "politics", "products"} <= names


def test_taxonomy_nested_parse():
    tax = Taxonomy({
        "regions": {"asia": ["myanmar", "thailand"]},
        "topics": {"ai-ml": ["llm", "agents"]},
        "categories": ["policy"],           # flat → leaf-only
        "content_types": ["article"],
    })
    assert tax.children_of("asia") == ["myanmar", "thailand"]
    assert tax.parents_of("myanmar") == ["asia"]
    assert tax.layer_of("myanmar") == "region"
    assert tax.layer_of("policy") == "category"
    assert "asia" in tax.all_nodes()
    assert tax.node_id("myanmar") == "taxonomy/region/myanmar"


def test_taxonomy_flat_list_backward_compat():
    tax = Taxonomy({"regions": ["global", "myanmar"], "topics": []})
    assert tax.regions == {"global": [], "myanmar": []}
    assert tax.children_of("global") == []


def test_config_providers_and_quality():
    cfg = Config.load()
    assert cfg.providers["groq"].role == "collect"
    assert cfg.providers["openrouter"].discover == "free_models"
    assert cfg.providers["gemini"].role == "check"
    assert cfg.providers["gemini"].search_tool == "google_search"
    assert cfg.quality.reject_threshold == 0.5
    assert cfg.run.phases == ["collect", "check"]


def test_priority_order():
    cfg = Config.load()
    ordered = cfg.collections_by_priority()
    assert ordered[0].name == "myanmar-news"      # priority 3 first
    assert ordered[1].name == "world-news"        # priority 3 second
    assert ordered[-1].name == "products"         # priority 1 last
