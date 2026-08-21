"""information-hub — scheduler (run layer, cron-driven entry point).

Runs when the scheduler.yml cron fires (or manually).  Reads the persisted
schedule (data/state/schedule.json), runs the due phase(s) under the pipeline
lock, then keeps the pipeline going:

- **In-run collect loop** (works WITHOUT any PAT): when collect providers are
  rate-limited, the job sleeps until the persisted cooldown lapses and retries
  — repeatedly, until the daily target is met or the job deadline
  (25 min) is reached.  One workflow run can fill the whole daily target by
  riding through free-tier cooldowns.
- **Cross-run scheduling**: after the run, the next run time is recomputed and
  written to data/state/schedule.json.  When a ``BOT_PAT`` secret is set, the
  dynamic cron line in ``.github/workflows/scheduler.yml`` is ALSO rewritten to
  that time (the GitHub App token alone cannot modify workflow files).  Without
  a PAT, the daily-01:00 safety cron drives the next trigger.

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
import time
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
    """Convert an ISO UTC datetime into a date-specific cron (M H D M *)."""
    d = dt.datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    d = d.astimezone(dt.timezone.utc)
    return f"{d.minute} {d.hour} {d.day} {d.month} *"


def update_workflow_cron(next_run_iso: str, path: Path | None = None) -> bool:
    """Rewrite the dynamic (first) cron line in scheduler.yml (BOT_PAT only).

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


# ---- in-run collect loop (dynamic scheduling without a PAT) ---------------

def _should_collect_more(remaining: int, now: dt.datetime, deadline: dt.datetime,
                         resume_iso: str | None) -> tuple[bool, str]:
    """Decide whether the collect loop should keep going.

    Returns ``(keep, reason)``:
    - target met → stop
    - job deadline reached → stop
    - a provider cooldown outlasts the job → stop (next run will resume)
    - otherwise → keep collecting (or waiting for a cooldown)
    """
    if remaining <= 0:
        return False, "daily target met"
    if now >= deadline:
        return False, "job deadline reached"
    if resume_iso:
        try:
            rdt = dt.datetime.fromisoformat(resume_iso)
            if rdt.tzinfo is None:
                rdt = rdt.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return True, ""
        if rdt >= deadline:
            return False, f"provider cooldown (until {resume_iso}) outlasts job"
    return True, ""


def _wait_seconds(resume_iso: str | None, now: dt.datetime,
                  deadline: dt.datetime) -> float | None:
    """Seconds to sleep before re-checking (None = no wait needed).

    Returns 0 when the cooldown already expired (re-check immediately),
    None when the cooldown outlasts the job.
    """
    if not resume_iso:
        return None
    try:
        rdt = dt.datetime.fromisoformat(resume_iso)
        if rdt.tzinfo is None:
            rdt = rdt.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    wait = (rdt - now).total_seconds()
    if wait <= 0:
        return 0.0
    if now + dt.timedelta(seconds=wait) >= deadline:
        return None
    return min(wait, 60.0)   # wake every 60s to re-check (cheap)


def _collect_until_deadline(cfg: Config, registry: Registry, run_log: RunLog) -> None:
    """Run collect repeatedly, waiting through provider cooldowns.

    One job fills as much of the daily target as free-tier rate limits allow:
    collect → providers rate-limited (cooldown persisted) → sleep until
    cooldown lapses → collect again → ... until target met or the 25-min job
    deadline.  Requires no PAT — works with the plain GitHub App token.
    """
    from src.run.controller import RunController
    ctl = RunController(cfg, registry, run_log, cfg.storage.data_dir)
    deadline = ctl.job_deadline()
    max_rounds = 20
    for _ in range(max_rounds):
        # Fresh attempt every round: clear model down-flags.  The persisted
        # provider cooldown is the real pacing guard — a model marked down by
        # an earlier (possibly buggy) run must not block the whole day.
        registry.reset_model_health()
        now = dt.datetime.now(dt.timezone.utc)
        schedule = json_load(registry.dir / "schedule.json")
        remaining = schedule.get("target_remaining", cfg.targets.total_per_day)
        resume = ctl.earliest_provider_resume()
        # Order matters: first honor cooldown waits (ride the rate limit),
        # THEN stop only if nothing can ever be called again this run.
        keep, reason = _should_collect_more(remaining, now, deadline, resume)
        if not keep:
            print(f"scheduler: collect loop stop — {reason}")
            run_log.event("scheduler", "collect_loop_stop", detail=reason)
            break
        wait = _wait_seconds(resume, now, deadline) if resume else None
        if wait is not None and wait > 0:
            print(f"scheduler: rate-limited — waiting {wait:.0f}s "
                  f"(cooldown until {resume})")
            run_log.event("scheduler", "waiting_cooldown",
                          detail=f"{wait:.0f}s until {resume}")
            time.sleep(wait)
            continue
        # No cooldown pending — but every collect provider may still be over
        # its daily token/item budget (nothing will change this run).
        if not ctl.any_collect_provider_callable():
            print("scheduler: collect loop stop — all collect providers "
                  "exhausted (daily budget)")
            run_log.event("scheduler", "collect_loop_stop",
                          detail="all collect providers over daily budget")
            break
        print("scheduler: provider available — collecting")
        # Cap a single collect subprocess well under the Actions job timeout
        # (30 min) — otherwise one slow/failing phase can run to the job
        # timeout and the run is CANCELLED, losing the commit step (see run
        # 32439749306 on 2026-08-21).
        sub_timeout = max(60, int((deadline - dt.datetime.now(dt.timezone.utc)).total_seconds()))
        try:
            rc = subprocess.run(
                [sys.executable, "-m", "src.main", "--phase", "collect"],
                cwd=str(_ROOT), env={**os.environ},
                timeout=sub_timeout,
            )
        except subprocess.TimeoutExpired:
            run_log.event("scheduler", "collect_timeout", status="error",
                          detail=f"collect subprocess > {sub_timeout}s — killed")
            print(f"scheduler: collect subprocess timed out after {sub_timeout}s")
            registry.reload()
            break
        registry.reload()   # subprocess saved fresh state — don't keep stale copy
        if rc.returncode != 0:
            run_log.event("scheduler", "collect_failed", status="error",
                          detail=f"rc={rc.returncode}")
    schedule = json_load(registry.dir / "schedule.json")
    print(f"scheduler: collect loop finished — "
          f"target_remaining={schedule.get('target_remaining', '?')}")


def _run_check(cfg: Config, registry: Registry, run_log: RunLog) -> None:
    """Verify today's collected items once (check phase)."""
    print("scheduler: running check")
    run_log.event("scheduler", "phase_run", detail="check")
    try:
        rc = subprocess.run(
            [sys.executable, "-m", "src.main", "--phase", "check"],
            cwd=str(_ROOT), env={**os.environ},
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        run_log.event("scheduler", "check_timeout", status="error",
                      detail="check subprocess > 600s — killed")
        print("scheduler: check subprocess timed out")
        registry.reload()
        return
    registry.reload()   # subprocess saved fresh state — don't keep stale copy
    if rc.returncode != 0:
        run_log.event("scheduler", "check_failed", status="error",
                      detail=f"rc={rc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="information-hub scheduler")
    parser.add_argument("--force-phase", choices=["collect", "check"], default=None,
                        help="run this phase now, bypassing the schedule")
    args = parser.parse_args()

    cfg = Config.load()
    registry = Registry(cfg.storage.data_dir / "state")
    run_log = RunLog(registry.dir)
    schedule = json_load(registry.dir / "schedule.json")

    # Daily rollover FIRST — stale "yesterday exhausted / target met" state
    # must never skip today's run (fresh UTC day → fresh quotas + target).
    from src.run.controller import RunController
    RunController(cfg, registry, run_log, cfg.storage.data_dir).rollover_daily()
    schedule = json_load(registry.dir / "schedule.json")   # reload after rollover

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

        # collect: in-run loop (rides through cooldowns within this job)
        if "collect" in phases:
            _collect_until_deadline(cfg, registry, run_log)
        # check: verify today's items once, after collecting
        if "check" in phases:
            _run_check(cfg, registry, run_log)

        registry.reload()   # final reload — fresh state before schedule/save

        # Cross-run schedule: recompute the next run time and, when a BOT_PAT
        # is configured, point the workflow cron at it (the GitHub App token
        # cannot modify .github/workflows/*).
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
