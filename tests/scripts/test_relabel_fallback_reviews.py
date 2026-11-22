"""Tests for scripts/relabel_fallback_reviews.py — the one-off status repair."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "relabel_fallback_reviews.py"
_spec = importlib.util.spec_from_file_location("relabel_fallback_reviews", _PATH)
relabel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relabel)

NOW = "2026-11-22T00:00:00+00:00"


def _rec(method: str, score: float, status: str = "verified") -> dict:
    return {
        "id": "info:item:world:global:2026-09-01-001",
        "grounding": {"method": method, "grounding_score": score},
        "review": {"status": status,
                   "approved_by": {"type": "ai", "provider": method, "model": method},
                   "approved_at": "2026-09-01T02:00:00+00:00"},
    }


def test_lexical_verified_becomes_provisional_and_keeps_history():
    fixed = relabel.corrected_review(_rec("lexical", 1.0), 0.5, NOW)
    assert fixed["status"] == "provisional"
    assert fixed["approved_by"] is None and fixed["approved_at"] is None
    (old,) = fixed["history"]
    assert old["status"] == "verified"
    assert old["approved_by"]["provider"] == "lexical"      # old trail kept
    assert old["approved_at"] == "2026-09-01T02:00:00+00:00"
    assert old["changed_at"] == NOW and "lexical" in old["reason"]


def test_web_verified_becomes_provisional():
    assert relabel.corrected_review(_rec("web", 0.8), 0.5, NOW)["status"] == "provisional"


def test_gemini_verified_is_left_alone():
    assert relabel.corrected_review(_rec("gemini-search", 0.9), 0.5, NOW) is None


def test_already_correct_or_unchecked_is_left_alone():
    assert relabel.corrected_review(_rec("lexical", 0.0, "pending_review"), 0.5, NOW) is None
    assert relabel.corrected_review(_rec("lexical", 1.0, "provisional"), 0.5, NOW) is None
    assert relabel.corrected_review({"id": "x"}, 0.5, NOW) is None


def test_never_upgrades_a_status():
    """pending_review with a passing gemini score is a human/registry matter —
    the repair only removes over-claimed approvals."""
    assert relabel.corrected_review(_rec("gemini-search", 0.9, "pending_review"), 0.5, NOW) is None
