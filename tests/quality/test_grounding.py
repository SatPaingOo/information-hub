"""Tests for src.quality.grounding — grounding engine (phase check)."""

from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.llm.providers import ModelSpec, ProviderManager
from src.quality.grounding import GroundingEngine
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

from ..conftest import sample_record


def _setup(tmp_path: Path):
    cfg = Config.load()
    reg = Registry(tmp_path)
    run_log = RunLog(reg.dir)
    pm = ProviderManager(cfg, reg, run_log, mock=True)
    return cfg, reg, run_log, pm


def test_check_record_mock_scores_and_verifies(tmp_path: Path):
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    spec = ModelSpec(provider="gemini", model="gemini-2.5-flash",
                     fmt="google", search_tool="google_search")
    rec = sample_record(key="2026-08-14-001")
    # items are registered during collect before check verifies them
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    result = engine.check_record(rec, spec)

    assert result["method"] == "gemini-search"
    assert result["grounding_score"] >= 0.5          # mock: 2/3 grounded
    assert result["claims_total"] >= 1
    assert result["sources_verified"]                # citations present
    assert result["review_status"] == "verified"

    # registry approval trail updated
    status = reg.item_status(rec["id"])
    assert status["review_status"] == "verified"
    assert status["approved_by"]["provider"] == "gemini"
    assert status["approved_by"]["model"] == "gemini-2.5-flash"
    assert status["approved_by"]["type"] == "ai"


def test_low_score_marks_pending_review(tmp_path: Path):
    cfg, reg, run_log, pm = _setup(tmp_path)
    # simulate a verify response with everything grounded=false
    pm.mock_verify_result = {"claims": [
        {"text": "c1", "grounded": False, "source_url": "", "source_title": ""},
        {"text": "c2", "grounded": False, "source_url": "", "source_title": ""},
    ]}
    engine = GroundingEngine(cfg, reg, run_log, pm)
    spec = ModelSpec(provider="gemini", model="gemini-2.5-flash",
                     fmt="google", search_tool="google_search")
    rec = sample_record(key="2026-08-14-002")
    result = engine.check_record(rec, spec)
    assert result["grounding_score"] == 0.0
    assert result["review_status"] == "pending_review"


def test_source_reputation_updates(tmp_path: Path):
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    spec = ModelSpec(provider="gemini", model="gemini-2.5-flash",
                     fmt="google", search_tool="google_search")
    rec = sample_record(key="2026-08-14-003")
    engine.check_record(rec, spec)
    stats = reg.source_stats("arXiv")
    assert stats.get("avg_grounding_score") is not None
    assert stats.get("grounding_scores")          # history kept


def test_lexical_fallback_without_spec(tmp_path: Path):
    """No provider/spec available → lexical grounding against fulltext."""
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-004")
    rec["key_facts"] = [
        "background context sentence repeated analysis content",
        "analysis content about the key development facts",
    ]
    result = engine.check_record(rec, spec=None, fulltext=" ".join([
        "background context sentence analysis content development",
        "fact sentence repeated wording",
    ]))
    assert result["method"] == "lexical"
    assert result["grounding_score"] is not None      # always scores (no API)
    assert result["review_status"] in ("verified", "pending_review")
    # registry approval still recorded for the lexical path
    assert reg.item_status(rec["id"])["review_status"] in ("verified", "pending_review")
