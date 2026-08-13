"""Tests for src.render.markdown — Obsidian markdown renderers."""

from __future__ import annotations

from src.render.markdown import (daily_index_markdown, entity_markdown,
                                 record_to_markdown, taxonomy_note_markdown)

from ..conftest import sample_record


def test_record_to_markdown_has_frontmatter_and_wikilinks():
    rec = sample_record()
    md = record_to_markdown(rec)
    assert md.startswith("---")
    assert f"id: \"{rec['id']}\"" in md
    assert "[[RAG]]" in md
    assert "[[OpenAI]]" in md
    assert "## Related" in md
    assert rec["title"] in md


def test_daily_index_markdown_lists_items():
    md = daily_index_markdown("2026-08-14", [sample_record()])
    assert "# Daily Hub — 2026-08-14" in md
    assert "[[info:item:ai-ml:global:2026-08-14-001]]" in md


def test_entity_markdown_backlinks():
    md = entity_markdown("RAG", "concept", [
        {"id": "info:item:ai-ml:global:2026-08-14-001", "title": "T", "date": "2026-08-14"},
    ])
    assert "# RAG" in md
    assert "2026-08-14" in md
    assert "backlink_count: 1" in md


def test_taxonomy_note_markdown():
    md = taxonomy_note_markdown("ai-ml", "topic", children=["llm"], parents=["ai"],
                                items=[sample_record()],
                                related_nodes=[("myanmar", "relates")])
    assert "# ai-ml" in md
    assert "[[llm]]" in md
    assert "[[myanmar]]" in md
