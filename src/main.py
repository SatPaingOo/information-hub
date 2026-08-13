"""information-hub — pipeline orchestrator (V3).

Two-phase operation:
  Phase collect  — Groq/OpenRouter free models generate deep-dives (self-managing)
  Phase check    — Gemini search-grounding verifies today's claims → score + review

Usage:
    python -m src.main --mock --phase collect    # offline generation (no API keys)
    python -m src.main --phase collect          # real collect
    python -m src.main --phase check            # verify today's items (Gemini search)
    python -m src.main --phase both             # collect then check (default)
    python -m src.main --collection ai-research --force
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from src.config import CollectionConfig, Config
from src.dedup import similarity_flags
from src.fulltext import extract as extract_fulltext
from src.indexer import Indexer
from src.logging_util import RunLog, setup_logging
from src.providers import ProviderError, ProviderManager
from src.quality import GroundingEngine
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

PROMPT_VERSION = "v3.1"
SCHEMA_VERSION = "v3"


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
        f"List at least 2 entities and 2 related/plausible related_items, and "
        f"fill related_taxonomy with 1-3 taxonomy node names this story connects to "
        f"(e.g. regions, topics, categories from the known taxonomy)."
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
        "related_taxonomy": [{"node": "<taxonomy node name>", "relation": "relates"}],
        "word_count": 700,
    }, indent=2)


def _today() -> str:
    return dt.date.today().isoformat()


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---- deep-dive mock (deterministic, candidate-aware) --------------------
def deep_dive_mock(cfg: Config, collection: CollectionConfig,
                   candidate: Candidate, fulltext: str,
                   known_entities: list[str], related_items: list[str],
                   policy_text: str) -> dict[str, Any]:
    """Deterministic record for --mock mode (schema-shaped, no API)."""
    topic = _candidate_topic(collection)
    region = _candidate_region(collection)
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
        "related_taxonomy": [
            {"node": topic, "relation": "primary_topic"},
            {"node": region, "relation": "primary_region"},
        ],
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


def _finalize_record(record: dict[str, Any], candidate: Candidate,
                     collection: CollectionConfig) -> dict[str, Any]:
    """Ensure id/key/date/word_count are correct before storing."""
    record["key"] = candidate.key
    record["date"] = candidate.date
    record["content_type"] = collection.content_type
    record["source"] = candidate.source
    topic = record.get("topic") or _candidate_topic(collection)
    region = record.get("region") or _candidate_region(collection)
    record["topic"] = topic
    record["region"] = region
    record["id"] = f"info:item:{topic}:{region}:{candidate.key}"
    record["word_count"] = word_count(record)
    return record


# ---- phase: collect ------------------------------------------------------
def run_collect(cfg: Config, registry: Registry, store: Store, indexer: Indexer,
                run_log: RunLog, logger: Any, pm: ProviderManager, *,
                mock: bool, collection_filter: str | None, date_override: str | None,
                limit: int | None, force: bool) -> int:
    known_entities = _known_entities(indexer, store)
    priority_text = _policy_text(cfg.policies.get("priority", []))
    exclude_text = _policy_text(cfg.policies.get("exclude", []))

    run_date = date_override or _today()

    collections = cfg.collections_by_priority()
    if collection_filter:
        collections = [c for c in collections if c.name == collection_filter]
        if not collections:
            print(f"No enabled collection named {collection_filter!r}", file=sys.stderr)
            return 2
    due: list[CollectionConfig] = []
    for c in collections:
        if force or registry.is_due(c.name, run_date, c.frequency):
            due.append(c)
        else:
            logger.info("[%s] not due (frequency=%s) — skipping (use --force)", c.name, c.frequency)
    collections = due

    run_records: list[dict[str, Any]] = []
    total_quota = cfg.storage.max_daily_items_total
    if limit:
        total_quota = min(total_quota, limit)
    seq = registry.next_sequence(run_date)

    for collection in collections:
        if total_quota <= 0:
            break
        try:
            candidates = _mock_candidates(collection) if mock else \
                fetch_collection(collection.name, collection.sources,
                                 collection.max_candidates)
        except Exception as e:
            registry.record_fetch(collection.name, 0, 0, error=str(e))
            logger.error("[%s] fetch error: %s", collection.name, e)
            continue
        if not candidates:
            registry.record_fetch(collection.name, 0, 0)
            logger.info("[%s] no candidates", collection.name)
            continue

        recent = store.iter_records()

        # selector (mock = local heuristic; real = collect provider)
        if mock:
            selected = select_mock(cfg, collection, candidates, recent,
                                   priority_text, exclude_text, registry)
        else:
            spec = pm.pick_collect(collection.name)
            if spec is None:
                registry.record_fetch(collection.name, len(candidates), 0)
                logger.warning("[%s] no collect provider available", collection.name)
                continue
            prompt = build_select_prompt(cfg, collection, candidates, recent,
                                         priority_text, exclude_text, registry)
            try:
                result = pm.generate(spec, SYSTEM_SELECT, prompt, items=0)
                selected = [int(i) for i in result.get("selected", [])]
            except ProviderError as e:
                logger.error("[%s] select failed: %s", collection.name, e)
                registry.record_fetch(collection.name, len(candidates), 0)
                continue

        if not selected:
            registry.record_fetch(collection.name, len(candidates), 0)
            logger.info("[%s] nothing selected", collection.name)
            continue

        published = 0
        for idx in selected:
            if total_quota <= 0:
                break
            candidate = candidates[idx]
            candidate.key = f"{run_date}-{seq:03d}"
            candidate.date = run_date
            candidate.stable_id = f"info:item:{_candidate_topic(collection)}:" \
                                  f"{_candidate_region(collection)}:{candidate.key}"
            seq += 1

            fulltext = extract_fulltext(candidate.url, cfg.content.fulltext_max_chars) \
                if not mock else candidate.summary
            related = _find_related(candidate, store.iter_records())

            record = None
            gen_provider, gen_model = "mock", "mock"
            for attempt in range(cfg.gemini.retries + 1):
                try:
                    if mock:
                        record = deep_dive_mock(cfg, collection, candidate, fulltext,
                                                known_entities, related, priority_text)
                        gen_provider, gen_model = "mock", "mock-generator"
                    else:
                        spec = pm.pick_collect(collection.name)
                        if spec is None:
                            logger.warning("[%s] no collect provider for deep-dive",
                                           collection.name)
                            break
                        gen_provider, gen_model = spec.provider, spec.model
                        prompt = build_deep_dive_prompt(
                            cfg, collection, candidate, fulltext,
                            known_entities, related, priority_text)
                        record = pm.generate(spec, SYSTEM_DEEP_DIVE, prompt)
                    record = _finalize_record(record, candidate, collection)
                    errors = validate_record(record, cfg.content.min_words)
                    if not errors:
                        break
                    logger.info("[%s] attempt %d invalid: %s",
                                collection.name, attempt + 1, errors[:2])
                    record = None
                except ProviderError as e:
                    logger.error("[%s] generate error: %s", collection.name, e)
                    record = None
            if record is None:
                continue

            record["provenance"] = {
                "generated_by": {"provider": gen_provider, "model": gen_model,
                                 "prompt_version": PROMPT_VERSION,
                                 "supports_json": True},
                "schema_version": SCHEMA_VERSION,
            }
            store.write_record(record, _primary_layer_value(collection, record))
            registry.record_item(record, status="published",
                                 gemini_calls=attempt + 1, validated=True,
                                 provider=gen_provider, model=gen_model)
            run_log.event("collect", "published", collection=collection.name,
                          item_id=record["id"], provider=gen_provider, model=gen_model,
                          detail=record["title"][:80])
            known_entities.update(e["name"] for e in record.get("entities", []))
            run_records.append(record)
            total_quota -= 1
            published += 1
            logger.info("[%s] published %s — %s", collection.name,
                        record["key"], record["title"][:60])

        registry.record_fetch(collection.name, len(candidates), published)
        registry.record_collection_run(collection.name, collection.frequency, run_date)

    timestamp = _utcnow()
    if run_records:
        store.write_raw_run(timestamp, run_records)
    indexer.rebuild(store.iter_records(), taxonomy=cfg.taxonomy, relations=cfg.relations)
    registry.mark_run([r["id"] for r in run_records], quota_used=len(run_records))
    registry.save()
    logger.info("collect done: %d item(s) published", len(run_records))
    return 0


# ---- phase: check ---------------------------------------------------------
def run_check(cfg: Config, registry: Registry, store: Store, indexer: Indexer,
              run_log: RunLog, logger: Any, pm: ProviderManager, *,
              mock: bool, date_override: str | None) -> int:
    run_date = date_override or _today()
    engine = GroundingEngine(cfg, registry, run_log, pm)
    records_today = [r for r in store.iter_records() if r.get("date") == run_date]
    if not records_today:
        logger.info("check: no items for %s", run_date)
        registry.save()
        return 0

    cap = cfg.quality.max_ai_verify_per_run
    checked = 0
    for rec in records_today[:cap]:
        spec = pm.pick_check()
        if spec is None:
            logger.warning("check: no healthy check provider — stopping")
            break
        result = engine.check_record(rec, spec)
        if result["grounding_score"] is None:
            logger.info("check: %s — skipped (%s)", rec["id"], result.get("reason"))
            continue
        rec["grounding"] = {
            "checked_by": {"provider": spec.provider, "model": spec.model,
                           "search_tool": spec.search_tool},
            "checked_at": _utcnow(),
            "grounding_score": result["grounding_score"],
            "claims_total": result["claims_total"],
            "claims_grounded": result["claims_grounded"],
            "sources_verified": result["sources_verified"],
            "method": result["method"],
        }
        status = result.get("review_status", "pending_review")
        rec["review"] = {
            "status": status,
            "approved_by": {"type": "ai", "provider": spec.provider,
                            "model": spec.model},
            "approved_at": _utcnow(),
        }
        store.write_record(rec, _layer_of_record(rec))
        logger.info("check: %s score=%s status=%s sources=%d",
                    rec["id"], result["grounding_score"], status,
                    len(result["sources_verified"]))
        checked += 1

    if checked:
        indexer.rebuild(store.iter_records(), taxonomy=cfg.taxonomy, relations=cfg.relations)
    registry.save()
    logger.info("check done: %d item(s) verified", checked)
    return 0


# ---- runner ------------------------------------------------------------
def run(mock: bool, phase: str, collection_filter: str | None,
        date_override: str | None, limit: int | None, force: bool) -> int:
    cfg = Config.load()
    registry = Registry(cfg.storage.data_dir / "collections" / "registry")
    store = Store(cfg.storage.data_dir)
    indexer = Indexer(cfg.storage.data_dir)
    run_date = date_override or _today()
    logger = setup_logging(cfg.storage.data_dir, run_date, phase)
    run_log = RunLog(registry.dir)

    pm = ProviderManager(cfg, registry, run_log, mock=mock)

    phases = [phase] if phase in ("collect", "check") else list(cfg.run.phases)
    if phase == "both":
        phases = ["collect", "check"]
    if phase == "both" and mock:
        phases = ["collect", "check"]  # mock both

    for p in phases:
        run_log.event(p, "phase_start")
        if p == "collect":
            rc = run_collect(cfg, registry, store, indexer, run_log, logger, pm,
                             mock=mock, collection_filter=collection_filter,
                             date_override=date_override, limit=limit, force=force)
            if rc != 0:
                return rc
        elif p == "check":
            run_check(cfg, registry, store, indexer, run_log, logger, pm,
                      mock=mock, date_override=date_override)
        run_log.event(p, "phase_end")
    return 0


def _primary_layer_value(collection: CollectionConfig, record: dict[str, Any]) -> str:
    layer = collection.primary_layer
    if layer == "region":
        return record.get("region", "global")
    if layer == "content_type":
        return record.get("content_type", "article")
    return record.get("topic", "misc")


def _layer_of_record(record: dict[str, Any]) -> str:
    return record.get("topic", "misc")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="information-hub pipeline")
    parser.add_argument("--mock", action="store_true",
                        help="run offline with deterministic mock data (no API keys)")
    parser.add_argument("--phase", default="both", choices=["collect", "check", "both"],
                        help="which phase to run (collect=generate, check=verify)")
    parser.add_argument("--collection", default=None,
                        help="run only this collection name")
    parser.add_argument("--date", default=None,
                        help="date override (YYYY-MM-DD) — record date")
    parser.add_argument("--limit", type=int, default=None,
                        help="max total items for this run")
    parser.add_argument("--force", action="store_true",
                        help="run collections even if not due (bypass frequency check)")
    args = parser.parse_args()
    sys.exit(run(mock=args.mock, phase=args.phase, collection_filter=args.collection,
                 date_override=args.date, limit=args.limit, force=args.force))


if __name__ == "__main__":
    main()
