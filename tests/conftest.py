"""Shared test helpers — sample records and config fixtures."""

from __future__ import annotations

from typing import Any


def sample_record(key: str = "2026-08-14-001", title: str = "Sample deep dive title",
                  topic: str = "ai-ml", region: str = "global") -> dict[str, Any]:
    rec = {
        "id": f"info:item:{topic}:{region}:{key}",
        "key": key,
        "date": key[:10],
        "content_type": "briefing",
        "topic": topic,
        "region": region,
        "categories": ["research"],
        "source": {"name": "arXiv", "url": "https://arxiv.org/abs/2608.00001", "type": "arxiv"},
        "title": title,
        "tldr": "Two or three sentence tldr for the index and quick scans.",
        "background": " ".join(["background context sentence"] * 8),
        "analysis": [
            {"heading": "Key development",
             "content": " ".join(["analysis content about the key development"] * 10)},
            {"heading": "Context and significance",
             "content": " ".join(["analysis content about broader context"] * 10)},
            {"heading": "What to watch",
             "content": " ".join(["analysis content about forward signals"] * 10)},
        ],
        "key_facts": ["fact one", "fact two", "fact three"],
        "implications": ["implication for researchers", "implication for companies"],
        "outlook": " ".join(["outlook statement about expected trajectory"] * 6),
        "entities": [
            {"type": "concept", "name": "RAG", "relation": "uses"},
            {"type": "company", "name": "OpenAI", "relation": "mentioned"},
        ],
        "tags": ["sample"],
        "related_items": [],
        "word_count": 0,
    }
    from src.schema import word_count
    rec["word_count"] = word_count(rec)
    return rec
