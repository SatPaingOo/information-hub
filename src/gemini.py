"""information-hub — Gemini REST client (free tier).

Calls the Gemini generateContent endpoint with JSON structured output
(application/json), retrying with backoff up to cfg.retries.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, model: str, api_key: str, temperature: float = 0.4,
                 max_output_tokens: int = 2048, retries: int = 2):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Send a chat request and return parsed JSON object."""
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._post(payload)
            except GeminiError as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(2 ** attempt)  # backoff
        raise GeminiError(f"Gemini request failed after {self.retries + 1} attempts: {last_err}")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = _GENERATE_URL.format(model=self.model, key=self.api_key)
        try:
            resp = requests.post(url, json=payload, timeout=60)
        except requests.RequestException as e:
            raise GeminiError(f"network error: {e}") from e
        if resp.status_code != 200:
            raise GeminiError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = _extract_text(data)
        if not text:
            raise GeminiError("empty response content")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise GeminiError(f"model returned non-JSON: {text[:200]}") from e


def _extract_text(data: dict[str, Any]) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        return ""


def mock_generate_json(model: str, api_key: str, temperature: float,
                       max_output_tokens: int, retries: int,
                       system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Stand-in used by --mock mode: deterministic content without an API key.

    Produces a minimal but schema-shaped JSON object so the full pipeline
    (store/indexer/views/registry) can be verified offline.
    """
    from src.schema import deep_dive_item
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
    }
