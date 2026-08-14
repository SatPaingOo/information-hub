"""information-hub — scheduler (run layer, heartbeat entry point).

Runs inside scheduler.yml (heartbeat cron + manual dispatch).  Reads the
persisted schedule (registry/schedule.json) and, when a phase is due and the
pipeline lock is free, runs that phase by invoking ``src.main`` directly.

  - collect is due when collect_next_run <= now AND target_remaining > 0
  - check   is due when check_next_run   <= now AND unverified items exist
  - no fixed run times — fully dynamic, driven by provider cooldowns/budgets

Usage:
    python -m src.run.scheduler                # heartbeat: run due phases
    python -m src.run.scheduler --force-phase collect   # bypass due check
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from src.config import Config
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root (config.yml lives here)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="information-hub scheduler")
    parser.add_argument("--force-phase", choices=["collect", "check"], default=None,
                        help="run this phase now, bypassing the schedule")
    args = parser.parse_args()

    cfg = Config.load()
    registry = Registry(cfg.storage.data_dir / "collections" / "registry")
    run_log = RunLog(registry.dir)
    schedule = json_load(registry.dir / "schedule.json")

    if not registry.acquire_lock():
        print("scheduler: pipeline lock held by another run — skipping")
        run_log.event("scheduler", "lock_busy", status="skip")
        return 0

    phases: list[str] = []
    if args.force_phase:
        phases = [args.force_phase]
    else:
        remaining = schedule.get("target_remaining", cfg.targets.total_per_day)
        if _due(schedule, "collect_next_run") and remaining > 0:
            phases.append("collect")
        if _due(schedule, "check_next_run"):
            phases.append("check")

    if not phases:
        print("scheduler: no phase due — heartbeat idle")
        run_log.event("scheduler", "idle", status="ok")
        registry.release_lock()
        registry.save()
        return 0

    try:
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
    finally:
        registry.release_lock()
        registry.save()
    return 0


def json_load(path: Path) -> dict:
    import json
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


if __name__ == "__main__":
    sys.exit(main())
