"""information-hub — raw LLM HTTP clients (llm layer).

Low-level request builders for the two provider API formats:

  - ``openai`` format — POST ``/chat/completions`` (Groq, OpenRouter)
  - ``google`` format — POST ``:generateContent`` (Gemini), optionally with
    the ``google_search`` grounding tool

These functions are transport-only: they send prompts and return parsed JSON
(or, for non-JSON google calls, text + grounding chunks).  They know nothing
about budget/rotation — that is the job of :mod:`src.llm.providers`.

Role: phase collect (generation) and phase check (search verification) —
consumed by ``llm.providers.ProviderManager``.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

UA = {"User-Agent": "information-hub/0.3 (research aggregator)"}

# provider name -> API base url for openai-format chat completions
OPENAI_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# google-format generateContent url
GOOGLE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# statuses that trigger rotating to another provider/model
RETRYABLE = (429, 500, 502, 503, 504)


class ProviderError(RuntimeError):
    """Raised on any provider call failure; carries an optional HTTP status
    and, for rate-limit errors, the server's suggested wait (seconds)."""

    def __init__(self, message: str, status_code: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def call_openai(spec: Any, key: str, system: str, user: str,
                json_mode: bool = True, max_tokens: int = 4096,
                temperature: float = 0.4) -> dict[str, Any]:
    """Call an OpenAI-compatible ``/chat/completions`` endpoint.

    Args:
        spec:   ModelSpec (uses ``base_url`` and ``model``).
        key:    provider API key.
        system: system prompt.
        user:   user prompt.
        json_mode: request ``response_format: json_object`` (when supported).
        max_tokens / temperature: generation config.

    Returns:
        A tuple ``(parsed_json, tokens_used)`` where ``tokens_used`` is the
        provider-reported total token count (0 when the API omits usage).

    Raises:
        ProviderError: on network failure, HTTP error, or bad response.
    """
    if not spec.base_url:
        raise ProviderError("no base_url for openai-format provider")
    body: dict[str, Any] = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(f"{spec.base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {key}", **UA},
                             json=body, timeout=60)
    except requests.RequestException as e:
        raise ProviderError(f"network error: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(
            f"HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
            retry_after=_parse_retry_after(resp, resp.text),
        )
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise ProviderError("empty model content")
        tokens = _extract_openai_usage(data)
        return _parse_json_tolerant(content), tokens
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as e:
        raise ProviderError(f"bad response: {e}") from e


def call_google(spec: Any, key: str, system: str, user: str,
                json_mode: bool = True, search_tool: str | None = None,
                max_tokens: int = 4096, temperature: float = 0.4) -> dict[str, Any]:
    """Call the Gemini ``:generateContent`` endpoint.

    Args:
        spec:   ModelSpec (uses ``model``).
        key:    Gemini API key.
        system / user: prompts.
        json_mode: request ``responseMimeType: application/json``.
        search_tool: if set (e.g. ``"google_search"``), attaches the web-search
            grounding tool so claims can be verified against the web.
        max_tokens / temperature: generation config.

    Returns:
        A tuple ``(result, tokens_used)`` — in json_mode ``result`` is the
        parsed JSON object; otherwise ``{"text": ..., "grounding": [...]}``.
        ``tokens_used`` is the provider-reported total token count.

    Raises:
        ProviderError: on network failure, HTTP error, or non-JSON response.
    """
    url = GOOGLE_URL.format(model=spec.model, key=key)
    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    if search_tool:
        payload["tools"] = [{"google_search": {}}]
    try:
        resp = requests.post(url, json=payload, timeout=60)
    except requests.RequestException as e:
        raise ProviderError(f"network error: {e}") from e
    if resp.status_code != 200:
        raise ProviderError(
            f"HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
            retry_after=_parse_retry_after(resp, resp.text),
        )
    data = resp.json()
    text = _extract_google_text(data)
    if not text:
        raise ProviderError("empty response content")
    tokens = _extract_google_usage(data)
    if json_mode:
        try:
            return _parse_json_tolerant(text), tokens
        except json.JSONDecodeError as e:
            raise ProviderError(f"non-JSON response: {text[:200]}") from e
    return {"text": text, "grounding": _extract_grounding(data)}, tokens


def _parse_retry_after(resp: Any, body: str) -> float | None:
    """Extract a server-suggested wait (seconds) for a rate-limit error.

    Checks, in order: ``Retry-After`` header (seconds or HTTP-date),
    ``x-ratelimit-reset`` header (epoch seconds), and a ``"try again in Ns"``
    phrase in the response body (Groq-style quota errors).
    """
    import re
    h = resp.headers.get("Retry-After")
    if h:
        try:
            return float(h)
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:
                when = parsedate_to_datetime(h)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                pass
    reset = resp.headers.get("x-ratelimit-reset")
    if reset:
        try:
            epoch = float(reset)
            return max(0.0, epoch - time.time())
        except ValueError:
            pass
    m = re.search(r"try again in ([\d.]+)s", body, re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _extract_openai_usage(data: dict[str, Any]) -> int:
    """Total tokens from an OpenAI-format response (0 when omitted)."""
    try:
        return int(data["usage"]["total_tokens"])
    except (KeyError, TypeError, ValueError):
        return 0


def _extract_google_usage(data: dict[str, Any]) -> int:
    """Total tokens from a Gemini response (0 when omitted)."""
    try:
        return int(data["usageMetadata"]["totalTokenCount"])
    except (KeyError, TypeError, ValueError):
        return 0


def _parse_json_tolerant(text: str) -> dict[str, Any]:
    """Parse JSON from model text, stripping ```json fences if present.

    Falls back to slicing the first ``{`` … ``}`` block so models without
    JSON mode (which may wrap output in prose) still work.  Only objects are
    accepted — a top-level array is rejected so callers get a dict.
    """
    text = text.strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # strip markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
        else:
            # slice first balanced {...} block
            start = text.find("{")
            if start < 0:
                raise
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        result = json.loads(text[start:i + 1])
                        break
            else:
                raise
    if not isinstance(result, dict):
        raise ProviderError(f"model returned non-object JSON ({type(result).__name__})")
    return result


def _extract_google_text(data: dict[str, Any]) -> str:
    """Pull the concatenated text from a Gemini response payload."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_grounding(data: dict[str, Any]) -> list[dict[str, str]]:
    """Parse groundingMetadata → [{url, title}] from a google response."""
    try:
        meta = data["candidates"][0]["groundingMetadata"]
        chunks = meta.get("groundingChunks", [])
        out = []
        for c in chunks:
            web = c.get("web", {})
            if web.get("uri"):
                out.append({"url": web["uri"], "title": web.get("title", "")})
        return out
    except (KeyError, IndexError):
        return []
