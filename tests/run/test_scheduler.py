"""Tests for src.run.scheduler — cron calculation + workflow cron rewrite."""

from __future__ import annotations

from pathlib import Path

from src.run.scheduler import iso_to_cron, update_workflow_cron

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
