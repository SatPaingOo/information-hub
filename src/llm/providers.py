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
from src.llm.clients import (OPENAI_BASES, call_google, call_openai,
                             ProviderError)
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

UA = {"User-Agent": "information-hub/0.3 (research aggregator)"}

# HTTP statuses treated as transient (rate limits / upstream hiccups) —
# transient failures put the whole provider into a persisted cooldown.
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)


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
        # per-model consecutive 429s within this run (temporary down).
        # Provider-level cooldown is PERSISTED in the registry (survives runs).
        self._model_429_count: dict[str, int] = {}

    # ---- model discovery ------------------------------------------------
    def models_for_role(self, role: str) -> list[ModelSpec]:
        """All enabled provider models for a role (in config order).

        Mock mode: env keys are not required (auto-enable all configured).
        Real mode: providers without keys are auto-disabled and logged.
        Models marked down on a previous day are re-enabled (fresh attempt).
        Daily provider token quotas are reset when the UTC day changed.
        """
        self.registry.reset_health_if_stale()
        self.registry.reset_provider_quotas_if_new_day()
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
    def _cooldown_active(self, until_iso: str | None) -> bool:
        """True when an ISO UTC cooldown deadline is still in the future."""
        if not until_iso:
            return False
        try:
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(until_iso)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < deadline
        except ValueError:
            return False

    def _provider_available(self, provider: str) -> bool:
        """True if the provider is not in a persisted rate-limit cooldown."""
        return not self._cooldown_active(
            self.registry.provider_cooldown_until(provider))

    def can_call(self, spec: ModelSpec, est_output_tokens: int = 0) -> bool:
        """Pre-call rate-limit gate (called BEFORE any HTTP request).

        Returns False when the provider OR this specific model is in a
        persisted cooldown, or its daily token/item budget would be exceeded
        by this call.
        """
        if not self._provider_available(spec.provider):
            return False
        if self._cooldown_active(
                self.registry.model_cooldown_until(spec.provider, spec.model)):
            return False
        cfg_p = self.cfg.providers.get(spec.provider)
        if not cfg_p:
            return False
        if self.registry.provider_items_used(spec.provider) >= cfg_p.max_daily_items:
            return False
        projected = self.registry.provider_tokens_used(spec.provider) + est_output_tokens
        return projected <= cfg_p.max_daily_tokens

    def pick_collect(self, collection: str, est_output_tokens: int = 0) -> ModelSpec | None:
        """Pick the best healthy collect model (deep-dive generation).

        Returns the model with the fewest calls among healthy models that pass
        the pre-call gate (cooldown + budget), or None when every collect
        model is down/exhausted/rate-limited.
        """
        self._phase = "collect"
        for spec in self._ranked_collect():
            if not self.can_call(spec, est_output_tokens):
                continue
            if self.registry.provider_healthy(spec.provider, spec.model):
                self.log.event("collect", "pick_model", collection=collection,
                               provider=spec.provider, model=spec.model)
                return spec
        self.log.event("collect", "no_provider_available", collection=collection,
                       status="skip", detail="all collect models down/budget-exhausted/rate-limited")
        self._persist_earliest_model_resume()
        return None

    def _persist_earliest_model_resume(self) -> None:
        """Point each cooling provider's cooldown at its earliest model recovery.

        Called when pick_collect found nothing callable.  The scheduler waits
        on PROVIDER cooldowns, so without this it would re-run collect
        immediately and fail instantly while every model is still cooling.
        """
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        earliest: dict[str, dt.datetime] = {}
        for spec in self._ranked_collect():
            until = self.registry.model_cooldown_until(spec.provider, spec.model)
            if not until:
                continue
            try:
                deadline = dt.datetime.fromisoformat(until)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            if deadline <= now:
                continue
            cur = earliest.get(spec.provider)
            if cur is None or deadline < cur:
                earliest[spec.provider] = deadline
        for provider, deadline in earliest.items():
            self.registry.set_provider_cooldown(
                provider, deadline.isoformat(timespec="seconds"))
            self.log.event("collect", "all_models_cooling", provider=provider,
                           detail=f"retry after {deadline.isoformat(timespec='seconds')}")

    def pick_check(self) -> ModelSpec | None:
        """Pick the healthy check provider (Gemini search grounding)."""
        self._phase = "check"
        for spec in self.models_for_role("check"):
            if not self.can_call(spec, est_output_tokens=300):
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
        """Deprecated — replaced by the token-aware ``can_call`` gate."""
        return self.can_call(spec)

    # ---- calls ------------------------------------------------------------
    def generate(self, spec: ModelSpec, system_prompt: str,
                 user_prompt: str, items: int = 1,
                 est_output_tokens: int = 0) -> dict[str, Any]:
        """Generate a deep-dive via the selected collect model (JSON output)."""
        if self.mock:
            return self.mock_generator(system_prompt, user_prompt)
        start = time.monotonic()
        key = self._next_key(spec)
        try:
            if spec.fmt == "google":
                data, tokens = call_google(spec, key, system_prompt, user_prompt,
                                           json_mode=True,
                                           max_tokens=self._max_output(spec))
            else:
                data, tokens = call_openai(spec, key, system_prompt, user_prompt,
                                           json_mode=spec.supports_json,
                                           max_tokens=self._max_output(spec))
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               items=items, tokens=tokens,
                                               latency_ms=latency)
            self.log.event("collect", "call_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency,
                           detail=f"tokens={tokens}")
            return data
        except ProviderError as e:
            self._record_failure(spec, e.status_code, phase="collect",
                                 retry_after=e.retry_after)
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
            data, tokens = call_google(spec, key, system_prompt, user_prompt,
                                       json_mode=True, search_tool=spec.search_tool,
                                       max_tokens=self._max_output(spec))
            latency = int((time.monotonic() - start) * 1000)
            self.registry.record_provider_call(spec.provider, spec.model,
                                               tokens=tokens, latency_ms=latency)
            self.log.event("check", "verify_ok", provider=spec.provider,
                           model=spec.model, latency_ms=latency,
                           detail=f"tokens={tokens}")
            return data
        except ProviderError as e:
            self._record_failure(spec, e.status_code, phase="check",
                                 retry_after=e.retry_after)
            raise
        except Exception as e:  # defensive — never crash the pipeline
            self.registry.record_provider_failure(spec.provider, spec.model, mark_down=True)
            self.log.event("check", "verify_error", provider=spec.provider,
                           model=spec.model, status="error", detail=str(e))
            raise ProviderError(f"unexpected error: {e}") from e

    def _max_output(self, spec: ModelSpec) -> int:
        """Per-call output cap from the provider config."""
        p = self.cfg.providers.get(spec.provider)
        return p.max_output_tokens if p else 2048

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
                        phase: str, retry_after: float | None = None) -> None:
        """Handle a failed provider call: persisted cooldown/down + log.

        429/5xx (transient): put the whole provider into a persisted cooldown
        (registry) so later picks rotate to a different provider — even across
        runs.  Cooldown respects the server's ``Retry-After`` when present.
        Hard 4xx (decommissioned/bad config): mark the model down immediately.
        """
        transient = status_code in TRANSIENT_STATUSES or status_code is None
        if transient:
            base = float(self.cfg.run_control.cooldown_base_seconds)
            model_key = f"{spec.provider}/{spec.model}"
            n = self._model_429_count.get(model_key, 0) + 1
            self._model_429_count[model_key] = n
            backoff = base * (2 ** (n - 1))
            wait = retry_after if retry_after and retry_after > backoff else backoff
            wait = min(wait, 900)  # cap at 15 min (job deadline bound)
            # Transient failures NEVER mark the model down: the provider-level
            # cooldown alone prevents hammering, and once it expires the model
            # is retried (this is what lets the in-run collect loop recover).
            mark_down = False
        else:
            wait = 0.0
            mark_down = True

        self.registry.record_provider_failure(spec.provider, spec.model,
                                              mark_down=mark_down)
        if transient:
            import datetime as dt
            now = dt.datetime.now(dt.timezone.utc)
            if wait > 0:
                until = (now + dt.timedelta(seconds=wait)).isoformat(timespec="seconds")
                self.registry.set_provider_cooldown(spec.provider, until)
            # Per-model cooldown grows with PERSISTED consecutive failures so
            # a chronically rate-limited model is rotated away from instead of
            # being re-picked (fewest-calls ranking) every round and hammered.
            mfail = self.registry.provider_model_stats(
                spec.provider, spec.model).get("consecutive_failures", 0)
            model_wait = min(base * (2 ** max(mfail - 1, 0)), 900)
            self.registry.set_model_cooldown(
                spec.provider, spec.model,
                (now + dt.timedelta(seconds=model_wait)).isoformat(timespec="seconds"))
        self.log.event(phase, "call_error", provider=spec.provider,
                       model=spec.model, status="error",
                       detail=f"HTTP {status_code}: {spec.provider} "
                              f"{'cooldown' if transient else 'down'} "
                              f"(wait={wait:.0f}s mark_down={mark_down})")
