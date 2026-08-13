"""information-hub — offline mock data (collect layer).

Deterministic stand-ins used by ``--mock`` mode so the collect phase runs
without network or API keys:

  - ``_mock_candidates``  fake candidate stories per collection
  - ``deep_dive_mock``    schema-shaped deep-dive record
  - ``select_mock``       local duplicate-aware selector

The mock content is intentionally long enough to pass the minimum-word
gate so the full validation → store → index → registry path is exercised.

Role: phase collect (mock only) — consumed by ``main.run_collect``.
"""

from __future__ import annotations

from typing import Any

from src.config import CollectionConfig, Config
from src.models.candidate import Candidate
from src.storage.registry import Registry

from .dedup import similarity_flags


def deep_dive_mock(cfg: Config, collection: CollectionConfig,
                   candidate: Candidate, fulltext: str,
                   known_entities: list[str], related_items: list[str],
                   policy_text: str) -> dict[str, Any]:
    """Deterministic schema-shaped record for a mock candidate (no API).

    Generates enough body text to pass the minimum-word-count gate so the
    full pipeline (validate → store → index → registry) can be verified.
    """
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
        "word_count": 0,  # patched by main._finalize_record
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
    """Build a deterministic multi-sentence mock body section."""
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
    """Build deterministic mock bullet facts/implications."""
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
    """Deterministic local selector for ``--mock`` mode.

    Picks the first fresh (non-duplicate) candidates up to the collection
    quota — mirroring the real Gemini selector without an API call.
    """
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


def mock_candidates(collection: CollectionConfig) -> list[Candidate]:
    """Deterministic fake candidates for ``--mock`` mode (no network)."""
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


def _candidate_topic(collection: CollectionConfig) -> str:
    """Default topic for a collection (first configured topic or 'misc')."""
    return collection.topics[0] if collection.topics else "misc"


def _candidate_region(collection: CollectionConfig) -> str:
    """Default region for a collection (first configured region or 'global')."""
    return collection.regions[0] if collection.regions else "global"
