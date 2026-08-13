"""Tests for src.storage.indexer — layer views, graph + taxonomy nodes."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import Config
from src.storage.indexer import Indexer
from src.storage.store import Store

from ..conftest import sample_record


def _fake_store(tmp_path: Path) -> Store:
    store = Store(tmp_path)
    store.write_record(sample_record(key="2026-08-14-001"), "ai-ml")
    store.write_record(sample_record(key="2026-08-14-002", topic="geopolitics",
                                     region="myanmar", title="Myanmar policy story"),
                       "myanmar")
    return store


def test_indexer_rebuilds_layers(tmp_path: Path):
    store = _fake_store(tmp_path)
    indexer = Indexer(tmp_path)
    indexer.rebuild(store.iter_records())

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


def test_graph_contains_taxonomy_nodes_and_edges(tmp_path: Path):
    cfg = Config.load()
    store = Store(tmp_path)
    store.write_record(sample_record(topic="ai-ml", region="global"), "ai-ml")
    store.write_record(sample_record(key="2026-08-14-002", topic="geopolitics",
                                     region="myanmar"), "geopolitics")
    indexer = Indexer(tmp_path)
    indexer.rebuild(store.iter_records(), taxonomy=cfg.taxonomy, relations=cfg.relations)

    graph = json.loads((indexer.index_dir / "graph.json").read_text(encoding="utf-8"))
    node_ids = {n["id"] for n in graph["nodes"]}
    # hierarchy-derived nodes
    assert "taxonomy/region/asia" in node_ids
    assert "taxonomy/region/myanmar" in node_ids
    assert "taxonomy/topic/ai-ml" in node_ids
    # item-derived classification edges
    rels = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}
    assert ("info:item:ai-ml:global:2026-08-14-001",
            "taxonomy/topic/ai-ml", "classified_in") in rels
    # hierarchy edge asia -> myanmar
    assert ("taxonomy/region/asia", "taxonomy/region/myanmar", "parent_of") in rels
    # static relation myanmar -> geopolitics
    assert any(e["relation"] == "relates" and "geopolitics" in e["target"]
               and "myanmar" in e["source"] for e in graph["edges"])


def test_taxonomy_index_and_notes(tmp_path: Path):
    cfg = Config.load()
    store = Store(tmp_path)
    store.write_record(sample_record(topic="ai-ml", region="global"), "ai-ml")
    indexer = Indexer(tmp_path)
    indexer.rebuild(store.iter_records(), taxonomy=cfg.taxonomy, relations=cfg.relations)

    tax_view = json.loads((indexer.index_dir / "taxonomy.json").read_text(encoding="utf-8"))
    assert tax_view["nodes"]["ai-ml"]["layer"] == "topic"
    assert tax_view["nodes"]["ai-ml"]["children"] == ["llm", "agents", "vision"]

    note = indexer.preview / "taxonomy" / "topics" / "ai-ml.md"
    assert note.exists()
    content = note.read_text(encoding="utf-8")
    assert "# ai-ml" in content
    assert "[[llm]]" in content
