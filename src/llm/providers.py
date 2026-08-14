"""information-hub — self-managing multi-provider layer (llm layer).

Manages the AI providers by role:

  - ``collect`` — Groq / OpenRouter free models generate deep-dives
  - ``check``   — Gemini (google_search grounding) verifies claims

The manager handles model discovery (OpenRouter free models at runtime),
health pings, per-run budgets, rotation on 429/5xx, and full provenance
logging of every pick/call/rotate.  Raw HTTP transport lives in
:mod:`src.llm.clients`.

Role: both phases — consumed by ``main`` and ``quality.grounding``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from src.config import Config, ProviderConfig
from src.llm.clients import (OPENAI_BASES, RETRYABLE, call_google, call_openai,
                             ProviderError)
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

UA = {"User-Agent": "information-hub/0.3 (research aggregator)"}

# HTTP statuses treated as transient (rate limits / upstream hiccups) —
# the model stays eligible for the next pick instead of being marked down.
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)


def _should_mark_down(status_code: int | None) -> bool:
    """True if this error status permanently disables the model for the run.

    Hard 4xx (decommissioned model, bad request) → down (rotate).
    Transient 429/5xx → stay up (may succeed on the next attempt).
    """
    if status_code is None:
        return True
    return status_code >= 400 and status_code not in TRANSIENT_STATUSES


@dataclass
class ModelSpec:
    """A concrete provider+model pair selected for a call.

    Attributes:
        provider:      provider name (groq / openrouter / gemini).
        model:         model id.
        fmt:           API format — ``"openai"`` or ``"google"``.
        supports_json: whether the model supports native JSON mode.
        search_tool:   google_search tool id for check providers, else None.
        base_url:      openai-format endpoint base (used when fmt=openai).
        keys:          API keys available for this provider.
    """

    provider: str
    model: str
    fmt: str = "openai"
    supports_json: bool = True
    search_tool: str | None = None
    base_url: str = ""
    keys: list[str] = field(default_factory=list)


class ProviderManager:
    """Discovers, health-checks and rotates provider models (self-managing).

    Args:
        cfg:      global configuration (providers + budgets).
        registry: storage registry (provider health/budget tracking).
        run_log:  structured run-log writer (provenance events).
        mock:     offline mode — no network, deterministic outputs.
        mock_generator: callable used in mock mode to fake model output.
    """

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
        # provider -> unix ts until which we skip it (rate-limit cooldown).
        # When a provider's free pool is 429-rate-limited, ALL its models are
        # affected — so we back off the whole provider and rotate to another.
        self._provider_cooldown_until: dict[str, float] = {}
        # provider -> consecutive 429 count (to mark a provider down)
        self._provider_429_count: dict[str, int] = {}
        # per-model consecutive 429s within this run (temporary down)
        self._model_429_count: dict[str, int] = {}

    # ---- model discovery ------------------------------------------------
    def models_for_role(self, role: str) -> list[ModelSpec]:
        """All enabled provider models for a role (in config order).

        Mock mode: env keys are not required (auto-enable all configured).
        Real mode: providers without keys are auto-disabled and logged.
        Models marked down on a previous day are re-enabled (fresh attempt).
        """
        self.registry.reset_health_if_stale()
        specs: list[ModelSpec] = []
        for p in self.cfg.providers.values():
            if not p.enabled or p.role != role:
                continue
            if not self.mock and not p.env_keys():
                self.log.event(role, "auto_disabled", provider=p.name,
                               status="skip", detail="no API key configured")
                continue
            if p.format == "openai":
                base = p.base_url or OPENAI_BASES.get(p.name, "")
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
        """Resolve the model list for a provider (config or auto-discovered)."""
        if p.discover == "free_models":
            if p.name not in self._discovered:
                self._discovered[p.name] = self._discover_openrouter_free()
            return self._discovered[p.name]
        return list(p.models)

    def _discover_openrouter_free(self) -> list[str]:
        """Fetch OpenRouter free models (pricing==0), JSON-capable first.

        Records each model's JSON-mode capability in the registry so
        ``models_for_role`` can pick ``supports_json`` and fall back to the
        tolerant prompt-JSON path for models without native JSON mode.
        """
        if self.mock:
            for m in ("qwen/qwen-2.5-7b-instruct:free", "liquid/lfm-2.5-2.6b:free"):
                self.registry.set_provider_json_support("openrouter", m, True)
            return ["qwen/qwen-2.5-7b-instruct:free", "liquid/lfm-2.5-2.6b:free"]
        try:
            resp = requests.get(
                "https://openrouter.ai/api/v1/models", headers=UA, timeout=20)
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
        free.sort(key=lambda x: (not x[1], x[0]))  # JSON-capable first
        result: list[str] = []
        for mid, json_ok in free:
            self.registry.set_provider_json_support("openrouter", mid, json_ok)
            result.append(mid)
        self.log.event("collect", "discovered_models", provider="openrouter",
                       detail=f"{len(result)} free models")
        return result

    # ---- selection -------------------------------------------------------
    def _provider_available(self, provider: str) -> bool:
        """True if this provider is not in a rate-limit cooldown window."""
        until = self._provider_cooldown_until.get(provider, 0.0)
        if until > time.monotonic():
            return False
        return True

    def pick_collect(self, collection: str) -> ModelSpec | None:
        """Pick the best healthy collect model (deep-dive generation).

        Returns the model with the fewest calls among healthy models still
        within budget, skipping providers in a rate-limit cooldown, or None
        when every collect model is down/exhausted/cooldown.
        """
        self._phase = "collect"
        for spec in self._ranked_collect():
            if not self._provider_available(spec.provider):
                continue
            if self._within_budget(spec) and self.registry.provider_healthy(spec.provider, spec.model):
                self.log.event("collect", "pick_model", collection=collection,
                               provider=spec.provider, model=spec.model)
                return spec
        self.log.event("collect", "no_provider_available", collection=collection,
                       status="skip", detail="all collect models down/budget-exhausted/rate-limited")
        return None

    def pick_check(self) -> ModelSpec | None:
        """Pick the healthy check provider (Gemini search grounding)."""
        self._phase = "check"
        for spec in self.models_for_role("check"):
            if not self._provider_available(spec.provider):
                continue
            if self.registry.provider_healthy(spec.provider, spec.model):
                self.log.event("check", "pick_model", provider=spec.provider,
                               model=spec.model)
                return spec
        self.log.event("check", "no_provider_available", status="skip",
                       detail="no healthy check provider (or rate-limited)")
        return None

    def _ranked_collect(self) -> list[ModelSpec]:
        """Collect models sorted by fewest calls first (load balancing)."""
        specs = self.models_for_role("collect")
        return sorted(specs, key=lambda s: (
            self.registry.provider_calls(s.provider, s.model),
            self.registry.provider_items(s.provider, s.model),
        ))

    def _within_budget(self, spec: ModelSpec) -> bool:
        """True if this model has not exceeded its per-run calls/items budget."""
        p = self.cfg.providers.get(spec.provider)
        if not p:
            return False
        return (self.registry.provider_calls(spec.provider, spec.model) < p.max_calls
                and self.registry.provider_items(spec.provider, spec.model) < p.max_items)

    # ---- calls ------------------------------------------------------------
    def generate(self, spec: ModelSpec, system_prompt: str,
                 user_prompt: str, items: int = 1) -> dict[str, Any]:
        """Generate a deep-dive via the selected collect model (JSON output)."""
        if self.mock:
            return self.mock_generator(system_prompt, user_prompt)
        start = time.monotonic()
        key = self._next_key(spec)
        try:
            if spec.fmt == "google":
                data = call_google(spec, key, system_prompt, user_prompt,
                                   json_mode=True)
            else:
                data = call_openai(spec, key, system_prompt, user_prompt,
                                   json_mode=spec.supports_json)
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               items=items, latency_ms=latency)
            self.log.event("collect", "call_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency)
            return data
        except ProviderError as e:
            self._record_failure(spec, e.status_code, phase="collect")
            raise
        except Exception as e:  # defensive — never crash the pipeline
            self.registry.record_provider_failure(spec.provider, spec.model, mark_down=True)
            self.log.event("collect", "call_error", provider=spec.provider,
                           model=spec.model, status="error", detail=str(e))
            raise ProviderError(f"unexpected error: {e}") from e

    def verify(self, spec: ModelSpec, system_prompt: str,
               user_prompt: str) -> dict[str, Any]:
        """Verify claims with the Gemini search-grounding check provider."""
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
            data = call_google(spec, key, system_prompt, user_prompt,
                               json_mode=True, search_tool=spec.search_tool)
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               latency_ms=latency)
            self.log.event("check", "verify_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency)
            return data
        except ProviderError as e:
            self._record_failure(spec, e.status_code, phase="check")
            raise
        except Exception as e:  # defensive — never crash the pipeline
            self.registry.record_provider_failure(spec.provider, spec.model, mark_down=True)
            self.log.event("check", "verify_error", provider=spec.provider,
                           model=spec.model, status="error", detail=str(e))
            raise ProviderError(f"unexpected error: {e}") from e

    def ping(self, spec: ModelSpec) -> bool:
        """Cheap health ping (max_tokens=1). Returns True if responsive."""
        if self.mock:
            return True
        try:
            if spec.fmt == "google":
                call_google(spec, self._next_key(spec), "ping", "Reply ok.",
                            json_mode=False, max_tokens=1)
            else:
                call_openai(spec, self._next_key(spec), "ping", "Reply ok.",
                            json_mode=False, max_tokens=1)
            return True
        except ProviderError:
            return False

    # ---- key rotation within a provider ----------------------------------
    def _next_key(self, spec: ModelSpec) -> str:
        """Round-robin over a provider's keys for this call."""
        keys = spec.keys or [""]
        idx = self._key_index.get(spec.provider, 0)
        self._key_index[spec.provider] = (idx + 1) % len(keys)
        return keys[idx]

    # ---- failure handling --------------------------------------------------
    def _record_failure(self, spec: ModelSpec, status_code: int | None,
                        phase: str) -> None:
        """Handle a failed provider call: cooldown/down + provenance log.

        429/5xx (transient): put the whole provider into a cooldown window so
        the next pick rotates to a different provider; after a few consecutive
        429s the model is also marked down for this run.
        Hard 4xx (decommissioned/bad config): mark the model down immediately.
        """
        transient = status_code in TRANSIENT_STATUSES or status_code is None
        if transient:
            # whole provider cooldown (~2 min) — its free pool is rate-limited
            self._provider_cooldown_until[spec.provider] = \
                time.monotonic() + 120
            key = f"{spec.provider}/{spec.model}"
            n = self._model_429_count.get(key, 0) + 1
            self._model_429_count[key] = n
            mark_down = n >= 3  # hammering the same rate-limited model is futile
        else:
            mark_down = True
        self.registry.record_provider_failure(spec.provider, spec.model,
                                              mark_down=mark_down)
        self.log.event(phase, "call_error", provider=spec.provider,
                       model=spec.model, status="error",
                       detail=f"HTTP {status_code}: {spec.provider} "
                              f"{'cooldown' if transient else 'down'} "
                              f"(mark_down={mark_down})")
