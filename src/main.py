"""information-hub — pipeline orchestrator.

Usage:
    python -m src.main --mock                  # no API key; deterministic sample run
    python -m src.main                         # real run (GEMINI_API_KEY required)
    python -m src.main --collection ai-research
    python -m src.main --date 2026-08-14
    python -m src.main --limit 1               # max items this run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

from src.config import CollectionConfig, Config, GeminiConfig
from src.fulltext import extract as extract_fulltext
from src.gemini import GeminiClient, GeminiError
from src.dedup import similarity_flags
from src.indexer import Indexer
from src.registry import Registry
from src.schema import validate_record, word_count
from src.sources import Candidate, fetch_collection
from src.store import Store

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


def build_select_prompt(cfg: Config, collection: CollectionConfig,
                        candidates: list[Candidate],
                        recent_records: list[dict[str, Any]],
                        priority_text: str, exclude_text: str,
                        registry: Registry | None = None) -> str:
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
        lines.append(
            f"[{i}] ({dup_flag}) title={c.title!r} source={c.source['name']!r} "
            f"url={c.url} summary={c.summary[:300]!r}"
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
        f"List at least 2 entities and 2 related/plausible related_items."
    )


def _schema_instruction(collection: CollectionConfig) -> str:
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
        "entities": [{"type": "concept|company|model|person", "name": "...", "relation": "..."}],
        "tags": ["..."],
        "related_items": ["info:item:..."],
        "word_count": 700,
    }, indent=2)


def _today() -> str:
    return dt.date.today().isoformat()


# ---- selection (real = Gemini, mock = local scoring) -----------------
def select_via_gemini(gemini: GeminiClient, cfg: Config, collection: CollectionConfig,
                      candidates: list[Candidate],
                      recent_records: list[dict[str, Any]],
                      priority_text: str, exclude_text: str,
                      registry: Registry | None = None) -> list[int]:
    prompt = build_select_prompt(cfg, collection, candidates, recent_records,
                                 priority_text, exclude_text, registry)
    result = gemini.generate_json(SYSTEM_SELECT, prompt)
    selected = result.get("selected", [])
    return [int(i) for i in selected if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()]


def select_mock(cfg: Config, collection: CollectionConfig,
                candidates: list[Candidate],
                recent_records: list[dict[str, Any]],
                priority_text: str, exclude_text: str,
                registry: Registry | None = None) -> list[int]:
    """Deterministic local selection for --mock: prefer policy-priority match."""
    picked: list[int] = []
    for i, c in enumerate(candidates[:collection.max_candidates]):
        if len(picked) >= collection.max_daily_items:
            break
        if registry and registry.has_seen(c.title, c.url):
            continue
        sim = similarity_flags(recent_records, c.title, c.summary,
                               threshold=cfg.content.similarity_threshold)
        if sim["duplicate"]:
            continue
        picked.append(i)  # mock: take first fresh candidates deterministically
    return picked


# ---- deep-dive --------------------------------------------------------
def deep_dive_via_gemini(gemini: GeminiClient, cfg: Config, collection: CollectionConfig,
                         candidate: Candidate, fulltext: str,
                         known_entities: list[str], related_items: list[str],
                         policy_text: str) -> dict[str, Any]:
    prompt = build_deep_dive_prompt(cfg, collection, candidate, fulltext,
                                    known_entities, related_items, policy_text)
    return gemini.generate_json(SYSTEM_DEEP_DIVE, prompt)


def deep_dive_mock(cfg: Config, collection: CollectionConfig,
                   candidate: Candidate, fulltext: str,
                   known_entities: list[str], related_items: list[str],
                   policy_text: str) -> dict[str, Any]:
    """Deterministic record for --mock mode (schema-shaped, no API).

    Generates enough body text to pass the minimum-word-count gate so the
    full pipeline (validate → store → index → registry) can be verified.
    """
    topic = collection.topics[0] if collection.topics else "misc"
    region = collection.regions[0] if collection.regions else "global"
    return {
        "id": f"info:item:{topic}:{region}:{candidate.key}",
        "key": candidate.key,
        "date": candidate.date,
        "content_type": collection.content_type,
        "topic": topic,
        "region": region,
        "categories": collection.categories,
        "source": candidate.source,
        "title": candidate.title,
        "tldr": f"Mock summary of: {candidate.title}",
        "background": _mock_section(candidate, collection, "background"),
        "analysis": [
            {"heading": "Key development", "content": _mock_section(candidate, collection, "dev")},
            {"heading": "Context and significance", "content": _mock_section(candidate, collection, "context")},
            {"heading": "What to watch", "content": _mock_section(candidate, collection, "outlook")},
        ],
        "key_facts": _mock_bullets(candidate, "fact", 4),
        "implications": _mock_bullets(candidate, "implication", 3),
        "outlook": _mock_section(candidate, collection, "outlook"),
        "entities": [
            {"type": "concept", "name": "MockEntity", "relation": "related"},
            {"type": "company", "name": "MockLabs", "relation": "mentioned"},
        ],
        "tags": ["mock"],
        "related_items": related_items[:2],
        "word_count": 0,  # patched by _finalize_record
    }


_SENTENCES = (
    "This development in the {topic} domain draws on a body of prior work "
    "that has shaped how practitioners approach the problem space over time. "
    "Observers point to several converging signals, including shifts in tooling, "
    "workflow, and the expectations of the people who depend on these systems "
    "every day. {title}. ",
    "What makes this noteworthy is not any single announcement, but the way it "
    "connects to an ongoing series of changes in the {region} ecosystem. "
    "Teams adopting these patterns tend to see measurable differences in how "
    "quickly they can move from idea to production, though adoption is rarely "
    "uniform across organizations. ",
    "A careful reading of the source material suggests the authors intend for "
    "this to be a starting point rather than a conclusion. The supporting "
    "details reinforce the broader narrative that {region} and international "
    "stakeholders are converging on similar priorities, even where their "
    "immediate incentives differ. ",
    "Experts caution against overinterpreting early results, noting that "
    "reproducibility, governance, and long-term maintenance remain open "
    "questions. Still, the direction of travel is clear: incremental "
    "improvements compound, and the systems that integrate them well tend to "
    "outperform those that do not. ",
    "For {collection_name}, the practical implication is that teams should "
    "track follow-up activity, benchmark new tooling against their existing "
    "baselines, and revisit assumptions that may no longer hold. "
    "Documentation, community discussion, and upstream releases will all be "
    "indicators of how seriously the claims should be taken. ",
)


def _mock_section(candidate: Candidate, collection: CollectionConfig, kind: str) -> str:
    parts = []
    for i, s in enumerate(_SENTENCES):
        parts.append(s.format(
            topic=(collection.topics[0] if collection.topics else "this field"),
            region=(collection.regions[0] if collection.regions else "global"),
            collection_name=collection.name,
            title=candidate.title,
        ))
        if i % 2 == 1:
            parts.append("This ties directly to the story reported by "
                         f"{candidate.source['name']}, and is consistent with "
                         "the wider evidence base collected in this feed.")
    return " ".join(parts)


def _mock_bullets(candidate: Candidate, kind: str, count: int) -> list[str]:
    return [
        f"Mock {kind} {n}: derived from {candidate.source['name']} report on "
        f"'{candidate.title}', consistent with the surrounding evidence base."
        for n in range(1, count + 1)
    ]


def _finalize_record(record: dict[str, Any], candidate: Candidate,
                     collection: CollectionConfig) -> dict[str, Any]:
    """Ensure id/key/date/word_count are correct before storing."""
    record["key"] = candidate.key
    record["date"] = candidate.date
    record["content_type"] = collection.content_type
    record["source"] = candidate.source
    topic = record.get("topic") or (collection.topics[0] if collection.topics else "misc")
    region = record.get("region") or (collection.regions[0] if collection.regions else "global")
    record["topic"] = topic
    record["region"] = region
    record["id"] = f"info:item:{topic}:{region}:{candidate.key}"
    record["word_count"] = word_count(record)
    return record


# ---- runner ------------------------------------------------------------
def run(mock: bool, collection_filter: str | None, date_override: str | None,
        limit: int | None) -> int:
    cfg = Config.load()
    registry = Registry(cfg.storage.data_dir / "collections" / "registry")
    store = Store(cfg.storage.data_dir)
    indexer = Indexer(cfg.storage.data_dir)

    known_entities = _known_entities(indexer, store)
    known_ids = {r["id"] for r in store.iter_records()}

    if mock:
        gemini: Any = _MockBackend()
    else:
        if not cfg.gemini.api_key:
            print("ERROR: GEMINI_API_KEY not set (use --mock for offline run)", file=sys.stderr)
            return 2
        gemini = GeminiClient(cfg.gemini.model, cfg.gemini.api_key,
                              cfg.gemini.temperature, cfg.gemini.max_output_tokens,
                              cfg.gemini.retries)

    priority_text = _policy_text(cfg.policies.get("priority", []))
    exclude_text = _policy_text(cfg.policies.get("exclude", []))

    run_date = date_override or _today()

    collections = cfg.enabled_collections()
    if collection_filter:
        collections = [c for c in collections if c.name == collection_filter]
        if not collections:
            print(f"No enabled collection named {collection_filter!r}", file=sys.stderr)
            return 2

    run_records: list[dict[str, Any]] = []
    total_quota = cfg.storage.max_daily_items_total
    if limit:
        total_quota = min(total_quota, limit)

    seq = registry.next_sequence(run_date)  # per-date counter (stable keys)

    for collection in collections:
        if total_quota <= 0:
            break
        try:
            if mock:
                candidates = _mock_candidates(collection)
            else:
                candidates = fetch_collection(collection.name, collection.sources,
                                              collection.max_candidates)
        except Exception as e:
            registry.record_fetch(collection.name, 0, 0, error=str(e))
            print(f"[{collection.name}] fetch error: {e}", file=sys.stderr)
            continue
        if not candidates:
            registry.record_fetch(collection.name, 0, 0)
            print(f"[{collection.name}] no candidates")
            continue

        recent = store.iter_records()
        selected = gemini.select(cfg, collection, candidates, recent,
                                 priority_text, exclude_text, registry)
        if not selected:
            registry.record_fetch(collection.name, len(candidates), 0)
            print(f"[{collection.name}] nothing selected")
            continue

        published = 0
        for idx in selected:
            if total_quota <= 0:
                break
            candidate = candidates[idx]
            # stable key/date — date is an attribute, not a folder
            candidate.key = f"{run_date}-{seq:03d}"
            candidate.date = run_date
            candidate.stable_id = f"info:item:{_candidate_topic(collection)}:" \
                                  f"{_candidate_region(collection)}:{candidate.key}"
            seq += 1

            fulltext = extract_fulltext(candidate.url, cfg.content.fulltext_max_chars) \
                if not mock else candidate.summary
            related = _find_related(candidate, store.iter_records())

            record = None
            for attempt in range(cfg.gemini.retries + 1):
                try:
                    record = gemini.deep_dive(cfg, collection, candidate, fulltext,
                                              known_entities, related, priority_text)
                    record = _finalize_record(record, candidate, collection)
                    errors = validate_record(record, cfg.content.min_words)
                    if not errors:
                        break
                    print(f"[{collection.name}] attempt {attempt + 1} invalid: {errors[:2]}")
                    record = None
                except GeminiError as e:
                    print(f"[{collection.name}] gemini error: {e}", file=sys.stderr)
                    record = None
            if record is None:
                continue

            store.write_record(record, _primary_layer_value(collection, record))
            registry.record_item(record, status="published",
                                 gemini_calls=attempt + 1, validated=True)
            known_ids.add(record["id"])
            for e in record.get("entities", []):
                known_entities.add(e["name"])
            run_records.append(record)
            total_quota -= 1
            published += 1
            print(f"[{collection.name}] published {record['key']} — {record['title'][:60]}")

        registry.record_fetch(collection.name, len(candidates), published)

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if run_records:
        store.write_raw_run(timestamp, run_records)
        indexer.rebuild(store.iter_records())
    registry.mark_run([r["id"] for r in run_records], quota_used=len(run_records))
    registry.save()

    print(f"Done: {len(run_records)} item(s) published this run.")
    return 0


def _primary_layer_value(collection: CollectionConfig, record: dict[str, Any]) -> str:
    layer = collection.primary_layer
    if layer == "region":
        return record.get("region", "global")
    if layer == "content_type":
        return record.get("content_type", "article")
    return record.get("topic", "misc")  # default: topic


def _candidate_topic(collection: CollectionConfig) -> str:
    return collection.topics[0] if collection.topics else "misc"


def _candidate_region(collection: CollectionConfig) -> str:
    return collection.regions[0] if collection.regions else "global"


def _known_entities(indexer: Indexer, store: Store) -> set[str]:
    out: set[str] = set()
    entity_dir = indexer.index_dir / "by-entity"
    if entity_dir.exists():
        for f in entity_dir.glob("*.json"):
            out.add(f.stem)
    return out


def _find_related(candidate: Candidate, records: list[dict[str, Any]]) -> list[str]:
    """Related items = recent records sharing topic/collection (mock-friendly)."""
    out: list[str] = []
    for r in sorted(records, key=lambda x: x.get("date", ""), reverse=True):
        if r.get("topic") == candidate.collection or r.get("collection") == candidate.collection:
            out.append(r["id"])
        if len(out) >= 3:
            break
    return out


def _policy_text(policies: list[dict[str, Any]]) -> str:
    if not policies:
        return "(none)"
    return "\n".join(
        f"- {p.get('type')}: {p.get('value')} (weight {p.get('weight', 1)})"
        for p in policies
    )


_MOCK_STORIES = {
    "myanmar-news": [
        ("Myanmar economy ministry announces new investment reforms",
         "https://example.com/mm-economy-1"),
        ("Rural connectivity initiative launched across Myanmar",
         "https://example.com/mm-rural-1"),
        ("Parliamentary debate on digital policy in Myanmar",
         "https://example.com/mm-policy-1"),
    ],
    "ai-research": [
        ("A New Benchmark for Agentic AI Reasoning",
         "https://arxiv.org/abs/2608.00001"),
        ("Scaling Laws for Multimodal LLMs at Inference Time",
         "https://arxiv.org/abs/2608.00002"),
        ("Efficient Retrieval-Augmented Generation with Sparse Indexes",
         "https://arxiv.org/abs/2608.00003"),
    ],
    "us-tech": [
        ("OpenAI releases new agent tooling for developers",
         "https://example.com/us-openai-1"),
        ("Open-source vector database gains momentum",
         "https://example.com/us-oss-1"),
        ("US startup funding surges in AI infrastructure",
         "https://example.com/us-funding-1"),
    ],
}


def _mock_candidates(collection: CollectionConfig) -> list[Candidate]:
    """Deterministic fake candidates for --mock mode (no network)."""
    stories = _MOCK_STORIES.get(collection.name, [])
    out: list[Candidate] = []
    for title, url in stories:
        out.append(Candidate(
            collection=collection.name,
            source={"name": "MockSource", "url": url, "type": "mock"},
            title=title,
            url=url,
            summary=f"Mock feed summary for {title}.",
        ))
    return out


class _MockBackend:
    """Adapter that makes select/deep_dive work identically offline."""

    def select(self, cfg: Config, collection: CollectionConfig,
               candidates: list[Candidate], recent: list[dict[str, Any]],
               priority_text: str, exclude_text: str,
               registry: Registry | None = None) -> list[int]:
        return select_mock(cfg, collection, candidates, recent,
                           priority_text, exclude_text, registry)

    def deep_dive(self, cfg: Config, collection: CollectionConfig,
                  candidate: Candidate, fulltext: str,
                  known_entities: list[str], related: list[str],
                  policy_text: str) -> dict[str, Any]:
        return deep_dive_mock(cfg, collection, candidate, fulltext,
                              known_entities, related, policy_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="information-hub pipeline")
    parser.add_argument("--mock", action="store_true",
                        help="run offline with deterministic mock data (no API key)")
    parser.add_argument("--collection", default=None,
                        help="run only this collection name")
    parser.add_argument("--date", default=None,
                        help="date override (YYYY-MM-DD) — record date")
    parser.add_argument("--limit", type=int, default=None,
                        help="max total items for this run")
    args = parser.parse_args()
    sys.exit(run(mock=args.mock, collection_filter=args.collection,
                 date_override=args.date, limit=args.limit))


if __name__ == "__main__":
    main()
