"""information-hub — structured logging (utils layer).

Writes:
  - console output (INFO/ERROR)
  - data/logs/run-<date>-<phase>.log                 (human-readable file)
  - data/state/run-log.jsonl                         (machine events: phase,
    provider, model, status, latency, error — full provenance trail)

Role: both phases — consumed by main and llm.providers / quality.grounding.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def setup_logging(data_dir: Path, run_date: str, phase: str) -> logging.Logger:
    """Configure root logger with console + file handlers; return the logger."""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run-{run_date}-{phase}.log"

    logger = logging.getLogger("information-hub")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


class RunLog:
    """Appends structured events to data/state/run-log.jsonl (JSON lines)."""

    def __init__(self, registry_dir: Path):
        self.path = Path(registry_dir) / "run-log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, phase: str, event: str, *, collection: str = "",
              item_id: str = "", provider: str = "", model: str = "",
              status: str = "ok", latency_ms: int | None = None,
              detail: str = "") -> None:
        entry = {
            "ts": _utcnow(),
            "phase": phase,
            "event": event,
            "collection": collection,
            "item_id": item_id,
            "provider": provider,
            "model": model,
            "status": status,
            "latency_ms": latency_ms,
            "detail": detail,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
