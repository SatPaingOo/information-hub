"""Tests for src.keys (multi API key manager)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import GeminiConfig
from src.keys import KeyManager, KeyManagerError
from src.registry import Registry


def test_pick_requires_keys(tmp_path: Path):
    reg = Registry(tmp_path)
    km = KeyManager(GeminiConfig(), reg)
    with pytest.raises(KeyManagerError):
        km.pick()


def test_least_used_picks_balanced(tmp_path: Path):
    reg = Registry(tmp_path)
    cfg = GeminiConfig(key_strategy="least_used")
    km = KeyManager(cfg, reg)
    # simulate a couple of pre-used keys
    reg.record_key_use("k1"); reg.record_key_use("k1"); reg.record_key_use("k2")
    km.keys = ["k1", "k2", "k3"]
    assert km.pick() == "k3"          # fewest calls
    assert reg.key_stats("k3")["calls"] == 1


def test_round_robin_cycles(tmp_path: Path):
    reg = Registry(tmp_path)
    cfg = GeminiConfig(key_strategy="round_robin")
    km = KeyManager(cfg, reg)
    km.keys = ["k1", "k2"]
    picked = {km.pick(), km.pick(), km.pick()}
    assert picked == {"k1", "k2"}     # cycles across both


def test_report_and_rotate_on_quota_error(tmp_path: Path):
    reg = Registry(tmp_path)
    cfg = GeminiConfig(key_strategy="least_used",
                       key_policy=None)
    from src.config import KeyPolicy
    cfg.key_policy = KeyPolicy(rotate_on_error=[429, 500], max_errors_per_key=3)
    km = KeyManager(cfg, reg)
    km.keys = ["k1", "k2"]

    assert km.should_rotate("k1", 429) is True
    assert km.should_rotate("k1", 400) is False

    km.report("k1", status_code=429, error=True)
    km.report("k1", status_code=429, error=True)
    km.report("k1", status_code=429, error=True)
    assert reg.key_stats("k1")["failed"] is True
    assert km.pick() == "k2"          # k1 disabled → rotate to k2


def test_success_resets_error_streak(tmp_path: Path):
    reg = Registry(tmp_path)
    cfg = GeminiConfig(key_strategy="least_used")
    km = KeyManager(cfg, reg)
    km.keys = ["k1", "k2"]
    km.report("k1", status_code=429, error=True)
    km.report("k1", status_code=200, error=False)
    assert reg.key_stats("k1")["errors"] == 0
    assert reg.key_stats("k1")["failed"] is False
