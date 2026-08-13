"""Tests for src.schema."""

from __future__ import annotations

from src.schema import validate_record, word_count

from .conftest import sample_record


def test_valid_record_passes():
    rec = sample_record()
    assert validate_record(rec, min_words=100) == []


def test_missing_field_fails():
    rec = sample_record()
    del rec["outlook"]
    errors = validate_record(rec, min_words=100)
    assert any("outlook" in e for e in errors)


def test_short_body_fails_min_words():
    rec = sample_record()
    rec["background"] = "too short"
    errors = validate_record(rec, min_words=500)
    assert any("word count" in e for e in errors)


def test_bad_entity_type_fails():
    rec = sample_record()
    rec["entities"] = [{"type": "banana", "name": "x", "relation": "y"}]
    errors = validate_record(rec, min_words=100)
    assert any("banana" in e for e in errors)


def test_word_count_counts_body_only():
    rec = sample_record()
    wc = word_count(rec)
    assert wc >= 100
    assert isinstance(wc, int)
