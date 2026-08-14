"""Tests for src.run.controller — scheduling + target tracking."""

from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.run.controller import RunController
from src.storage.registry import Registry
from src.utils.logging_util import RunLog


def _setup(tmp_path: Path):
    cfg = Config.load()
    reg = Registry(tmp_path)
    run_log = RunLog(reg.dir)
    return cfg, reg, run_log


def test_schedule_roundtrip(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(collect_next_run="2026-08-14T12:00:00+00:00",
                       target_remaining=25)
    schedule = ctl.load_schedule()
    assert schedule["collect_next_run"] == "2026-08-14T12:00:00+00:00"
    assert schedule["target_remaining"] == 25


def test_decide_next_run_none_when_no_cooldown(tmp_path: Path):
    """No provider cooldown → a run is possible now (None = no wait)."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=cfg.targets.total_per_day)
    assert ctl.earliest_provider_resume() is None


def test_decide_next_run_after_cooldown(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    # ALL providers in cooldown → earliest expiry is the next run time
    reg.set_provider_cooldown("openrouter", "2099-01-01T00:00:00+00:00")
    reg.set_provider_cooldown("groq", "2099-01-02T00:00:00+00:00")
    reg.set_provider_cooldown("gemini", "2099-01-03T00:00:00+00:00")
    resume = ctl.earliest_provider_resume()
    assert resume is not None
    assert resume.startswith("2099-01-01")     # earliest cooldown expiry
    assert ctl.decide_next_run() is not None    # next run scheduled


def test_target_met_stops_next_run(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=0)      # target met
    reg.set_provider_cooldown("openrouter", "2099-01-01T00:00:00+00:00")
    assert ctl.decide_next_run() is None        # no more runs today


def test_progress_updates_remaining(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=30)
    ctl.report_progress(4)
    assert ctl.load_schedule()["target_remaining"] == 26


def test_should_continue_stops_at_target_and_deadline(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    import datetime as dt
    start = dt.datetime.now(dt.timezone.utc)
    assert ctl.should_continue(start, collected=cfg.targets.total_per_day) is False
    assert ctl.should_continue(start, collected=0) is True
