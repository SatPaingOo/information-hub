"""information-hub — scheduler (run layer, cron-driven entry point).

Runs when the scheduler.yml cron fires.  Reads the persisted schedule
(data/state/schedule.json), runs the due phase(s) under the pipeline lock,
then RECOMPUTES the next run time (provider cooldowns/budgets via
RunController) and REWRITES the dynamic cron line in
``.github/workflows/scheduler.yml`` so the workflow only fires again when
collection is actually possible — no fixed run times, no every-N-minute
heartbeat.

  - collect is due when collect_next_run <= now AND target_remaining > 0
  - check   is due when check_next_run   <= now
  - empty schedule (first run) → bootstrap a collect
  - after running, scheduler.yml cron is updated to the next run time
    (scheduler.yml keeps a static daily-01:00 safety cron as a fallback)

Usage:
    python -m src.run.scheduler                # cron entry: run due phases
    python -m src.run.scheduler --force-phase collect   # bypass due check
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from src.config import Config
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root (config.yml lives here)
_WORKFLOW = _ROOT / ".github" / "workflows" / "scheduler.yml"


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _due(schedule: dict, key: str) -> bool:
    """True when the phase's scheduled run time has passed."""
    when = schedule.get(key)
    if not when:
        return False
    try:
        deadline = dt.datetime.fromisoformat(when)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=dt.timezone.utc)
        return dt.datetime.now(dt.timezone.utc) >= deadline
    except ValueError:
        return False


def json_load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def iso_to_cron(iso: str) -> str:
    """Convert an ISO UTC datetime into a date-specific cron (M H D M *).

    GitHub Actions cron is 5-field (minute hour day-of-month month
    day-of-week) — a date-specific expression fires once at that time, which
    is exactly what a cooldown-based "next run" needs.
    """
    d = dt.datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    d = d.astimezone(dt.timezone.utc)
    return f"{d.minute} {d.hour} {d.day} {d.month} *"


def update_workflow_cron(next_run_iso: str, path: Path | None = None) -> bool:
    """Rewrite the dynamic (first) cron line in scheduler.yml.

    The second cron line (safety fallback) is left untouched.  The change is
    committed by the workflow's commit step, so GitHub picks up the new
    schedule on the next trigger evaluation.

    Returns:
        True when the cron line was rewritten.
    """
    path = path or _WORKFLOW
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    cron = iso_to_cron(next_run_iso)
    new, n = re.subn(r'(\s*cron: ")[^"]*(")', rf"\g<1>{cron}\g<2>", text, count=1)
    if n:
        path.write_text(new, encoding="utf-8")
    return bool(n)


def _run_phases(cfg: Config, phases: list[str], run_log: RunLog) -> None:
    """Execute each phase via ``python -m src.main --phase <p>`` (same env)."""
    for phase in phases:
        print(f"scheduler: running {phase}")
        run_log.event("scheduler", "phase_run", detail=phase)
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "--phase", phase],
            cwd=str(_ROOT),   # absolute repo root → src importable
            env={**__import__("os").environ},
        )
        if result.returncode != 0:
            run_log.event("scheduler", "phase_failed", status="error",
                          detail=f"{phase} rc={result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="information-hub scheduler")
    parser.add_argument("--force-phase", choices=["collect", "check"], default=None,
                        help="run this phase now, bypassing the schedule")
    args = parser.parse_args()

    cfg = Config.load()
    registry = Registry(cfg.storage.data_dir / "state")
    run_log = RunLog(registry.dir)
    schedule = json_load(registry.dir / "schedule.json")

    if not registry.acquire_lock():
        print("scheduler: pipeline lock held by another run — skipping")
        run_log.event("scheduler", "lock_busy", status="skip")
        return 0

    try:
        phases: list[str] = []
        if args.force_phase:
            phases = [args.force_phase]
        elif not schedule:
            # first run — nothing scheduled yet → bootstrap a collect
            phases = ["collect"]
            run_log.event("scheduler", "bootstrap", detail="empty schedule")
        else:
            remaining = schedule.get("target_remaining", cfg.targets.total_per_day)
            if _due(schedule, "collect_next_run") and remaining > 0:
                phases.append("collect")
            if _due(schedule, "check_next_run"):
                phases.append("check")

        if not phases:
            print("scheduler: no phase due — idle")
            run_log.event("scheduler", "idle", status="ok")
            return 0

        _run_phases(cfg, phases, run_log)

        # After running — recompute the next run time and (when a BOT_PAT is
        # configured) point the workflow cron at it. The GitHub App token
        # cannot modify .github/workflows/*, so without BOT_PAT we keep the
        # schedule in data/state/ and let the daily-01:00 safety cron drive
        # the next run (the dynamic cron stays at its placeholder).
        from src.run.controller import RunController
        ctl = RunController(cfg, registry, run_log, cfg.storage.data_dir)
        next_run = ctl.decide_next_run()
        ctl.write_schedule(collect_next_run=next_run,
                           check_next_run=next_run if "collect" in phases else None)
        if os.environ.get("BOT_PAT"):
            if update_workflow_cron(next_run):
                run_log.event("scheduler", "cron_updated",
                              detail=f"next_run={next_run}")
                print(f"scheduler: cron updated → {iso_to_cron(next_run)} "
                      f"(next run {next_run} UTC)")
            else:
                run_log.event("scheduler", "cron_update_failed", status="error")
                print("scheduler: WARNING — could not rewrite scheduler.yml cron")
        else:
            print(f"scheduler: no BOT_PAT — schedule kept in data/state "
                  f"(next run {next_run} UTC); safety cron 0 1 * * * drives runs")
        return 0
    finally:
        registry.release_lock()
        registry.save()


if __name__ == "__main__":
    sys.exit(main())
