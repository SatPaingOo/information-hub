"""Tests for src.storage.registry — key-value tracking."""

from __future__ import annotations

from pathlib import Path

from src.storage.registry import Registry

from ..conftest import sample_record


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


def test_registry_approval_trail(tmp_path: Path):
    reg = Registry(tmp_path)
    rec = sample_record(key="2026-08-14-001")
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock-generator")
    reg.update_approval(rec["id"], 0.8, "ai", "gemini", "gemini-2.5-flash")
    status = reg.item_status(rec["id"])
    assert status["grounding_score"] == 0.8
    assert status["review_status"] == "verified"
    assert status["approved_by"]["provider"] == "gemini"
    assert status["approved_by"]["type"] == "ai"


def test_registry_source_reputation(tmp_path: Path):
    reg = Registry(tmp_path)
    reg.record_grounding("arXiv", 0.8)
    reg.record_grounding("arXiv", 0.6, failed=True)
    stats = reg.source_stats("arXiv")
    assert stats["grounding_failures"] == 1
    assert stats["avg_grounding_score"] == 0.7


def test_registry_provider_budget(tmp_path: Path):
    reg = Registry(tmp_path)
    reg.record_provider_call("groq", "llama-3.1-8b-instant", items=2, latency_ms=50)
    stats = reg.provider_model_stats("groq", "llama-3.1-8b-instant")
    assert stats["calls"] == 1
    assert stats["items"] == 2
    assert reg.provider_calls("groq", "llama-3.1-8b-instant") == 1
    assert reg.provider_items("groq", "llama-3.1-8b-instant") == 2
    reg.record_provider_failure("groq", "llama-3.1-8b-instant", mark_down=True)
    assert reg.provider_healthy("groq", "llama-3.1-8b-instant") is False
