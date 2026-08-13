"""Tests for src.config."""

from __future__ import annotations

from src.config import Config

from .conftest import sample_record  # noqa: F401 (imports helpers for test session)


def test_config_loads_from_repo():
    cfg = Config.load()
    assert cfg.gemini.model == "gemini-2.5-flash"
    assert cfg.storage.max_daily_items_total == 6
    # nested taxonomy
    assert "ai-ml" in cfg.taxonomy.topics
    assert "llm" in cfg.taxonomy.children_of("ai-ml")
    assert "myanmar" in cfg.taxonomy.children_of("asia")
    assert "myanmar-news" in cfg.collections
    assert cfg.collections["myanmar-news"].primary_layer == "region"
    assert cfg.collections["ai-research"].content_type == "briefing"


def test_enabled_collections_only():
    cfg = Config.load()
    for c in cfg.enabled_collections():
        assert c.enabled
    # all three sample collections are enabled by default
    names = {c.name for c in cfg.enabled_collections()}
    assert {"myanmar-news", "ai-research", "us-tech"} <= names
