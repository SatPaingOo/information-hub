"""Tests for src.store + src.registry."""

from __future__ import annotations

import json
from pathlib import Path

from src.registry import Registry
from src.store import Store

from .conftest import sample_record


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


def test_registry_tracks_sources_items_meta(tmp_path: Path):
    reg = Registry(tmp_path)
    reg.record_fetch("arxiv", candidates=12, published=1)
    rec = sample_record()
    reg.record_item(rec, status="published", gemini_calls=2, validated=True)

    assert reg.source_stats("arxiv")["items_published"] == 1
    assert reg.item_status(rec["id"])["status"] == "published"
    assert reg.has_seen(rec["title"], rec["source"]["url"]) is True
    assert reg.has_seen("brand new title", "https://brand-new.example") is False

    reg.mark_run([rec["id"]], quota_used=1)
    assert reg.meta["total_items"] == 1
    reg.save()
    reloaded = Registry(tmp_path)
    assert reloaded.meta["total_items"] == 1


def test_registry_next_sequence(tmp_path: Path):
    reg = Registry(tmp_path)
    assert reg.next_sequence("2026-08-14") == 1
    reg.record_item(sample_record(key="2026-08-14-005"), "published", 1, True)
    assert reg.next_sequence("2026-08-14") == 6
    assert reg.next_sequence("2026-08-15") == 1
