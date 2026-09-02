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
    # entity wikilinks point at the entity NOTE file (safe_name), not raw name
    assert "[[RAG]]" in md
    assert "[[OpenAI]]" in md
    assert "## Related" in md
    assert rec["title"] in md


def test_record_to_markdown_related_uses_resolved_notes():
    rec = sample_record()
    rec["related_items"] = ["info:item:ai-ml:global:2026-08-14-002"]
    rec["related_notes"] = ["2026-08-14-002-another-story"]
    md = record_to_markdown(rec)
    assert "[[2026-08-14-002-another-story]]" in md
    # unresolved item ids (no note) are NOT rendered as links
    assert "[[info:item:" not in md


def test_daily_index_markdown_lists_items():
    md = daily_index_markdown("2026-08-14", [sample_record()])
    assert "# Daily Hub — 2026-08-14" in md
    # item notes are flat <key>-<title-slug>.md → link to the filename
    assert "[[2026-08-14-001-sample-deep-dive-title]]" in md


def test_entity_markdown_backlinks():
    md = entity_markdown("RAG", "concept", [
        {"id": "info:item:ai-ml:global:2026-08-14-001",
         "note": "2026-08-14-001-sample-deep-dive-title",
         "title": "T", "date": "2026-08-14"},
    ])
    assert "# RAG" in md
    assert "2026-08-14" in md
    assert "backlink_count: 1" in md
    # backlink links to the item note filename
    assert "[[2026-08-14-001-sample-deep-dive-title]]" in md


def test_taxonomy_note_markdown():
    md = taxonomy_note_markdown("ai-ml", "topic", children=["llm"], parents=["ai"],
                                items=[sample_record()],
                                related_nodes=[("myanmar", "relates")])
    assert "# ai-ml" in md
    # taxonomy/entity node links use safe_name
    assert "[[llm]]" in md
    assert "[[myanmar]]" in md
    # item links use the item note filename
    assert "[[2026-08-14-001-sample-deep-dive-title]]" in md
