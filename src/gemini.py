"""information-hub — legacy Gemini helpers + mock generator.

V3: actual provider HTTP calls moved to src.providers (google/openai
formats + search grounding). This module keeps:
  - mock_generate_json   (deterministic offline output for --mock)
  - GeminiError          (backward-compat alias of ProviderError)
  - GeminiClient         (thin legacy wrapper over the check provider)

Tests that used the old GeminiClient signature should use ProviderManager.
"""

from __future__ import annotations

from typing import Any

from src.providers import ProviderError as GeminiError  # noqa: F401 (backward-compat)
from src.providers import _call_google, _call_openai    # noqa: F401 (generic callers)


class GeminiClient:
    """Legacy wrapper kept for compatibility with pre-V3 callers.

    Prefer ProviderManager (src.providers) in new code.
    """

    def __init__(self, model: str, key_manager: Any, temperature: float = 0.4,
                 max_output_tokens: int = 2048, retries: int = 2):
        self.model = model
        self.keys = key_manager
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        raise NotImplementedError(
            "GeminiClient is legacy — use ProviderManager (src.providers) instead"
        )


def mock_generate_json(model: str, api_key: str, temperature: float,
                       max_output_tokens: int, retries: int,
                       system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Stand-in used by --mock mode: deterministic content without an API key.

    Produces a minimal but schema-shaped JSON object so the full pipeline
    (store/indexer/views/registry) can be verified offline.
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
