"""information-hub — RunController (run layer).

Decides WHEN to run and HOW MUCH to collect based on persisted rate-limit
state (provider cooldowns + token budgets) and daily content targets.

Responsibilities:
  - decide_next_run   — earliest time any provider can be called again
  - write_schedule    — persist {collect_next_run, check_next_run, target_remaining}
  - progress/summary  — run-log events for target tracking and deadlines

The scheduler (src/run/scheduler.py) reads the schedule and triggers runs;
this controller is used by the collect/check phases to keep the schedule up
to date as quotas are consumed or exhausted.

Role: both phases — consumed by main and scheduler.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.config import Config
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

_SCHEDULE_FILE = "schedule.json"


class RunController:
    """Scheduling + target-tracking for the daily pipeline.

    Args:
        cfg:      global configuration (targets, run limits).
        registry: storage registry (persisted provider quota/cooldown).
        run_log:  structured run-log writer.
    """

    def __init__(self, cfg: Config, registry: Registry, run_log: RunLog,
                 data_dir: Path):
        self.cfg = cfg
        self.registry = registry
        self.log = run_log
        self.schedule_dir = data_dir / "collections" / "registry"
        self.schedule_dir.mkdir(parents=True, exist_ok=True)

    # ---- schedule persistence ------------------------------------------
    def load_schedule(self) -> dict[str, Any]:
        path = self.schedule_dir / _SCHEDULE_FILE
        if not path.exists():
            return {"collect_next_run": None, "check_next_run": None,
                    "target_remaining": self.cfg.targets.total_per_day,
                    "updated_at": None}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"collect_next_run": None, "check_next_run": None,
                    "target_remaining": self.cfg.targets.total_per_day,
                    "updated_at": None}

    def write_schedule(self, collect_next_run: str | None = None,
                       check_next_run: str | None = None,
                       target_remaining: int | None = None) -> None:
        """Persist the schedule; None leaves the field unchanged."""
        schedule = self.load_schedule()
        if collect_next_run is not None:
            schedule["collect_next_run"] = collect_next_run
        if check_next_run is not None:
            schedule["check_next_run"] = check_next_run
        if target_remaining is not None:
            schedule["target_remaining"] = max(0, target_remaining)
        schedule["updated_at"] = _utcnow()
        path = self.schedule_dir / _SCHEDULE_FILE
        path.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    # ---- next-run decision ---------------------------------------------
    def earliest_provider_resume(self) -> str | None:
        """Earliest ISO time any provider's persisted cooldown ends."""
        now = dt.datetime.now(dt.timezone.utc)
        earliest: dt.datetime | None = None
        for provider in self.cfg.providers:
            until = self.registry.provider_cooldown_until(provider)
            if not until:
                return None  # a provider is immediately available
            try:
                deadline = dt.datetime.fromisoformat(until)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                return None
            if deadline <= now:
                return None  # cooldown already expired → callable now
            if earliest is None or deadline < earliest:
                earliest = deadline
        return earliest.isoformat(timespec="seconds") if earliest else None

    def decide_next_run(self) -> str:
        """Next collect run time — ALWAYS a concrete UTC datetime.

        - daily target met, or every collect provider at daily budget
          → next UTC day 01:00 (fresh quotas/target)
        - some provider in cooldown → earliest cooldown expiry + jitter
        - otherwise (budget remains) → short re-check delay
        """
        now = dt.datetime.now(dt.timezone.utc)
        if self._daily_target_met() or self._providers_exhausted():
            nxt = now + dt.timedelta(days=1)
            return nxt.replace(hour=1, minute=0, second=0, microsecond=0).isoformat()
        resume = self.earliest_provider_resume()
        if resume is not None:
            jitter = self.cfg.run_control.heartbeat_minutes * 60 * 0.5
            deadline = dt.datetime.fromisoformat(resume)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
            return (deadline + dt.timedelta(seconds=jitter)).isoformat(timespec="seconds")
        delay = self.cfg.run_control.heartbeat_minutes
        return (now + dt.timedelta(minutes=delay)).isoformat(timespec="seconds")

    def _providers_exhausted(self) -> bool:
        """True when every collect provider is at its daily token/item budget."""
        for p in self.cfg.providers.values():
            if not p.enabled or p.role != "collect":
                continue
            if (self.registry.provider_items_used(p.name) < p.max_daily_items
                    and self.registry.provider_tokens_used(p.name) < p.max_daily_tokens):
                return False
        return True

    def _daily_target_met(self) -> bool:
        schedule = self.load_schedule()
        remaining = schedule.get("target_remaining",
                                 self.cfg.targets.total_per_day)
        return remaining <= 0

    def report_progress(self, collected: int) -> None:
        """Update target_remaining + log a progress event."""
        schedule = self.load_schedule()
        remaining = max(0, schedule.get("target_remaining",
                                        self.cfg.targets.total_per_day) - collected)
        self.write_schedule(target_remaining=remaining)
        total = self.cfg.targets.total_per_day
        self.log.event("collect", "progress", detail=f"collected {collected} "
                       f"(target {total - remaining}/{total} remaining {remaining})")

    # ---- deadline --------------------------------------------------------
    def job_deadline(self) -> dt.datetime:
        """UTC deadline for this job (Actions timeout is 30 min)."""
        return (dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(minutes=self.cfg.run_control.max_job_minutes))

    def should_continue(self, start: dt.datetime, collected: int) -> bool:
        """True if the run should keep collecting.

        Stops when: daily target met, job deadline reached, or every
        collect provider is in cooldown/budget-exhausted.
        """
        if collected >= self.cfg.targets.total_per_day:
            return False
        if dt.datetime.now(dt.timezone.utc) >= self.job_deadline():
            return False
        return True


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
