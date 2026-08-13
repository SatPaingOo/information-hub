"""Tests for V2 taxonomy hierarchy + graph cross-links."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import Config, Taxonomy
from src.indexer import Indexer
from src.store import Store

from .conftest import sample_record


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


def test_config_loads_nested_taxonomy_and_controls():
    cfg = Config.load()
    assert cfg.taxonomy.children_of("asia") == ["myanmar", "thailand", "china"]
    assert cfg.taxonomy.children_of("ai-ml") == ["llm", "agents", "vision"]
    assert cfg.relations, "sample relations block expected"
    coll = cfg.collections["myanmar-news"]
    assert coll.priority == 3
    assert coll.frequency == "daily"
    assert cfg.collections["us-tech"].frequency == "every-2-days"
    assert cfg.gemini.key_strategy in ("round_robin", "least_used")


def test_priority_order():
    cfg = Config.load()
    ordered = cfg.collections_by_priority()
    assert ordered[0].name == "myanmar-news"      # priority 3 first
    assert ordered[-1].name == "us-tech"          # priority 1 last


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
