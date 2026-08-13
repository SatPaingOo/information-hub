"""information-hub — pipeline orchestrator (CLI entry point).

Coordinates the two-phase daily pipeline:

  * Phase ``collect`` — source fetchers + self-managing LLM providers
    (Groq/OpenRouter free models) generate deep-dive records.
  * Phase ``check`` — the Gemini search-grounding provider verifies the
    claims of today's records (grounding score + review status).

All orchestration logic lives here; the heavy lifting is delegated to the
layered packages under ``src`` (collect / llm / quality / storage / render).

Usage:
    python -m src.main --mock --phase both      # offline, no API keys
    python -m src.main --phase collect          # real collect
    python -m src.main --phase check            # verify today's items
    python -m src.main --collection ai-research --force
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any

from src.collect.fulltext import extract as extract_fulltext
from src.collect.mock import (deep_dive_mock, mock_candidates, select_mock,
                              _candidate_topic, _candidate_region)
from src.collect.prompts import (PROMPT_VERSION, SYSTEM_DEEP_DIVE, SYSTEM_SELECT,
                                 build_deep_dive_prompt, build_select_prompt)
from src.collect.fetchers import fetch_collection
from src.config import CollectionConfig, Config
from src.llm.providers import ProviderError, ProviderManager
from src.models.candidate import Candidate
from src.models.schema import validate_record, word_count
from src.quality.grounding import GroundingEngine
from src.storage.indexer import Indexer
from src.storage.registry import Registry
from src.storage.store import Store
from src.utils.logging_util import RunLog, setup_logging

SCHEMA_VERSION = "v3"


def _today() -> str:
    """Today's date as ISO (used for record date when no override)."""
    return dt.date.today().isoformat()


def _utcnow() -> str:
    """Current UTC time as ISO-8601 seconds precision."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _finalize_record(record: dict[str, Any], candidate: Candidate,
                     collection: CollectionConfig) -> dict[str, Any]:
    """Normalise a model-produced record before storage.

    Fixes id/key/date/content_type/source/topic/region/word_count so the
    record is schema-valid and carries stable identifiers.
    """
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


def _policy_text(policies: list[dict[str, Any]]) -> str:
    """Render policy rules (priority/exclude) as prompt text."""
    if not policies:
        return "(none)"
    return "\n".join(
        f"- {p.get('type')}: {p.get('value')} (weight {p.get('weight', 1)})"
        for p in policies
    )


def _known_entities(indexer: Indexer, store: Store) -> set[str]:
    """Collect every entity name already seen (for link consistency)."""
    out: set[str] = set()
    entity_dir = indexer.index_dir / "by-entity"
    if entity_dir.exists():
        for f in entity_dir.glob("*.json"):
            out.add(f.stem)
    return out


def _find_related(candidate: Candidate, records: list[dict[str, Any]]) -> list[str]:
    """Related item ids = recent records sharing the candidate's topic."""
    out: list[str] = []
    for r in sorted(records, key=lambda x: x.get("date", ""), reverse=True):
        if r.get("topic") == candidate.collection or r.get("collection") == candidate.collection:
            out.append(r["id"])
        if len(out) >= 3:
            break
    return out


def _primary_layer_value(collection: CollectionConfig, record: dict[str, Any]) -> str:
    """Canonical physical folder layer value for a record."""
    layer = collection.primary_layer
    if layer == "region":
        return record.get("region", "global")
    if layer == "content_type":
        return record.get("content_type", "article")
    return record.get("topic", "misc")


def _layer_of_record(record: dict[str, Any]) -> str:
    """Topic-based layer for re-storing a record during check phase."""
    return record.get("topic", "misc")


# ---- phase: collect ------------------------------------------------------
def run_collect(cfg: Config, registry: Registry, store: Store, indexer: Indexer,
                run_log: RunLog, logger: Any, pm: ProviderManager, *,
                mock: bool, collection_filter: str | None, date_override: str | None,
                limit: int | None, force: bool) -> int:
    """Execute the collect phase: fetch → select → deep-dive → store.

    Respects collection priority order, frequency due-dates (unless forced),
    global item quota and per-provider budgets.  Stamps provenance
    (generated_by) on every record and logs all events to the run log.
    """
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
            candidates = mock_candidates(collection) if mock else \
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
    """Execute the check phase: verify today's records with Gemini search.

    Every record dated ``run_date`` is passed to the grounding engine which
    scores its claims, attaches cited sources, updates review status and the
    per-source reputation, then re-writes the enriched record.
    """
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
    """Top-level runner: load config, wire dependencies, execute phases."""
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


def main() -> None:
    """Parse CLI arguments and run the pipeline."""
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
