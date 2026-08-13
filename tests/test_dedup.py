"""Tests for src.dedup."""

from __future__ import annotations

from src.dedup import (entity_overlap, is_exact_duplicate, normalize_title,
                       similarity_flags, title_hash, token_overlap, url_hash)

from .conftest import sample_record


def test_normalize_title_lowercases_and_strips():
    assert normalize_title("  RAG vs. Vector DBs!  ") == "rag vs vector dbs"


def test_hashes_are_stable_and_distinct():
    t = "A benchmark for agentic AI"
    assert title_hash(t) == title_hash("  A Benchmark for Agentic AI  ")
    assert title_hash(t) != title_hash("A totally different title")
    assert len(url_hash("https://x.com/a")) == 16


def test_token_overlap():
    assert token_overlap("hello world", "hello world") == 1.0
    assert token_overlap("hello world", "goodbye moon") == 0.0
    assert token_overlap("hello world foo", "hello world bar") == 2 / 4


def test_exact_duplicate_registry():
    registry = {url_hash("https://x.com/a"): {"id": "x"}}
    assert is_exact_duplicate(registry, "any title", "https://x.com/a")
    assert is_exact_duplicate(registry, "any title", "https://x.com/b") is False
    assert is_exact_duplicate({}, "any title", "") is False


def test_similarity_flags_over_threshold():
    recent = [sample_record(title="OpenAI releases new agent tooling")]
    res = similarity_flags(recent, "OpenAI releases new agent tooling", threshold=0.3)
    assert res["duplicate"] is True
    assert res["score"] >= 0.3


def test_entity_overlap():
    recent = [sample_record()]
    hits = entity_overlap(recent, [{"name": "RAG"}, {"name": "Unknown"}])
    assert hits == ["rag"]


def test_entity_overlap_empty():
    assert entity_overlap([sample_record()], []) == []
