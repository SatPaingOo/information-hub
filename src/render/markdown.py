"""information-hub — Obsidian markdown renderers (render layer).

Builds the human-readable ``.md`` views from canonical records:

  - ``record_to_markdown``      a deep-dive article (frontmatter + wikilinks)
  - ``daily_index_markdown``    per-day hub note listing that day's items
  - ``entity_markdown``         entity node note (backlinks)
  - ``taxonomy_note_markdown``  taxonomy node note (hierarchy + items)

These are *generated views* — the source of truth is the ``.json`` dataset.

Role: output layer — consumed by storage.store and storage.indexer.
"""

from __future__ import annotations

import json
from typing import Any

from src.storage.naming import record_filename, safe_name

_YAML_BOOL = {True: "true", False: "false"}


def record_to_markdown(record: dict[str, Any]) -> str:
    """Render one deep-dive record as an Obsidian-ready markdown file."""
    lines: list[str] = []
    lines.append("---")
    lines.append(f"id: \"{record['id']}\"")
    lines.append(f"key: \"{record['key']}\"")
    lines.append(f"date: {record['date']}")
    lines.append(f"content_type: {record['content_type']}")
    lines.append(f"topic: {record['topic']}")
    lines.append(f"region: {record['region']}")
    lines.append(f"categories: {_yaml_list(record['categories'])}")
    lines.append(f"source: \"{record['source']['name']}\"")
    lines.append(f"source_url: \"{record['source']['url']}\"")
    lines.append(f"word_count: {record['word_count']}")
    lines.append(f"tags: {_yaml_list(record['tags'])}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {record['title']}")
    lines.append("")
    lines.append(f"> [!summary] TL;DR — {record['tldr']}")
    lines.append("")
    lines.append("## Background")
    lines.append("")
    lines.append(record["background"])
    lines.append("")

    for section in record["analysis"]:
        lines.append(f"## {section['heading']}")
        lines.append("")
        lines.append(section["content"])
        lines.append("")

    lines.append("## Key facts")
    lines.append("")
    for fact in record["key_facts"]:
        lines.append(f"- {fact}")
    lines.append("")

    lines.append("## Implications")
    lines.append("")
    for imp in record["implications"]:
        lines.append(f"- {imp}")
    lines.append("")

    lines.append("## Outlook")
    lines.append("")
    lines.append(record["outlook"])
    lines.append("")

    if record["entities"]:
        lines.append("## Entities")
        lines.append("")
        for e in record["entities"]:
            # wikilink target = the entity note basename (entities/<type>/
            # <safe>.md) so the link resolves in Obsidian.
            lines.append(f"- [[{safe_name(e['name'])}]] — *{e['type']}* ({e['relation']})")
        lines.append("")

    if record["related_items"]:
        lines.append("## Related")
        lines.append("")
        # related_items are machine item IDs; the Obsidian links must point
        # at the item NOTE files, so main.py stamps ``related_notes`` with
        # the resolved filenames (record_filename).  IDs without a resolved
        # note (old records / invented by the model) are skipped.
        notes = record.get("related_notes") or []
        if notes:
            for rel in notes:
                lines.append(f"- [[{rel}]]")
        else:
            lines.append("_No linked notes yet — check the graph for emerging links._")
    else:
        lines.append("## Related")
        lines.append("")
        lines.append("_No related items yet — check the graph for emerging links._")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Source: [{record['source']['name']}]({record['source']['url']})*")
    return "\n".join(lines)


def daily_index_markdown(date: str, records: list[dict[str, Any]]) -> str:
    """Daily hub note listing that day's items with wikilinks."""
    lines = ["---", f"date: {date}", "type: daily-hub", "---", "",
             f"# Daily Hub — {date}", ""]
    if not records:
        lines.append("_No items published this day._")
        return "\n".join(lines)
    for rec in records:
        lines.append(f"## {rec['title']}")
        lines.append("")
        lines.append(f"- **Type**: {rec['content_type']} · **Topic**: {rec['topic']} · "
                     f"**Region**: {rec['region']}")
        lines.append(f"- **Source**: [{rec['source']['name']}]({rec['source']['url']})")
        lines.append(f"- **Note**: [[{record_filename(rec)}]]")
        lines.append(f"- **TL;DR**: {rec['tldr']}")
        lines.append("")
    return "\n".join(lines)


def entity_markdown(name: str, entity_type: str,
                    backlinks: list[dict[str, Any]]) -> str:
    """Entity node note listing every item that referenced it."""
    lines = ["---", f"name: \"{name}\"", f"entity_type: {entity_type}",
             f"backlink_count: {len(backlinks)}", "---", "",
             f"# {name}", "", f"*Type: {entity_type}*", "",
             "## Referenced by", ""]
    if not backlinks:
        lines.append("_No items yet._")
        return "\n".join(lines)
    for bl in sorted(backlinks, key=lambda b: b.get("date", ""), reverse=True):
        # bl['note'] = the referencing item note filename (set by indexer);
        # fall back to the item id for older callers.
        target = bl.get("note") or bl.get("id") or ""
        lines.append(f"- {bl['date']} · [[{target}]] — {bl['title']}")
    return "\n".join(lines)


def taxonomy_note_markdown(node: str, layer: str,
                           children: list[str],
                           parents: list[str],
                           items: list[dict[str, Any]],
                           related_nodes: list[tuple[str, str]]) -> str:
    """Taxonomy node note: hierarchy context + linked items + cross-layer edges."""
    lines = ["---", f"node: \"{node}\"", f"layer: {layer}",
             f"item_count: {len(items)}", "---", "",
             f"# {node}", "", f"*Taxonomy layer: {layer}*", ""]

    if parents:
        lines.append("## Parents")
        lines.append("")
        for p in parents:
            lines.append(f"- [[{safe_name(p)}]]")
        lines.append("")

    if children:
        lines.append("## Children")
        lines.append("")
        for c in children:
            lines.append(f"- [[{safe_name(c)}]]")
        lines.append("")

    if related_nodes:
        lines.append("## Cross-layer relations")
        lines.append("")
        for rel_node, rel_type in sorted(related_nodes):
            lines.append(f"- [[{safe_name(rel_node)}]] — *{rel_type}*")
        lines.append("")

    lines.append("## Items")
    lines.append("")
    if not items:
        lines.append("_No items yet._")
        return "\n".join(lines)
    for it in sorted(items, key=lambda i: i.get("date", ""), reverse=True):
        lines.append(f"- {it['date']} · [[{record_filename(it)}]] — {it['title']}")
    return "\n".join(lines)


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i, ensure_ascii=False) for i in items) + "]"
