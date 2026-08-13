"""information-hub — seed data generator.

Creates the initial sample dataset (structure သရုပ်ပြ) so the repo ships
with real-looking Obsidian-ready content + index + registry.

Run:  python -m src.seed
"""

from __future__ import annotations

import datetime as dt
import json

from src.config import Config
from src.indexer import Indexer
from src.registry import Registry
from src.schema import validate_record, word_count
from src.store import Store

_TOPIC_BODY = (
    "This is a representative sample deep-dive. It exists to demonstrate the "
    "data structure, layer classification, entity linking, and related-item "
    "edges that the daily pipeline produces automatically. "
)
_AI_BODY_1 = (
    "The paper introduces a new evaluation framework for agentic systems, "
    "moving beyond single-turn benchmarks toward long-horizon tasks that "
    "require planning, tool use, and recovery from errors. Early results show "
    "that current frontier models handle routine sub-tasks well but degrade "
    "sharply when a task requires sustained multi-step reasoning with "
    "intermediate feedback. The authors argue that composite scoring across "
    "planning, execution, and self-correction provides a more useful signal "
    "than headline accuracy numbers. "
)
_AI_BODY_2 = (
    "Open-source tooling for retrieval-augmented generation continues to "
    "consolidate around a few dominant patterns: chunk-then-embed pipelines, "
    "graph-based knowledge stores, and increasingly small on-device models. "
    "The trade-off between retrieval quality and latency remains the central "
    "engineering question, and recent releases push harder on hybrid "
    "sparse-plus-dense retrieval to close that gap. Teams evaluating these "
    "stacks should benchmark against their own corpus rather than relying on "
    "published leaderboards. "
)
_MM_BODY = (
    "Myanmar's digital economy is moving in two directions at once: "
    "expanding mobile-first financial services on the ground, and a policy "
    "environment that remains uncertain around data governance and foreign "
    "technology partnerships. Connectivity investment continues in major "
    "urban centers, while rural adoption grows through low-cost devices and "
    "messaging-based services. Observers note that regulatory clarity will "
    "determine whether the current momentum translates into durable "
    "infrastructure or remains concentrated in a small set of services. "
)


def _rec(key: str, topic: str, region: str, ctype: str, categories: list[str],
         title: str, tldr: str, background: str, analysis: list[dict],
         entities: list[dict], tags: list[str], source_name: str,
         source_url: str) -> dict:
    date = key[:10]
    rec = {
        "id": f"info:item:{topic}:{region}:{key}",
        "key": key,
        "date": date,
        "content_type": ctype,
        "topic": topic,
        "region": region,
        "categories": categories,
        "source": {"name": source_name, "url": source_url, "type": "sample"},
        "title": title,
        "tldr": tldr,
        "background": background,
        "analysis": analysis,
        "key_facts": [
            "Sample dataset shipped with the repository to demonstrate structure.",
            "Daily pipeline regenerates these files from Gemini output.",
            "Classification layers are queryable via data/collections/index/.",
        ],
        "implications": [
            "Readers can follow related items through stable item IDs.",
            "Entity notes accumulate backlinks as new items are published.",
        ],
        "outlook": "The daily workflow extends this dataset automatically.",
        "entities": entities,
        "tags": tags + ["seed"],
        "related_items": [],
        "word_count": 0,
    }
    rec["word_count"] = word_count(rec)
    errors = validate_record(rec, min_words=50)
    if errors:
        raise ValueError(f"seed record invalid: {errors}")
    return rec


def build_seed_records() -> list[dict]:
    """Three sample deep-dives across regions/topics, linked via entities."""
    ai_rag = {"type": "concept", "name": "Retrieval-Augmented Generation", "relation": "uses"}
    ai_agents = {"type": "concept", "name": "Agentic AI", "relation": "evaluates"}
    ent_openai = {"type": "company", "name": "OpenAI", "relation": "mentioned"}
    ent_anthropic = {"type": "company", "name": "Anthropic", "relation": "mentioned"}
    ent_myanmar = {"type": "concept", "name": "Myanmar Digital Economy", "relation": "analyzes"}
    ent_gov = {"type": "concept", "name": "Data Governance", "relation": "discusses"}

    return [
        _rec(
            key="2026-08-13-001", topic="ai-ml", region="global", ctype="briefing",
            categories=["research"],
            title="A new benchmark for long-horizon agentic AI reasoning",
            tldr="A new evaluation framework measures agents on planning, tool use, "
                 "and recovery — and finds frontier models still degrade on long tasks.",
            background=_TOPIC_BODY + _AI_BODY_1,
            analysis=[
                {"heading": "What changed",
                 "content": _AI_BODY_1 + " The benchmark publishes per-phase scores, "
                                           "so model comparisons are no longer a single number."},
                {"heading": "Why it matters",
                 "content": "As agents move into production workflows, evaluation that "
                            "matches real task structure is the gating factor for adoption."},
                {"heading": "What to watch",
                 "content": "Whether the major labs adopt the suite as a release gate, "
                            "and how scores shift across model generations."},
            ],
            entities=[ai_agents, ai_rag, ent_openai, ent_anthropic],
            tags=["benchmark", "agents"],
            source_name="arXiv",
            source_url="https://arxiv.org/abs/2608.00001",
        ),
        _rec(
            key="2026-08-13-002", topic="ai-ml", region="global", ctype="briefing",
            categories=["research", "open-source"],
            title="Open-source retrieval stacks narrow the gap on enterprise RAG",
            tldr="Hybrid sparse-plus-dense retrieval and graph stores are closing "
                 "the quality gap with proprietary RAG pipelines.",
            background=_TOPIC_BODY + _AI_BODY_2,
            analysis=[
                {"heading": "The shift",
                 "content": _AI_BODY_2 + " Vector databases now ship hybrid search by "
                                         "default rather than as an add-on."},
                {"heading": "Enterprise angle",
                 "content": "Companies can now achieve competitive retrieval quality "
                            "without commercial licensing, changing the build-vs-buy calculus."},
                {"heading": "Watch for",
                 "content": "Benchmarks that include latency and cost, not just accuracy, "
                            "and growing adoption of graph-native retrieval."},
            ],
            entities=[ai_rag, ent_openai],
            tags=["rag", "vector-db", "open-source"],
            source_name="GitHub Trending",
            source_url="https://github.com/topics/machine-learning",
        ),
        _rec(
            key="2026-08-13-003", topic="geopolitics", region="myanmar", ctype="digest",
            categories=["policy", "industry"],
            title="Myanmar's digital economy: connectivity grows while governance lags",
            tldr="Mobile-first finance and connectivity expand across Myanmar, but "
                 "unclear data-governance rules cap foreign technology partnerships.",
            background=_TOPIC_BODY + _MM_BODY,
            analysis=[
                {"heading": "On the ground",
                 "content": _MM_BODY + " Messaging-based commerce is the fastest-growing "
                                       "segment, especially outside Yangon."},
                {"heading": "Policy tension",
                 "content": "Ambiguity around data residency and licensing creates "
                            "friction for international platforms and fintech investors."},
                {"heading": "Outlook",
                 "content": "The next 12 months of regulatory announcements will shape "
                            "whether digital services scale nationally or stay niche."},
            ],
            entities=[ent_myanmar, ent_gov],
            tags=["digital-economy", "fintech"],
            source_name="Sample RSS",
            source_url="https://example.com/myanmar-digital",
        ),
    ]


def main() -> None:
    cfg = Config.load()
    store = Store(cfg.storage.data_dir)
    registry = Registry(cfg.storage.data_dir / "collections" / "registry")
    indexer = Indexer(cfg.storage.data_dir)

    records = build_seed_records()
    run_ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # reset the registry so seed is self-contained
    for f in registry.dir.iterdir():
        f.unlink()
    registry = Registry(cfg.storage.data_dir / "collections" / "registry")

    for rec in records:
        layer = rec["topic"]
        store.write_record(rec, layer)
        registry.record_item(rec, status="seed", gemini_calls=0, validated=True)

    store.write_raw_run(run_ts, records)
    indexer.rebuild(store.iter_records())
    registry.mark_run([r["id"] for r in records], quota_used=len(records))
    registry.save()

    print(f"Seeded {len(records)} records at {run_ts}")
    for rec in records:
        print(f"  {rec['id']} — {rec['title'][:60]}")


if __name__ == "__main__":
    main()
