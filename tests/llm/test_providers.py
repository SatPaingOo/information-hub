"""Tests for src.llm.providers — self-managing multi-provider layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.llm.providers import ModelSpec, ProviderError, ProviderManager
from src.storage.registry import Registry
from src.utils.logging_util import RunLog


def _pm(tmp_path: Path, mock: bool = True) -> ProviderManager:
    cfg = Config.load()
    reg = Registry(tmp_path)
    run_log = RunLog(reg.dir)
    return ProviderManager(cfg, reg, run_log, mock=mock)


def test_models_for_role_collect_includes_config_providers(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    specs = pm.models_for_role("collect")
    providers = {s.provider for s in specs}
    # groq + openrouter configured; mock allows unkeyed
    assert "groq" in providers
    assert "openrouter" in providers
    assert all(s.fmt == "openai" for s in specs if s.provider != "gemini")


def test_check_role_is_gemini_only(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    specs = pm.models_for_role("check")
    assert {s.provider for s in specs} == {"gemini"}
    assert specs[0].search_tool == "google_search"


def test_auto_disable_when_no_keys_real_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    pm = _pm(tmp_path, mock=False)
    assert pm.models_for_role("collect") == []
    assert pm.models_for_role("check") == []


def test_pick_collect_returns_spec_and_rotates_on_failure(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    spec = pm.pick_collect("ai-research")
    assert spec is not None
    assert spec.provider in ("groq", "openrouter")

    # simulate failures → rotation / graceful None when all down
    for s in pm.models_for_role("collect"):
        pm.registry.record_provider_failure(s.provider, s.model, mark_down=True)
    assert pm.pick_collect("ai-research") is None  # all down → skip


def test_pick_collect_scouts_untested_models(tmp_path: Path):
    """Auto-integrated models with 0 calls are ping-scouted before use
    (mock mode: scout is a no-op that returns True — untested models still
    get picked)."""
    pm = _pm(tmp_path, mock=True)
    spec = pm.pick_collect("ai-research")
    assert spec is not None
    assert pm.scout(spec) is True  # mock scout succeeds without a call


def test_ranked_collect_puts_proven_models_first(tmp_path: Path):
    """Proven (successful) models rank above untested ones; failing models
    fall as their error share grows."""
    pm = _pm(tmp_path, mock=True)
    proven = pm._ranked_collect()[0]
    # proven model: 5 calls, 0 errors → success 1.0
    pm.registry.record_provider_call(proven.provider, proven.model,
                                     items=3, tokens=3000)
    failing = pm._ranked_collect()[-1]
    # failing model: 5 calls, 4 errors → success 0.2 (ranks last)
    pm.registry.record_provider_failure(failing.provider, failing.model,
                                        mark_down=False)
    pm.registry.record_provider_failure(failing.provider, failing.model,
                                        mark_down=False)
    pm.registry.record_provider_failure(failing.provider, failing.model,
                                        mark_down=False)
    pm.registry.record_provider_failure(failing.provider, failing.model,
                                        mark_down=False)
    ordered = pm._ranked_collect()
    assert ordered[0] == proven
    assert ordered[-1] == failing


def test_budget_stops_calls(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    groq_cfg = pm.cfg.providers["groq"]
    # exhaust groq token budget (provider-aggregate), openrouter should be picked
    for s in pm.models_for_role("collect"):
        if s.provider == "groq":
            pm.registry.record_provider_call(s.provider, s.model,
                                             items=groq_cfg.max_daily_items,
                                             tokens=groq_cfg.max_daily_tokens)
    spec = pm.pick_collect("ai-research")
    assert spec is not None
    assert spec.provider == "openrouter"


def test_generate_mock_uses_generator(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    spec = pm.models_for_role("collect")[0]
    out = pm.generate(spec, "sys", "user")
    assert isinstance(out, dict)


def test_openrouter_discovery_mock_filter(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    models = pm._discover_openrouter_free()
    assert models  # mock returns two free model ids
    assert all(":free" in m for m in models)


def test_google_call_rejects_bad_json():
    # pure function check: call_google with a fake key raises ProviderError
    from src.llm.clients import call_google
    spec = ModelSpec(provider="gemini", model="gemini-2.5-flash",
                     fmt="google", search_tool="google_search")
    with pytest.raises(ProviderError):
        call_google(spec, "invalid-key", "sys", "user", json_mode=True)


def test_can_call_gate_respects_cooldown(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    spec = pm.models_for_role("collect")[0]
    assert pm.can_call(spec) is True
    # provider-level cooldown is only the scheduler's pacing signal — it does
    # NOT block calls (one model's 429 must not starve healthy siblings)
    pm.registry.set_provider_cooldown(spec.provider, "2099-01-01T00:00:00+00:00")
    assert pm.can_call(spec) is True
    # per-model cooldown DOES block that specific model
    pm.registry.set_model_cooldown(spec.provider, spec.model, "2099-01-01T00:00:00+00:00")
    assert pm.can_call(spec) is False


def test_pick_collect_uses_sibling_models_while_provider_cooling(tmp_path: Path):
    """One model rate-limited must not block the provider's other models —
    the pick falls through to a sibling within the same provider."""
    pm = _pm(tmp_path, mock=True)
    groq_specs = [s for s in pm.models_for_role("collect") if s.provider == "groq"]
    first, sibling = groq_specs[0], groq_specs[1]
    # sibling is proven (1 successful call) → ranks above untested models
    pm.registry.record_provider_call("groq", sibling.model, items=1, tokens=1000)
    pm.registry.set_provider_cooldown("groq", "2099-01-01T00:00:00+00:00")
    pm.registry.set_model_cooldown("groq", first.model, "2099-01-01T00:00:00+00:00")
    spec = pm.pick_collect("ai-research")
    assert spec is not None
    # provider cooldown no longer blocks calls — groq's other model is picked
    assert spec.provider == "groq"
    assert spec.model == sibling.model


def test_can_call_gate_respects_token_budget(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    spec = pm.models_for_role("collect")[0]
    cfg_p = pm.cfg.providers[spec.provider]
    pm.registry.record_provider_call(spec.provider, spec.model,
                                     tokens=cfg_p.max_daily_tokens)
    assert pm.can_call(spec, est_output_tokens=1) is False
    assert pm.can_call(spec, est_output_tokens=0) is True  # at the cap edge


def test_can_call_gate_respects_model_cooldown(tmp_path: Path):
    """A per-model cooldown blocks only that model, not the provider."""
    pm = _pm(tmp_path, mock=True)
    specs = pm.models_for_role("collect")
    first = specs[0]
    pm.registry.set_model_cooldown(first.provider, first.model,
                                   "2099-01-01T00:00:00+00:00")
    assert pm.can_call(first) is False
    other = next(s for s in specs if s.model != first.model)
    assert pm.can_call(other) is True


def test_pick_collect_rotates_away_from_rate_limited_model(tmp_path: Path):
    """The fewest-calls model in cooldown must not be re-picked — the pick
    falls through to the next model instead of hammering the same one."""
    pm = _pm(tmp_path, mock=True)
    ranked = pm._ranked_collect()
    first = ranked[0]
    pm.registry.set_model_cooldown(first.provider, first.model,
                                   "2099-01-01T00:00:00+00:00")
    spec = pm.pick_collect("ai-research")
    assert spec is not None
    assert (spec.provider, spec.model) != (first.provider, first.model)


def test_pick_collect_persists_model_resume_when_all_cooling(tmp_path: Path):
    """All models cooling → pick_collect None AND the provider cooldown is
    pointed at the earliest model recovery so the scheduler waits (no spin)."""
    pm = _pm(tmp_path, mock=True)
    for s in pm.models_for_role("collect"):
        pm.registry.set_model_cooldown(s.provider, s.model,
                                       "2099-01-01T00:00:00+00:00")
    assert pm.pick_collect("ai-research") is None
    assert pm.registry.provider_cooldown_until("groq") == "2099-01-01T00:00:00+00:00"
    assert pm.registry.provider_cooldown_until("openrouter") == "2099-01-01T00:00:00+00:00"


def test_chronically_rate_limited_model_is_quarantined(tmp_path: Path):
    """N consecutive transient failures remove the model for the day — the
    provider's other models stay usable (no provider-level block)."""
    from src.llm.providers import _QUARANTINE_AFTER
    pm = _pm(tmp_path, mock=True)
    ranked = pm._ranked_collect()
    bad = ranked[0]
    for _ in range(_QUARANTINE_AFTER + 1):
        pm.registry.record_provider_failure(bad.provider, bad.model, mark_down=False)
    pm._record_failure(bad, 429, phase="collect")
    # model is quarantined...
    assert pm.registry.provider_healthy(bad.provider, bad.model) is False
    assert pm.pick_collect("ai-research") is not None      # ...but provider still usable
    # next UTC day re-enables it
    pm.registry.provider_model_stats(bad.provider, bad.model)["last_health_check"] = "2026-08-01T00:00:00+00:00"
    pm.registry.reset_health_if_stale()
    assert pm.registry.provider_healthy(bad.provider, bad.model) is True


def test_retry_after_parsed_from_header():
    from src.llm.clients import _parse_retry_after

    class FakeResp:
        headers = {"Retry-After": "53", "x-ratelimit-reset": ""}
    assert _parse_retry_after(FakeResp(), "") == 53.0

    class FakeResp2:
        headers = {"Retry-After": "", "x-ratelimit-reset": ""}
    body = '{"message": "Please try again in 53.7s"}'
    assert abs(_parse_retry_after(FakeResp2(), body) - 53.7) < 0.1
