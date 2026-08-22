"""Tests for src.run.controller — scheduling + target tracking."""

from __future__ import annotations

import datetime as dt
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


def test_earliest_provider_resume_none_when_no_cooldown(tmp_path: Path):
    """No provider cooldown → a run is possible now (None = callable)."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=cfg.targets.total_per_day)
    assert ctl.earliest_provider_resume() is None


def test_earliest_provider_resume_after_cooldown(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    # ALL providers in cooldown → earliest expiry is the next run time
    reg.set_provider_cooldown("openrouter", "2099-01-01T00:00:00+00:00")
    reg.set_provider_cooldown("groq", "2099-01-02T00:00:00+00:00")
    reg.set_provider_cooldown("gemini", "2099-01-03T00:00:00+00:00")
    resume = ctl.earliest_provider_resume()
    assert resume is not None
    assert resume.startswith("2099-01-01")     # earliest cooldown expiry


def test_resume_ignores_budget_exhausted_provider(tmp_path: Path):
    """A budget-exhausted provider's expired cooldown must NOT be seen as
    'callable' — the loop would spin forever collecting nothing."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    # openrouter: over daily token budget, cooldown expired → irrelevant
    reg.record_provider_call("openrouter", "m", tokens=10**9)
    reg.set_provider_cooldown("openrouter", "2020-01-01T00:00:00+00:00")
    # groq: budget left, cooldown in the future → the loop must wait on IT
    reg.set_provider_cooldown("groq", "2099-01-02T00:00:00+00:00")
    resume = ctl.earliest_provider_resume()
    assert resume is not None
    assert resume.startswith("2099-01-02")     # groq's cooldown, not None
    # now groq also budget-exhausted + cooldown expired → nothing callable
    reg.record_provider_call("groq", "m", tokens=10**9)
    assert ctl.earliest_provider_resume() is None
    assert ctl.any_collect_provider_callable() is False


def test_provider_with_budget_but_all_models_down_is_not_callable(tmp_path: Path):
    """Budget left but every model quarantined (healthy=False today) must
    NOT be 'callable' — the scheduler would spin collect subprocesses that
    produce nothing (08-22 heartbeat spin)."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    # openrouter: budget OK but its model is quarantined
    for m in cfg.providers["openrouter"].models:
        reg.provider_model_stats("openrouter", m)["healthy"] = False
        reg.provider_model_stats("openrouter", m)["last_health_check"] = "2026-08-22T00:00:00+00:00"
    # groq: budget-exhausted → also not collectable
    reg.record_provider_call("groq", "m", tokens=10**9)
    assert ctl.any_collect_provider_callable() is False
    assert ctl._providers_exhausted() is True
    assert ctl.earliest_provider_resume() is None


def test_decide_next_run_cooldown_points_to_earliest(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    reg.set_provider_cooldown("openrouter", "2099-01-01T00:00:00+00:00")
    reg.set_provider_cooldown("groq", "2099-01-02T00:00:00+00:00")
    reg.set_provider_cooldown("gemini", "2099-01-03T00:00:00+00:00")
    nxt = ctl.decide_next_run()
    assert nxt is not None
    assert nxt.startswith("2099-01-01")        # earliest expiry (+ jitter)


def test_decide_next_run_budget_left_short_delay(tmp_path: Path):
    """No cooldown, budget remains → short re-check delay (not None)."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    nxt = ctl.decide_next_run()
    assert nxt is not None                      # always a concrete time
    # within a few minutes from now
    when = dt.datetime.fromisoformat(nxt)
    assert (when - dt.datetime.now(dt.timezone.utc)).total_seconds() < 3600


def test_decide_next_run_target_met_schedules_next_day(tmp_path: Path):
    """Target met → next run is tomorrow 01:00 UTC (fresh day)."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=0)      # target met
    nxt = ctl.decide_next_run()
    assert nxt is not None
    when = dt.datetime.fromisoformat(nxt)
    assert when.hour == 1 and when.minute == 0
    tomorrow = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    assert when.date() == tomorrow.date()


def test_decide_next_run_providers_exhausted_next_day(tmp_path: Path):
    """All collect providers at daily budget → next day 01:00."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    for p in cfg.providers.values():
        if p.role == "collect":
            reg.record_provider_call(p.name, "m", tokens=p.max_daily_tokens)
    nxt = ctl.decide_next_run()
    when = dt.datetime.fromisoformat(nxt)
    assert when.hour == 1 and when.minute == 0


def test_progress_updates_remaining(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.write_schedule(target_remaining=30)
    ctl.report_progress(4)
    assert ctl.load_schedule()["target_remaining"] == 26


def test_should_continue_stops_at_target_and_deadline(tmp_path: Path):
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    start = dt.datetime.now(dt.timezone.utc)
    assert ctl.should_continue(start, collected=cfg.targets.total_per_day) is False
    assert ctl.should_continue(start, collected=0) is True


def test_rollover_resets_quotas_and_target(tmp_path: Path):
    """New UTC day: exhausted provider quotas, active cooldowns and a met
    daily target must all roll forward — otherwise the first run of the day
    sees stale 'yesterday exhausted' state and collects nothing."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    # yesterday's exhaustion + cooldown
    reg.record_provider_call("groq", "m", tokens=99999)
    reg.set_provider_cooldown("groq", "2099-01-01T00:00:00+00:00")
    reg.provider_state("groq")["quota_date"] = "2000-01-01"
    ctl.write_schedule(target_remaining=0)      # yesterday's target met

    assert ctl.rollover_daily() is True         # day changed → rolled over
    assert reg.provider_tokens_used("groq") == 0
    assert reg.provider_cooldown_until("groq") is None
    assert ctl.load_schedule()["target_remaining"] == cfg.targets.total_per_day
    assert ctl.any_collect_provider_callable() is True

    # same day → no second rollover
    assert ctl.rollover_daily() is False
    assert ctl.load_schedule()["target_remaining"] == cfg.targets.total_per_day


def test_rollover_noop_same_day_keeps_state(tmp_path: Path):
    """Rollover on the same UTC day must not clear live quotas."""
    cfg, reg, run_log = _setup(tmp_path)
    ctl = RunController(cfg, reg, run_log, tmp_path)
    ctl.rollover_daily()                        # initialize rollover_date
    reg.record_provider_call("groq", "m", tokens=500)
    assert ctl.rollover_daily() is False
    assert reg.provider_tokens_used("groq") == 500
