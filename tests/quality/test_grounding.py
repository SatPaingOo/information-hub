"""Tests for src.quality.grounding — grounding engine (phase check)."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from src.config import Config
from src.llm.providers import ModelSpec, ProviderManager
from src.quality.grounding import GroundingEngine, review_status_for
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
    rec = sample_record(key="2026-08-14-001")
    # items are registered during collect before check verifies them
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    # check_record picks a FRESH check provider internally (mock → gemini)
    result = engine.check_record(rec, spec=None)

    assert result["method"] == "gemini-search"
    assert result["grounding_score"] >= 0.5          # mock: 2/3 grounded
    assert result["claims_total"] >= 1
    assert result["sources_verified"]                # citations present
    assert result["review_status"] == "verified"

    # registry approval trail updated (approver = internally-picked gemini)
    status = reg.item_status(rec["id"])
    assert status["review_status"] == "verified"
    assert status["approved_by"]["provider"] == "gemini"
    assert status["approved_by"]["model"].startswith("gemini-")
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


def test_lexical_fallback_when_provider_unavailable(tmp_path: Path):
    """Gemini budget exhausted → pick_check None → lexical grounding."""
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-004")
    rec["key_facts"] = [
        "background context sentence repeated analysis content",
        "analysis content about the key development facts",
    ]
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    # exhaust the gemini daily token budget so no check provider is available
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0], tokens=gem.max_daily_tokens)
    result = engine.check_record(rec, spec=None, fulltext=" ".join([
        "background context sentence analysis content development",
        "fact sentence repeated wording",
    ]))
    assert result["method"] == "lexical"
    assert result["grounding_score"] is not None      # always scores (no API)
    # golden rule: a fallback check never verifies and never names an approver
    assert result["review_status"] in ("provisional", "pending_review")
    assert result["approved_by"] is None
    status = reg.item_status(rec["id"])
    assert status["review_status"] == result["review_status"]
    assert status["approved_by"] is None


_GNEWS_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title>
<item>
  <title>Iceland holds knife-edge referendum on EU membership talks - Reuters</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <source url="https://www.reuters.com">Reuters</source>
</item>
</channel></rss>"""


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_gnews(monkeypatch, body: bytes = _GNEWS_RSS) -> list[str]:
    """Serve a canned Google News RSS response; returns the requested URLs."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(getattr(req, "full_url", str(req)))
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_web_corroboration_finds_independent_source(tmp_path: Path, monkeypatch):
    """Gemini down + claim not in source text → web corroboration grounds it
    with an INDEPENDENT outlet citation (method 'web')."""
    calls = _fake_gnews(monkeypatch)
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-005")
    rec["key_facts"] = [
        "Iceland holds knife-edge referendum on EU membership talks"]
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    # exhaust gemini so the fallback path runs
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0],
                             tokens=gem.max_daily_tokens)
    result = engine.check_record(rec, spec=None,
                                 fulltext="completely unrelated filler text")
    # independent outlet corroboration succeeded
    assert result["method"] == "web"
    assert result["claims_grounded"] >= 1
    assert result["sources_verified"]          # real citation(s) attached
    assert calls and calls[0].startswith("https://news.google.com/rss/search?q=")
    assert result["sources_verified"][0]["url"] == "https://www.reuters.com/"
    # corroborated by other outlets is still NOT verified
    assert result["review_status"] == "provisional"
    assert result["approved_by"] is None
    assert reg.item_status(rec["id"])["review_status"] == "provisional"


def test_web_corroboration_network_failure_stays_lexical(tmp_path: Path):
    """Google News unreachable (the autouse fixture blocks urlopen) → the
    check still completes on the lexical score; it must not crash or fail."""
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-009")
    rec["key_facts"] = [
        "Iceland holds knife-edge referendum on EU membership talks"]
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0], tokens=gem.max_daily_tokens)
    result = engine.check_record(rec, spec=None,
                                 fulltext="completely unrelated filler text")
    assert result["method"] == "lexical"
    assert result["sources_verified"] == []
    assert result["review_status"] != "verified"


def test_web_corroboration_skips_same_outlet(tmp_path: Path, monkeypatch):
    """A hit from the record's own outlet is not independent → not grounded."""
    _fake_gnews(monkeypatch, _GNEWS_RSS.replace(b"https://www.reuters.com",
                                                b"https://arxiv.org"))
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-010")   # source host: arxiv.org
    rec["key_facts"] = [
        "Iceland holds knife-edge referendum on EU membership talks"]
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0], tokens=gem.max_daily_tokens)
    result = engine.check_record(rec, spec=None,
                                 fulltext="completely unrelated filler text")
    assert result["method"] == "lexical"
    assert result["sources_verified"] == []


def test_web_corroboration_disabled_stays_lexical(tmp_path: Path):
    """web_corroborate=False → plain lexical fallback, no network."""
    cfg, reg, run_log, pm = _setup(tmp_path)
    cfg.quality.web_corroborate = False
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-006")
    rec["key_facts"] = ["unmatched claim about iceland eu referendum"]
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0],
                             tokens=gem.max_daily_tokens)
    result = engine.check_record(rec, spec=None, fulltext="nothing matches")
    assert result["method"] == "lexical"
    assert result["sources_verified"] == []      # no web pass


def test_lexical_pass_is_provisional_not_verified(tmp_path: Path):
    """Claims fully present in the source text → score 1.0, but Gemini did not
    check it, so it is provisional with no approver (was: 'verified')."""
    cfg, reg, run_log, pm = _setup(tmp_path)
    cfg.quality.web_corroborate = False
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-007")
    rec["key_facts"] = ["parliament approved the new budget on tuesday"]
    rec["implications"] = []
    rec["outlook"] = ""
    reg.record_item(rec, status="published", gemini_calls=1, validated=True,
                    provider="mock", model="mock")
    gem = cfg.providers["gemini"]
    reg.record_provider_call("gemini", gem.models[0], tokens=gem.max_daily_tokens)
    result = engine.check_record(
        rec, spec=None, fulltext="The parliament approved the new budget on Tuesday.")
    assert result["method"] == "lexical"
    assert result["grounding_score"] == 1.0
    assert result["review_status"] == "provisional"
    assert result["approved_by"] is None
    assert result["_spec"] is None          # Gemini is not recorded as the checker


def test_gemini_pass_names_gemini_as_approver(tmp_path: Path):
    cfg, reg, run_log, pm = _setup(tmp_path)
    engine = GroundingEngine(cfg, reg, run_log, pm)
    rec = sample_record(key="2026-08-14-008")
    result = engine.check_record(rec, spec=None)
    assert result["review_status"] == "verified"
    assert result["approved_by"]["provider"] == "gemini"


def test_review_status_rule():
    assert review_status_for(0.9, "gemini-search", 0.5) == "verified"
    assert review_status_for(0.5, "gemini-search", 0.5) == "verified"
    assert review_status_for(0.4, "gemini-search", 0.5) == "pending_review"
    assert review_status_for(1.0, "lexical", 0.5) == "provisional"
    assert review_status_for(1.0, "web", 0.5) == "provisional"
    assert review_status_for(0.0, "web", 0.5) == "pending_review"
    assert review_status_for(None, "gemini-search", 0.5) == "pending_review"
