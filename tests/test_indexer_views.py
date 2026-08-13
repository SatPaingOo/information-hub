"""Tests for src.indexer + src.views."""

from __future__ import annotations

import json
from pathlib import Path

from src.indexer import Indexer
from src.views import daily_index_markdown, entity_markdown, record_to_markdown

from .conftest import sample_record


def test_record_to_markdown_has_frontmatter_and_wikilinks():
    rec = sample_record()
    md = record_to_markdown(rec)
    assert md.startswith("---")
    assert f"id: \"{rec['id']}\"" in md
    assert "[[RAG]]" in md
    assert "[[OpenAI]]" in md
    assert "## Related" in md
    assert rec["title"] in md


def test_daily_index_markdown_lists_items():
    md = daily_index_markdown("2026-08-14", [sample_record()])
    assert "# Daily Hub — 2026-08-14" in md
    assert "[[info:item:ai-ml:global:2026-08-14-001]]" in md


def test_entity_markdown_backlinks():
    md = entity_markdown("RAG", "concept", [
        {"id": "info:item:ai-ml:global:2026-08-14-001", "title": "T", "date": "2026-08-14"},
    ])
    assert "# RAG" in md
    assert "2026-08-14" in md
    assert "backlink_count: 1" in md


def test_indexer_rebuilds_layers(tmp_path: Path):
    store = _fake_store(tmp_path)
    indexer = Indexer(tmp_path)
    records = store.iter_records()
    indexer.rebuild(records)

    # layer views
    assert (indexer.index_dir / "by-topic" / "ai-ml.json").exists()
    assert (indexer.index_dir / "by-region" / "myanmar.json").exists()
    assert (indexer.index_dir / "by-date" / "2026-08-14.json").exists()
    assert (indexer.index_dir / "by-entity" / "RAG.json").exists()

    # master index
    index = json.loads((indexer.index_dir / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 2
    assert index[0]["topic"] in ("ai-ml", "geopolitics")

    # graph
    graph = json.loads((indexer.index_dir / "graph.json").read_text(encoding="utf-8"))
    assert any(n["type"] == "item" for n in graph["nodes"])
    assert any("RAG" in e["target"] for e in graph["edges"])

    # generated obsidian views
    assert (indexer.preview / "daily" / "2026-08-14.md").exists()
    assert (indexer.preview / "entities" / "concept" / "RAG.md").exists()


def _fake_store(tmp_path: Path):
    from src.store import Store
    store = Store(tmp_path)
    store.write_record(sample_record(key="2026-08-14-001"), "ai-ml")
    store.write_record(sample_record(key="2026-08-14-002", topic="geopolitics",
                                     region="myanmar", title="Myanmar policy story"),
                       "myanmar")
    return store
