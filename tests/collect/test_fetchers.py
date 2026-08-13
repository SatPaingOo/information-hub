"""Tests for src.collect.fetchers — source fetchers."""

from __future__ import annotations

from src.collect.fetchers import fetch_collection, fetch_hackernews, fetch_rss, _source_name
from src.models.candidate import Candidate


def test_source_name_maps_types():
    assert _source_name("arxiv", {}) == "arXiv"
    assert _source_name("github", {"topic": "ml"}) == "github:ml"
    assert _source_name("hackernews", {}) == "HackerNews"
    assert _source_name("rss", {"url": "https://www.example.com/feed"}) == "example.com"


def test_fetch_collection_empty_sources_returns_empty():
    candidates = fetch_collection("test", [], max_candidates=5)
    assert candidates == []


def test_fetch_collection_unknown_type_skipped():
    candidates = fetch_collection("test", [{"type": "unknown"}], max_candidates=5)
    assert candidates == []


def test_fetch_rss_invalid_url_returns_empty():
    assert fetch_rss("https://invalid.local/feed.xml") == []


def test_fetch_hackernews_returns_list():
    # never raises — returns a list (possibly empty on network failure)
    result = fetch_hackernews(max_results=1, timeout=1)
    assert isinstance(result, list)


def test_candidate_dict_shape():
    c = Candidate(collection="c", source={"name": "s", "url": "u", "type": "rss"},
                  title="t", url="u2")
    d = c.to_dict()
    assert d["collection"] == "c"
    assert d["source"]["name"] == "s"
