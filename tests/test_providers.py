"""Tests for src.providers — self-managing multi-provider layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.logging_util import RunLog
from src.providers import ModelSpec, ProviderError, ProviderManager
from src.registry import Registry


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


def test_budget_stops_calls(tmp_path: Path):
    pm = _pm(tmp_path, mock=True)
    groq_cfg = pm.cfg.providers["groq"]
    # exhaust groq budget, openrouter should be picked instead
    for s in pm.models_for_role("collect"):
        if s.provider == "groq":
            pm.registry.record_provider_call(s.provider, s.model, items=groq_cfg.max_items)
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
    # pure function check: _call_google with a fake key raises ProviderError
    from src.providers import _call_google
    spec = ModelSpec(provider="gemini", model="gemini-2.5-flash",
                     fmt="google", search_tool="google_search")
    with pytest.raises(ProviderError):
        _call_google(spec, "invalid-key", "sys", "user", json_mode=True)
