"""information-hub — dataset store (storage layer).

Writes the three-way collection store:

  raws/     AI output data frame per run (UTC filename) — audit + dedup ref
  preview/  human .md per record — FLAT, filename = ``<key>-<title-slug>.md``
  data-set/ machine .json per record — FLAT, filename = ``<key>-<title-slug>.json``

Classification (content_type/topic/region/categories) lives inside each
record as metadata — it is data, not a folder path.  The filename is derived
from the AI-generated content title so a quick glance reveals the story.

Role: both phases — consumed by main and storage.indexer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.render.markdown import record_to_markdown
from src.storage.naming import record_filename, slugify  # re-exported for callers


class Store:
    """Writes canonical records (data-set .json + preview .md) flat.

    Args:
        data_dir: repo ``data`` directory (contains ``collections/``).
    """

    def __init__(self, data_dir: Path):
        base = Path(data_dir)
        self.collections = base / "collections"
        self.raws = self.collections / "raws"
        self.preview = self.collections / "preview"
        self.data_set = self.collections / "data-set"
        for d in (self.raws, self.preview, self.data_set):
            d.mkdir(parents=True, exist_ok=True)

    # ---- raws (AI output data frame) --------------------------------
    def write_raw_run(self, run_timestamp: str, records: list[dict[str, Any]]) -> Path:
        """Write the raw AI output frame for one run (UTC timestamp filename)."""
        safe = run_timestamp.replace(":", "-")
        path = self.raws / f"{safe}.json"
        frame = {
            "run_timestamp": run_timestamp,
            "items": records,
            "item_count": len(records),
        }
        path.write_text(json.dumps(frame, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return path

    # ---- canonical record (data-set .json + preview .md) ------------
    def write_record(self, record: dict[str, Any]) -> Path:
        """Store one record flat: ``data-set/<key>-<slug>.json`` + preview.

        Args:
            record: schema-valid deep-dive record (has ``key`` + ``title``).

        Returns:
            The path of the written ``.json`` file.
        """
        name = record_filename(record)

        json_path = self.data_set / f"{name}.json"
        json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

        md_path = self.preview / f"{name}.md"
        md_path.write_text(record_to_markdown(record) + "\n", encoding="utf-8")
        return json_path

    # ---- readers (used by indexer/query) ------------------------------
    def iter_records(self) -> list[dict[str, Any]]:
        """Load every canonical ``.json`` record (flat data-set directory)."""
        records: list[dict[str, Any]] = []
        for path in sorted(self.data_set.glob("*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return records

    def iter_raw_frames(self, run_date: str) -> list[dict[str, Any]]:
        """Load the raw AI output frames for a date (check-phase grounding).

        Args:
            run_date: ISO date (``YYYY-MM-DD``) — matches the frame filename
                prefix ``<run_timestamp>.json``.

        Returns:
            List of raw frame dicts for that date (each frame has ``items``
            which may carry a ``fulltext`` field used for lexical grounding).
        """
        frames: list[dict[str, Any]] = []
        for path in sorted(self.raws.glob(f"{run_date}*.json")):
            try:
                frames.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return frames
