"""Tests for src.storage.store — three-way dataset writer (flat, title-based)."""

from __future__ import annotations

import json
from pathlib import Path

from src.storage.store import Store, record_filename, slugify

from ..conftest import sample_record


def test_slugify_normalizes_title():
    assert slugify("  Myanmar Economy Ministry!  ") == "myanmar-economy-ministry"
    assert slugify("RAG vs. Vector DBs — 2026") == "rag-vs-vector-dbs-2026"
    assert len(slugify("x " * 100)) <= 60


def test_record_filename_is_key_plus_slug():
    rec = sample_record(title="Myanmar Economy Ministry Investment")
    assert record_filename(rec) == "2026-08-14-001-myanmar-economy-ministry-investment"


def test_store_writes_flat_three_way(tmp_path: Path):
    store = Store(tmp_path)
    rec = sample_record(title="Myanmar Economy Ministry Investment")
    store.write_record(rec)

    json_path = store.data_set / f"{record_filename(rec)}.json"
    md_path = store.preview / f"{record_filename(rec)}.md"
    assert json_path.exists()
    assert md_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["id"] == rec["id"]
    assert "## Related" in md_path.read_text(encoding="utf-8")
    # no topic/region folder nesting — flat only
    assert not (store.data_set / "ai-ml").exists()


def test_store_iter_records_roundtrip(tmp_path: Path):
    store = Store(tmp_path)
    store.write_record(sample_record(key="2026-08-14-001", title="First story"))
    store.write_record(sample_record(key="2026-08-14-002", title="Second story",
                                     topic="geopolitics", region="myanmar"))
    records = store.iter_records()
    assert len(records) == 2
    assert {r["key"] for r in records} == {"2026-08-14-001", "2026-08-14-002"}


def test_store_writes_raw_run(tmp_path: Path):
    store = Store(tmp_path)
    path = store.write_raw_run("2026-08-14T01-00-00+00-00", [sample_record()])
    assert path.name == "2026-08-14T01-00-00+00-00.json"
    frame = json.loads(path.read_text(encoding="utf-8"))
    assert frame["item_count"] == 1
