"""information-hub — self-managing multi-provider layer.

Roles:
  collect  — Groq / OpenRouter free models → deep-dive generation
  check    — Gemini (google_search grounding) → claim verification

Features:
  - auto-discover free OpenRouter models at runtime (no hardcode)
  - env-key-less providers are auto-disabled
  - health-check (cheap ping) + budget (calls/items per run) + rotation
  - 429/5xx → mark model down → next model → next provider → graceful skip
  - google / openai payload translation
  - every pick/call/rotate is logged to run-log.jsonl (full provenance)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from src.config import Config, ProviderConfig
from src.logging_util import RunLog
from src.registry import Registry

UA = {"User-Agent": "information-hub/0.3 (research aggregator)"}

# provider name -> API base url for openai-format chat completions
_OPENAI_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# google-format generateContent url
_GOOGLE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

_RETRYABLE = (429, 500, 502, 503, 504)


@dataclass
class ModelSpec:
    provider: str
    model: str
    fmt: str = "openai"
    supports_json: bool = True
    search_tool: str | None = None
    base_url: str = ""
    keys: list[str] = field(default_factory=list)


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderManager:
    def __init__(self, cfg: Config, registry: Registry, run_log: RunLog,
                 mock: bool = False,
                 mock_generator: Callable[[str, str], dict[str, Any]] | None = None):
        self.cfg = cfg
        self.registry = registry
        self.log = run_log
        self.mock = mock
        self.mock_generator = mock_generator or (lambda s, u: {"title": "mock"})
        self.mock_verify_result: dict[str, Any] | None = None
        self._key_index: dict[str, int] = {}
        self._discovered: dict[str, list[str]] = {}
        self._phase = "collect"

    # ---- model discovery ------------------------------------------------
    def models_for_role(self, role: str) -> list[ModelSpec]:
        """All enabled provider models for a role (in config order).

        Mock mode: env keys not required (auto-enable all configured).
        Real mode: providers without keys are auto-disabled.
        """
        specs: list[ModelSpec] = []
        for p in self.cfg.providers.values():
            if not p.enabled or p.role != role:
                continue
            if not self.mock and not p.env_keys():
                self.log.event(role, "auto_disabled", provider=p.name,
                               status="skip", detail="no API key configured")
                continue
            if p.format == "openai":
                base = p.base_url or _OPENAI_BASES.get(p.name, "")
            else:
                base = ""
            keys = p.env_keys() or (["mock-key"] if self.mock else [])
            models = self._models_for(p)
            for m in models:
                supports = self.registry.provider_model_stats(
                    p.name, m).get("supports_json", True)
                specs.append(ModelSpec(
                    provider=p.name, model=m, fmt=p.format,
                    supports_json=supports,
                    search_tool=p.search_tool,
                    base_url=base,
                    keys=keys,
                ))
        return specs

    def _models_for(self, p: ProviderConfig) -> list[str]:
        if p.discover == "free_models":
            if p.name not in self._discovered:
                self._discovered[p.name] = self._discover_openrouter_free()
            return self._discovered[p.name]
        return list(p.models)

    def _discover_openrouter_free(self) -> list[str]:
        """GET /api/v1/models → free (pricing==0) models, JSON-capable first."""
        if self.mock:
            return ["qwen/qwen-2.5-7b-instruct:free", "liquid/lfm-2.5-2.6b:free"]
        try:
            resp = requests.get(_OPENROUTER_MODELS_URL, headers=UA, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except (requests.RequestException, ValueError):
            self.log.event("collect", "discover_error", provider="openrouter",
                           status="error", detail="could not fetch model list")
            return []
        free: list[tuple[str, bool]] = []
        for m in data:
            pricing = m.get("pricing", {}) or {}
            is_free = pricing.get("prompt", "1") == "0" and pricing.get("completion", "1") == "0"
            if not is_free:
                continue
            mid = m.get("id", "")
            if not mid:
                continue
            params = m.get("supported_parameters", {}) or {}
            json_ok = "response_format" in params or "structured_outputs" in params
            free.append((mid, json_ok))
        # JSON-capable first, then the rest
        free.sort(key=lambda x: (not x[1], x[0]))
        result = [mid for mid, _ in free]
        self.log.event("collect", "discovered_models", provider="openrouter",
                       detail=f"{len(result)} free models")
        return result

    # ---- selection -------------------------------------------------------
    def pick_collect(self, collection: str) -> ModelSpec | None:
        """Pick the best healthy model for a collect (deep-dive) call."""
        self._phase = "collect"
        for spec in self._ranked_collect():
            if self._within_budget(spec) and self.registry.provider_healthy(spec.provider, spec.model):
                self.log.event("collect", "pick_model", collection=collection,
                               provider=spec.provider, model=spec.model)
                return spec
        self.log.event("collect", "no_provider_available", collection=collection,
                       status="skip", detail="all collect models down/budget-exhausted")
        return None

    def pick_check(self) -> ModelSpec | None:
        """Pick the healthy check provider (Gemini search grounding)."""
        self._phase = "check"
        for spec in self.models_for_role("check"):
            if self.registry.provider_healthy(spec.provider, spec.model):
                self.log.event("check", "pick_model", provider=spec.provider,
                               model=spec.model)
                return spec
        self.log.event("check", "no_provider_available", status="skip",
                       detail="no healthy check provider")
        return None

    def _ranked_collect(self) -> list[ModelSpec]:
        specs = self.models_for_role("collect")
        # fewest calls first (load balance); config order as tie-break
        return sorted(specs, key=lambda s: (
            self.registry.provider_calls(s.provider, s.model),
            self.registry.provider_items(s.provider, s.model),
        ))

    def _within_budget(self, spec: ModelSpec) -> bool:
        p = self.cfg.providers.get(spec.provider)
        if not p:
            return False
        return (self.registry.provider_calls(spec.provider, spec.model) < p.max_calls
                and self.registry.provider_items(spec.provider, spec.model) < p.max_items)

    # ---- calls ------------------------------------------------------------
    def generate(self, spec: ModelSpec, system_prompt: str,
                 user_prompt: str, items: int = 1) -> dict[str, Any]:
        """Deep-dive generation via the selected collect model (JSON)."""
        if self.mock:
            return self.mock_generator(system_prompt, user_prompt)
        start = time.monotonic()
        key = self._next_key(spec)
        try:
            if spec.fmt == "google":
                data = _call_google(spec, key, system_prompt, user_prompt,
                                    json_mode=True)
            else:
                data = _call_openai(spec, key, system_prompt, user_prompt,
                                    json_mode=spec.supports_json)
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               items=items, latency_ms=latency)
            self.log.event("collect", "call_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency)
            return data
        except ProviderError as e:
            self.registry.record_provider_failure(spec.provider, spec.model,
                                                  mark_down=e.status_code in _RETRYABLE)
            self.log.event("collect", "call_error", provider=spec.provider,
                           model=spec.model, status="error",
                           detail=f"HTTP {e.status_code}: {e}")
            raise

    def verify(self, spec: ModelSpec, system_prompt: str,
               user_prompt: str) -> dict[str, Any]:
        """Claim verification with Gemini search grounding."""
        if self.mock:
            return self.mock_verify_result or {
                "claims": [
                    {"text": "claim one", "grounded": True,
                     "source_url": "https://example.com/source-1",
                     "source_title": "Source One"},
                    {"text": "claim two", "grounded": True,
                     "source_url": "https://example.com/source-2",
                     "source_title": "Source Two"},
                    {"text": "claim three", "grounded": False,
                     "source_url": "", "source_title": ""},
                ],
                "reason": "mock verification",
            }
        start = time.monotonic()
        key = self._next_key(spec)
        try:
            data = _call_google(spec, key, system_prompt, user_prompt,
                                json_mode=True, search_tool=spec.search_tool)
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               latency_ms=latency)
            self.log.event("check", "verify_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency)
            return data
        except ProviderError as e:
            self.registry.record_provider_failure(spec.provider, spec.model,
                                                  mark_down=e.status_code in _RETRYABLE)
            self.log.event("check", "verify_error", provider=spec.provider,
                           model=spec.model, status="error",
                           detail=f"HTTP {e.status_code}: {e}")
            raise

    def ping(self, spec: ModelSpec) -> bool:
        """Cheap health ping (max_tokens=1). Returns True if responsive."""
        if self.mock:
            return True
        try:
            if spec.fmt == "google":
                _call_google(spec, self._next_key(spec), "ping", "Reply ok.",
                             json_mode=False, max_tokens=1)
            else:
                _call_openai(spec, self._next_key(spec), "ping", "Reply ok.",
                             json_mode=False, max_tokens=1)
            return True
        except ProviderError:
            return False

    # ---- key rotation within a provider ----------------------------------
    def _next_key(self, spec: ModelSpec) -> str:
        keys = spec.keys or [""]
        idx = self._key_index.get(spec.provider, 0)
        self._key_index[spec.provider] = (idx + 1) % len(keys)
        return keys[idx]


# ---- raw HTTP clients ------------------------------------------------------
def _call_openai(spec: ModelSpec, key: str, system: str, user: str,
                 json_mode: bool = True, max_tokens: int = 2048,
                 temperature: float = 0.4) -> dict[str, Any]:
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
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}",
                            status_code=resp.status_code)
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        raise ProviderError(f"bad response: {e}") from e


def _call_google(spec: ModelSpec, key: str, system: str, user: str,
                 json_mode: bool = True, search_tool: str | None = None,
                 max_tokens: int = 2048, temperature: float = 0.4) -> dict[str, Any]:
    url = _GOOGLE_URL.format(model=spec.model, key=key)
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
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}",
                            status_code=resp.status_code)
    data = resp.json()
    text = _extract_google_text(data)
    if not text:
        raise ProviderError("empty response content")
    if json_mode:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"non-JSON response: {text[:200]}") from e
    return {"text": text, "grounding": _extract_grounding(data)}


def _extract_google_text(data: dict[str, Any]) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
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
