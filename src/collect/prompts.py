"""information-hub — prompt builders (collect layer).

Constructs the system + user prompts sent to the LLM providers:

  - ``build_select_prompt`` — editorial selector: which candidates to publish
  - ``build_deep_dive_prompt`` — deep-dive writer: full structured record

The prompts embed policies (priority/exclude), collection defaults, source
full-text, dedup flags and the enforced JSON schema so the model output can
be validated deterministically downstream.

Role: phase collect — consumed by ``main.run_collect``.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import CollectionConfig, Config
from src.models.candidate import Candidate
from src.storage.registry import Registry

from .dedup import similarity_flags

SYSTEM_SELECT = (
    "You are the editorial selector for a structured daily intelligence feed. "
    "Choose which candidate stories to publish today based on the priority "
    "policy list, the per-collection quota, and duplicate flags. "
    "Return ONLY JSON: {\"selected\": [0-based candidate indices], \"reason\": \"...\"}"
)

SYSTEM_DEEP_DIVE = (
    "You write deep-dive intelligence briefings in English. Each item is a "
    "structured JSON object. You MUST include at least 3 analysis subsections "
    "and reach the requested word count. Output valid JSON only, matching the "
    "exact schema keys provided."
)

PROMPT_VERSION = "v3.1"


def build_select_prompt(cfg: Config, collection: CollectionConfig,
                        candidates: list[Candidate],
                        recent_records: list[dict[str, Any]],
                        priority_text: str, exclude_text: str,
                        registry: Registry | None = None) -> str:
    """Build the editorial-selection prompt for a collection.

    Each candidate is annotated with a ``DUPLICATE``/``new`` flag (exact
    registry match or similarity vs recent records) so the selector never
    re-publishes existing content.

    Returns:
        A single user prompt string for the selector call.
    """
    lines = [
        f"Collection: {collection.name}",
        f"Content type: {collection.content_type}",
        f"Quota: max {collection.max_daily_items} item(s) for this collection",
        f"Global cap: {cfg.storage.max_daily_items_total} items this run",
        "",
        "Priority policies (higher weight = prefer):",
        priority_text,
        "Exclude policies (match = never select):",
        exclude_text,
        "",
        "Candidates:",
    ]
    for i, c in enumerate(candidates):
        seen = registry.has_seen(c.title, c.url) if registry else False
        sim = similarity_flags(recent_records, c.title, c.summary,
                               threshold=cfg.content.similarity_threshold)
        dup_flag = "DUPLICATE" if (seen or sim["duplicate"]) else "new"
        # keep per-candidate text short — the select call must be token-cheap
        # (free-tier TPM: a long select prompt burns the budget before any
        # deep-dive runs)
        lines.append(
            f"[{i}] ({dup_flag}) {c.title[:120]!r} | {c.source['name']!r} "
            f"| {c.summary[:120]!r}"
        )
    lines.append(
        "Return the 0-based indices of the stories to publish, ranked. "
        "Do not select duplicates. Respect the quota."
    )
    return "\n".join(lines)


def build_deep_dive_prompt(cfg: Config, collection: CollectionConfig,
                           candidate: Candidate, fulltext: str,
                           known_entities: list[str],
                           related_items: list[str],
                           policy_text: str) -> str:
    """Build the deep-dive generation prompt for one candidate story.

    Embeds the source full-text, collection classification, editorial
    policies, known entity names (for link consistency), related item ids
    and the exact JSON schema the model must fill.

    Returns:
        A single user prompt string for the deep-dive call.
    """
    return (
        f"Write a deep-dive briefing for this story.\n"
        f"Collection: {collection.name}\n"
        f"Content type: {collection.content_type}\n"
        f"Topics: {collection.topics}  Regions: {collection.regions}\n"
        f"Categories: {collection.categories}\n"
        f"Target word count: {cfg.content.target_words[0]}-{cfg.content.target_words[1]} words "
        f"(minimum {cfg.content.min_words}).\n\n"
        f"Source: {candidate.source['name']} — {candidate.url}\n"
        f"Title: {candidate.title}\n"
        f"Feed summary: {candidate.summary[:1200]}\n"
        f"Article fulltext:\n{fulltext[:cfg.content.fulltext_max_chars]}\n\n"
        f"Known entity names to reuse when relevant: {known_entities}\n"
        f"Existing related item ids (link when relevant): {related_items}\n"
        f"Editorial priorities: {policy_text}\n\n"
        f"JSON schema (fill exactly):\n{_schema_instruction(collection)}\n"
        f"Set \"date\" to {candidate.date!r}, \"key\" to {candidate.key!r}, "
        f"\"id\" to {candidate.stable_id!r}.\n"
        f"List at least 2 entities and 2 related/plausible related_items, and "
        f"fill related_taxonomy with 1-3 taxonomy node names this story connects to "
        f"(e.g. regions, topics, categories from the known taxonomy)."
    )


def _schema_instruction(collection: CollectionConfig) -> str:
    """Render the deep-dive JSON schema as an instruction string."""
    return json.dumps({
        "id": "info:item:<topic>:<region>:<date>-<seq>",
        "key": "<YYYY-MM-DD-NNN>",
        "date": "<YYYY-MM-DD>",
        "content_type": collection.content_type,
        "topic": collection.topics[0] if collection.topics else "misc",
        "region": collection.regions[0] if collection.regions else "global",
        "categories": collection.categories,
        "source": {"name": "...", "url": "...", "type": "..."},
        "title": "...",
        "tldr": "2-3 sentence summary",
        "background": "context...",
        "analysis": [{"heading": "...", "content": "..."}],
        "key_facts": ["...", "..."],
        "implications": ["...", "..."],
        "outlook": "...",
        "entities": [{"type": "concept|company|model|person|product|region|organization|event", "name": "...", "relation": "..."}],
        "tags": ["..."],
        "related_items": ["info:item:..."],
        "related_taxonomy": [{"node": "<taxonomy node name>", "relation": "relates"}],
        "word_count": 700,
    }, indent=2)
