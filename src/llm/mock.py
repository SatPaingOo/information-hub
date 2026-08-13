"""information-hub — mock LLM generator (llm layer).

Deterministic offline stand-in for a provider call.  Used by ``--mock`` mode
so the full pipeline (store / indexer / registry / provenance) can be
verified without any API key or network access.

Role: phase collect (mock only) — consumed by ``llm.providers.ProviderManager``.
"""

from __future__ import annotations

from typing import Any


def mock_generate_json(model: str, api_key: str, temperature: float,
                       max_output_tokens: int, retries: int,
                       system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Return a minimal schema-shaped JSON object without calling an API.

    Args are accepted for signature compatibility with the real client; the
    returned dict is intentionally simple — enough for pipeline testing but
    not a real deep-dive.

    Returns:
        A dict with ``title``, ``tldr``, ``background``, ``analysis``,
        ``key_facts``, ``implications``, ``outlook``, ``entities``, ``tags``,
        ``related_items``, ``related_taxonomy`` keys.
    """
    text = user_prompt[:2000]
    return {
        "title": "Mock deep-dive: " + text.split("\n")[0][:80],
        "tldr": "This is mock output generated without an API key.",
        "background": "Mock background context generated locally for pipeline testing.",
        "analysis": [
            {"heading": "Key development", "content": "Mock analysis section one."},
            {"heading": "Context", "content": "Mock analysis section two."},
            {"heading": "What to watch", "content": "Mock analysis section three."},
        ],
        "key_facts": ["Mock fact one.", "Mock fact two.", "Mock fact three."],
        "implications": ["Mock implication for researchers.", "Mock implication for companies."],
        "outlook": "Mock outlook statement.",
        "entities": [{"type": "concept", "name": "MockEntity", "relation": "related"}],
        "tags": ["mock"],
        "related_items": [],
        "related_taxonomy": [],
    }
