"""information-hub — key-value registry (storage layer).

Tracks pipeline + content status for the whole system.  Files under
``data/state/`` (system state — the auto-run brain, NOT collected data):

  sources.json      per-source: last_fetched, candidates, published, errors,
                    grounding reputation (avg score / failures)
  items.json        per-item: status, word_count, provider/model,
                    grounding_score, review_status, approval trail
  meta.json         last run, quotas_used, processed ids, pipeline lock
  keys.json         (legacy V2) multi-key budget — kept for back-compat
  collections.json  per-collection due tracking (frequency / next_due)
  providers.json    per provider: daily token/item quotas, cooldown_until,
                    per-model health — the rate-limit gate state
  schedule.json     next-run times + target_remaining (run-control)
  run-log.jsonl     machine event log (full provenance trail)

Role: both phases — consumed by main, llm.providers, quality.grounding,
run.controller and run.scheduler.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from src.collect.dedup import title_hash, url_hash


class Registry:
    def __init__(self, registry_dir: Path):
        self.dir = registry_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.sources = self._load("sources.json")
        self.items = self._load("items.json")
        self.meta = self._load("meta.json")
        self.keys = self._load("keys.json")
        self.collections = self._load("collections.json")
        self.providers = self._load("providers.json")

    # ---- persistence -------------------------------------------------
    def _load(self, name: str) -> dict[str, Any]:
        path = self.dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        with self._lock:
            self._dump("sources.json", self.sources)
            self._dump("items.json", self.items)
            self._dump("meta.json", self.meta)
            self._dump("keys.json", self.keys)
            self._dump("collections.json", self.collections)
            self._dump("providers.json", self.providers)

    def reload(self) -> None:
        """Re-read all state files from disk.

        Used by the scheduler AFTER running a phase as a subprocess: the
        subprocess saves fresh state (e.g. items.json gains new records), and
        this instance must not overwrite it with its stale in-memory copy.
        """
        with self._lock:
            self.sources = self._load("sources.json")
            self.items = self._load("items.json")
            self.meta = self._load("meta.json")
            self.keys = self._load("keys.json")
            self.collections = self._load("collections.json")
            self.providers = self._load("providers.json")

    def _dump(self, name: str, data: dict[str, Any]) -> None:
        path = self.dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        tmp.replace(path)

    # ---- source status ----------------------------------------------
    def record_fetch(self, source_name: str, candidates: int, published: int,
                     error: str | None = None) -> None:
        entry = self.sources.setdefault(source_name, {})
        entry["last_fetched"] = _utcnow()
        entry["candidates_found"] = candidates
        entry["items_published"] = published
        entry["last_error"] = error
        # reputation defaults (V3 quality)
        entry.setdefault("grounding_failures", 0)
        entry.setdefault("grounding_scores", [])

    def record_grounding(self, source_name: str, grounding_score: float | None,
                         failed: bool = False) -> None:
        """Update source reputation with a grounding outcome."""
        entry = self.sources.setdefault(source_name, {})
        scores = entry.setdefault("grounding_scores", [])
        if grounding_score is not None:
            scores.append(grounding_score)
            if len(scores) > 50:  # keep bounded
                del scores[: len(scores) - 50]
        if failed:
            entry["grounding_failures"] = entry.get("grounding_failures", 0) + 1
        entry["avg_grounding_score"] = (
            round(sum(scores) / len(scores), 3) if scores else None
        )

    def source_stats(self, source_name: str) -> dict[str, Any]:
        return self.sources.get(source_name, {})

    # ---- item status -------------------------------------------------
    def has_seen(self, title: str, url: str) -> bool:
        """Exact dedup check against stored checksums."""
        if url and url_hash(url) in self.items:
            return True
        if title and title_hash(title) in self.items:
            return True
        return False

    def record_item(self, record: dict[str, Any], status: str,
                    gemini_calls: int, validated: bool,
                    provider: str = "", model: str = "") -> None:
        base = {
            "id": record["id"],
            "key": record["key"],
            "title_hash": title_hash(record["title"]),
            "url_hash": url_hash(record["source"]["url"]),
            "status": status,
            "word_count": record.get("word_count", 0),
            "gemini_calls": gemini_calls,
            "validated": validated,
            "provider": provider,
            "model": model,
            "grounding_score": None,
            "review_status": "pending_review",
            "approved_by": None,
            "approved_at": None,
        }
        self.items[url_hash(record["source"]["url"])] = base
        self.items[title_hash(record["title"])] = base

    def update_approval(self, item_id: str, grounding_score: float,
                        approved_by_type: str, approved_by_provider: str,
                        approved_by_model: str) -> None:
        """Record grounding result + approval trail for an item."""
        for entry in self.items.values():
            if entry.get("id") == item_id:
                entry["grounding_score"] = grounding_score
                entry["review_status"] = (
                    "verified" if grounding_score >= 0.5 else "pending_review"
                )
                entry["approved_by"] = {
                    "type": approved_by_type,
                    "provider": approved_by_provider,
                    "model": approved_by_model,
                }
                entry["approved_at"] = _utcnow()

    def item_status(self, item_id: str) -> dict[str, Any] | None:
        for entry in self.items.values():
            if entry.get("id") == item_id:
                return entry
        return None

    # ---- key budget (multi API key) ------------------------------------
    def key_stats(self, key: str) -> dict[str, Any]:
        return self.keys.setdefault(key, {"calls": 0, "errors": 0, "last_used": "",
                                          "failed": False})

    def record_key_use(self, key: str) -> None:
        stats = self.key_stats(key)
        stats["calls"] = stats.get("calls", 0) + 1
        stats["last_used"] = _utcnow()

    def record_key_result(self, key: str, status_code: int | None,
                          error: bool, max_errors: int = 3) -> None:
        stats = self.key_stats(key)
        if error:
            stats["errors"] = stats.get("errors", 0) + 1
        else:
            stats["errors"] = 0  # success resets the error streak
        if status_code is not None and status_code >= 400:
            # transient 429/500 don't disable permanently, but hard 4xx does
            if status_code not in (429, 500, 502, 503, 504):
                stats["failed"] = True
        if stats.get("errors", 0) >= max_errors:
            stats["failed"] = True

    # ---- collection due tracking (action control) -----------------------
    def collection_stats(self, name: str) -> dict[str, Any]:
        return self.collections.setdefault(name, {"last_run": None, "next_due": None,
                                                  "runs": 0})

    def record_collection_run(self, name: str, frequency: str, run_date: str) -> None:
        stats = self.collection_stats(name)
        stats["last_run"] = run_date
        stats["runs"] = stats.get("runs", 0) + 1
        stats["next_due"] = _next_due(run_date, frequency)

    def is_due(self, name: str, run_date: str, frequency: str) -> bool:
        """True if the collection should run on run_date given its frequency."""
        stats = self.collection_stats(name)
        next_due = stats.get("next_due")
        if not next_due:
            return True  # never ran → run now
        return next_due <= run_date

    # ---- provider quota/budget (V6 token-aware, persisted) ---------------
    def provider_state(self, provider: str) -> dict[str, Any]:
        """Provider-level state: daily quota counters + cooldown + models.

        Structure::

            {quota_date, tokens_used, items, calls, errors,
             cooldown_until, models: {model: {calls, items, errors, ...}}}

        Migrates the old ``{model: stats}`` shape on first access.
        """
        state = self.providers.setdefault(provider, {})
        if "models" not in state or not isinstance(state.get("models"), dict):
            # old shape: {model: stats} → new shape with a models sub-dict
            models = {k: v for k, v in state.items() if isinstance(v, dict)}
            state = {
                "quota_date": _utc_today(),
                "tokens_used": 0,
                "items": 0,
                "calls": 0,
                "errors": 0,
                "cooldown_until": None,
                "models": models,
            }
            self.providers[provider] = state
        state.setdefault("quota_date", _utc_today())
        state.setdefault("tokens_used", 0)
        state.setdefault("items", 0)
        state.setdefault("calls", 0)
        state.setdefault("errors", 0)
        state.setdefault("cooldown_until", None)
        state.setdefault("models", {})
        return state

    def reset_provider_quotas_if_new_day(self) -> None:
        """Reset per-provider daily counters when the UTC day changed.

        Called at run start — replaces the manual ``reset provider health``
        commits: tokens_used/items/calls/errors/cooldown_until all reset.
        """
        today = _utc_today()
        for provider in list(self.providers.keys()):
            state = self.provider_state(provider)
            if state.get("quota_date") != today:
                state["quota_date"] = today
                state["tokens_used"] = 0
                state["items"] = 0
                state["calls"] = 0
                state["errors"] = 0
                state["cooldown_until"] = None

    def provider_model_stats(self, provider: str, model: str) -> dict[str, Any]:
        state = self.provider_state(provider)
        stats = state["models"].setdefault(
            model, {"calls": 0, "items": 0, "errors": 0,
                    "consecutive_failures": 0, "healthy": True,
                    "last_health_check": None, "latency_ms": None,
                    "supports_json": True})
        return stats

    def record_provider_call(self, provider: str, model: str, *,
                             items: int = 0, tokens: int = 0,
                             latency_ms: int | None = None) -> None:
        state = self.provider_state(provider)
        state["calls"] = state.get("calls", 0) + 1
        state["items"] = state.get("items", 0) + items
        state["tokens_used"] = state.get("tokens_used", 0) + tokens
        stats = state["models"].setdefault(
            model, {"calls": 0, "items": 0, "errors": 0,
                    "consecutive_failures": 0, "healthy": True,
                    "last_health_check": None, "latency_ms": None,
                    "supports_json": True})
        stats["calls"] = stats.get("calls", 0) + 1
        stats["items"] = stats.get("items", 0) + items
        if latency_ms is not None:
            stats["latency_ms"] = latency_ms
        stats["consecutive_failures"] = 0
        stats["last_health_check"] = _utcnow()
        self.providers[provider] = state

    def record_provider_failure(self, provider: str, model: str,
                                mark_down: bool = False) -> None:
        state = self.provider_state(provider)
        state["errors"] = state.get("errors", 0) + 1
        stats = state["models"].setdefault(
            model, {"calls": 0, "items": 0, "errors": 0,
                    "consecutive_failures": 0, "healthy": True,
                    "last_health_check": None, "latency_ms": None,
                    "supports_json": True})
        stats["errors"] = stats.get("errors", 0) + 1
        stats["consecutive_failures"] = stats.get("consecutive_failures", 0) + 1
        stats["last_health_check"] = _utcnow()
        if mark_down:
            stats["healthy"] = False
        self.providers[provider] = state

    def provider_healthy(self, provider: str, model: str) -> bool:
        return bool(self.provider_model_stats(provider, model).get("healthy", True))

    # ---- persisted cooldown (rate-limit state across runs) ----------------
    def set_provider_cooldown(self, provider: str, until_iso: str | None) -> None:
        """Persist a provider cooldown deadline (ISO UTC) or clear it."""
        state = self.provider_state(provider)
        state["cooldown_until"] = until_iso
        self.providers[provider] = state

    def provider_cooldown_until(self, provider: str) -> str | None:
        return self.provider_state(provider).get("cooldown_until")

    # ---- provider aggregate budget (not per-model) ------------------------
    def provider_tokens_used(self, provider: str) -> int:
        return self.provider_state(provider).get("tokens_used", 0)

    def provider_items_used(self, provider: str) -> int:
        return self.provider_state(provider).get("items", 0)

    def provider_calls(self, provider: str, model: str) -> int:
        return self.provider_model_stats(provider, model).get("calls", 0)

    def provider_items(self, provider: str, model: str) -> int:
        return self.provider_model_stats(provider, model).get("items", 0)

    def set_provider_json_support(self, provider: str, model: str, supports: bool) -> None:
        self.provider_model_stats(provider, model)["supports_json"] = supports

    def reset_health_if_stale(self) -> None:
        """Re-enable models whose last health check was a previous day.

        Registry persists across runs (committed with the data), so models
        marked down during one run are retried once per day.  Comparison is
        done in UTC to match the ``last_health_check`` timestamps.
        """
        today = _utc_today()
        for provider in list(self.providers.keys()):
            state = self.provider_state(provider)
            for model, stats in state.get("models", {}).items():
                last = stats.get("last_health_check", "")
                if stats.get("healthy") is False and not last.startswith(today):
                    stats["healthy"] = True
                    stats["consecutive_failures"] = 0

    # ---- pipeline lock (scheduler double-safety) ---------------------------
    def acquire_lock(self) -> bool:
        """Try to acquire the pipeline lock; True if acquired."""
        if self.meta.get("lock"):
            return False
        self.meta["lock"] = _utcnow()
        return True

    def release_lock(self) -> None:
        self.meta["lock"] = None

    # ---- meta ---------------------------------------------------------
    def next_sequence(self, date: str) -> int:
        """Return the next NNN for a date based on keys already stored."""
        max_seq = 0
        for entry in self.items.values():
            key = entry.get("key", "")
            if key.startswith(date + "-"):
                try:
                    max_seq = max(max_seq, int(key.rsplit("-", 1)[1]))
                except (ValueError, IndexError):
                    continue
        return max_seq + 1

    def mark_run(self, processed: list[str], quota_used: int) -> None:
        self.meta["last_run"] = _utcnow()
        self.meta["last_processed"] = processed
        self.meta["quota_used"] = quota_used
        self.meta["total_items"] = len(set(
            e.get("id") for e in self.items.values() if e.get("id")
        ))


def _utcnow() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _utc_today() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _next_due(run_date: str, frequency: str) -> str:
    """Next scheduled date for a frequency (ISO date strings, UTC)."""
    import datetime as dt
    try:
        base = dt.date.fromisoformat(run_date)
    except ValueError:
        base = dt.date.today()
    if frequency == "weekly":
        return (base + dt.timedelta(days=7)).isoformat()
    if frequency == "every-2-days":
        return (base + dt.timedelta(days=2)).isoformat()
    return base.isoformat()  # daily → due every day (next_due == today)
