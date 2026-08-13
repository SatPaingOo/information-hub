"""Tests for src.storage.store — three-way dataset writer."""

from __future__ import annotations

import json
from pathlib import Path

from src.storage.store import Store

from ..conftest import sample_record


def test_store_writes_three_way(tmp_path: Path):
    store = Store(tmp_path)
    rec = sample_record()
    store.write_record(rec, "ai-ml")

    json_path = store.data_set / "ai-ml" / f"{rec['key']}.json"
    md_path = store.preview / "ai-ml" / f"{rec['key']}.md"
    assert json_path.exists()
    assert md_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["id"] == rec["id"]
    assert "## Related" in md_path.read_text(encoding="utf-8")


def test_store_iter_records_roundtrip(tmp_path: Path):
    store = Store(tmp_path)
    store.write_record(sample_record(key="2026-08-14-001"), "ai-ml")
    store.write_record(sample_record(key="2026-08-14-002", topic="geopolitics",
                                     region="myanmar"), "myanmar")
    records = store.iter_records()
    assert len(records) == 2
    assert {r["key"] for r in records} == {"2026-08-14-001", "2026-08-14-002"}


def test_store_writes_raw_run(tmp_path: Path):
    store = Store(tmp_path)
    path = store.write_raw_run("2026-08-14T01-00-00+00-00", [sample_record()])
    assert path.name == "2026-08-14T01-00-00+00-00.json"
    frame = json.loads(path.read_text(encoding="utf-8"))
    assert frame["item_count"] == 1
