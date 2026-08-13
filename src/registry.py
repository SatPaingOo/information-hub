"""information-hub — key-value registry (pipeline + content status tracking).

Files under data/collections/registry/:
  sources.json  per-source: last_fetched, candidates_found, items_published, last_error
  items.json    per-item: status, word_count, gemini_calls, validated, checksums
  meta.json     last_run, quotas_used, processed_ids (dedup reference)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from src.dedup import title_hash, url_hash


class Registry:
    def __init__(self, registry_dir: Path):
        self.dir = registry_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.sources = self._load("sources.json")
        self.items = self._load("items.json")
        self.meta = self._load("meta.json")

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
                    gemini_calls: int, validated: bool) -> None:
        self.items[url_hash(record["source"]["url"])] = {
            "id": record["id"],
            "key": record["key"],
            "title_hash": title_hash(record["title"]),
            "url_hash": url_hash(record["source"]["url"]),
            "status": status,
            "word_count": record.get("word_count", 0),
            "gemini_calls": gemini_calls,
            "validated": validated,
        }
        self.items[title_hash(record["title"])] = {
            "id": record["id"],
            "key": record["key"],
            "title_hash": title_hash(record["title"]),
            "url_hash": url_hash(record["source"]["url"]),
            "status": status,
            "word_count": record.get("word_count", 0),
            "gemini_calls": gemini_calls,
            "validated": validated,
        }

    def item_status(self, item_id: str) -> dict[str, Any] | None:
        for entry in self.items.values():
            if entry.get("id") == item_id:
                return entry
        return None

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
