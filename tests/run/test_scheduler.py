"""Tests for src.run.scheduler — cron calculation, workflow rewrite, collect loop."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.run.scheduler import (iso_to_cron, update_workflow_cron,
                               _should_collect_more, _wait_seconds)

_SAMPLE = """\
name: scheduler

on:
  schedule:
    # DYNAMIC cron — the pipeline rewrites this line to the next run time
    - cron: "0 1 1 1 *"
    # SAFETY fallback — daily 01:00 UTC. Never rewritten.
    - cron: "0 1 * * *"
  workflow_dispatch: {}
"""


def test_iso_to_cron_date_specific():
    assert iso_to_cron("2026-08-14T04:13:00+00:00") == "13 4 14 8 *"
    assert iso_to_cron("2026-12-31T23:59:00+00:00") == "59 23 31 12 *"


def test_iso_to_cron_naive_treated_as_utc():
    assert iso_to_cron("2026-08-14T04:13:00") == "13 4 14 8 *"


def test_update_workflow_cron_rewrites_first_line(tmp_path: Path):
    wf = tmp_path / "scheduler.yml"
    wf.write_text(_SAMPLE, encoding="utf-8")
    ok = update_workflow_cron("2026-08-14T04:13:00+00:00", wf)
    assert ok is True
    text = wf.read_text(encoding="utf-8")
    assert 'cron: "13 4 14 8 *"' in text          # dynamic line updated
    assert 'cron: "0 1 * * *"' in text            # safety line untouched


def test_update_workflow_cron_missing_file(tmp_path: Path):
    ok = update_workflow_cron("2026-08-14T04:13:00+00:00", tmp_path / "nope.yml")
    assert ok is False


# ---- collect-loop decision helpers ----------------------------------------

def _t(seconds_from_now: float = 0) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds_from_now)


def test_should_collect_more_target_met_stops():
    keep, reason = _should_collect_more(0, _t(), _t(1500), None)
    assert keep is False
    assert "target" in reason


def test_should_collect_more_deadline_reached_stops():
    keep, _ = _should_collect_more(5, _t(), _t(-10), None)
    assert keep is False


def test_should_collect_more_cooldown_outlasts_job_stops():
    resume = _t(3000).isoformat(timespec="seconds")   # 50 min away > 25-min job
    keep, reason = _should_collect_more(5, _t(), _t(1500), resume)
    assert keep is False
    assert "outlasts" in reason


def test_should_collect_more_budget_left_keeps_going():
    keep, _ = _should_collect_more(9, _t(), _t(1500), None)
    assert keep is True
    # short cooldown within the job window → keep (wait, then retry)
    keep2, _ = _should_collect_more(9, _t(), _t(1500), _t(300).isoformat())
    assert keep2 is True


def test_wait_seconds_respects_cooldown_and_deadline():
    now = _t()
    deadline = now + dt.timedelta(minutes=25)
    # cooldown 5 min away → wait ~300s (capped at 60s chunks)
    w = _wait_seconds((now + dt.timedelta(minutes=5)).isoformat(), now, deadline)
    assert w is not None and 0 < w <= 60
    # cooldown expired → 0 (re-check immediately)
    assert _wait_seconds((now - dt.timedelta(seconds=5)).isoformat(), now, deadline) == 0.0
    # cooldown outlasts job → None
    assert _wait_seconds((now + dt.timedelta(minutes=40)).isoformat(),
                         now, deadline) is None
    # no cooldown → None (no wait)
    assert _wait_seconds(None, now, deadline) is None
