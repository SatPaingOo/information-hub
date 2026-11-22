"""One-off maintenance: relabel fallback-checked records that say "verified".

Until 2026-11-22 the check phase marked a record ``verified`` whenever its
grounding score passed the threshold, even when Gemini search was down and
only the lexical / web-corroboration fallback ran.  Gemini never answered in
that period, so those records were never verified (golden rule #1).

This script repairs history with the same rule the check phase uses now
(``src.quality.grounding.review_status_for``):

1. For each record whose ``review.status`` differs from what its
   ``grounding`` result allows (in practice: ``verified`` but
   ``grounding.method`` is not ``gemini-search``), set the status the rule
   gives (``provisional``) and clear ``approved_by`` / ``approved_at``.
2. The old review block is NOT thrown away: it is appended to
   ``review.history`` with ``changed_at`` and ``reason``, so the original
   approval trail stays readable.
3. Matching entries in ``data/state/items.json`` get the same status and lose
   the approver.
4. Views (``data/views/**``) are rebuilt from the corrected records.

Nothing else in a record is touched.  Safe to run twice (the second run finds
nothing to change).

Usage:
    python scripts/relabel_fallback_reviews.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.config import Config  # noqa: E402
from src.quality.grounding import review_status_for  # noqa: E402
from src.storage.indexer import Indexer  # noqa: E402
from src.storage.registry import Registry  # noqa: E402
from src.storage.store import Store  # noqa: E402

REASON = ("relabel: fallback check (method={method}) cannot mark an item "
          "verified; only the Gemini search check can")


def corrected_review(record: dict, threshold: float, now: str) -> dict | None:
    """The fixed review block for ``record``, or None when it is already right."""
    review = record.get("review")
    grounding = record.get("grounding")
    if not isinstance(review, dict) or not isinstance(grounding, dict):
        return None
    method = grounding.get("method") or ""
    want = review_status_for(grounding.get("grounding_score"), method, threshold)
    if review.get("status") == want:
        return None
    if review.get("status") != "verified":
        return None   # only repair over-claimed approvals; never upgrade a status here
    old = {k: v for k, v in review.items() if k != "history"}
    return {
        "status": want,
        "approved_by": None,
        "approved_at": None,
        "history": list(review.get("history") or [])
        + [{**old, "changed_at": now, "reason": REASON.format(method=method or "none")}],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args(argv)

    cfg = Config.load()
    threshold = cfg.quality.reject_threshold
    data_dir = cfg.storage.data_dir
    data_set = data_dir / "collections" / "data-set"
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    changed: dict[str, str] = {}          # record id -> new status
    by_change: Counter[str] = Counter()
    for path in sorted(data_set.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        fixed = corrected_review(record, threshold, now)
        if fixed is None:
            continue
        method = (record.get("grounding") or {}).get("method") or "none"
        by_change[f"{record['review'].get('status')} -> {fixed['status']} ({method})"] += 1
        changed[record["id"]] = fixed["status"]
        if not args.dry_run:
            record["review"] = fixed
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    registry = Registry(data_dir / "state")
    reg_fixed = 0
    for entry in registry.items.values():
        new = changed.get(entry.get("id"))
        if new and entry.get("review_status") != new:
            reg_fixed += 1
            if not args.dry_run:
                entry["review_status"] = new
                entry["approved_by"] = None
                entry["approved_at"] = None

    for k, v in sorted(by_change.items()):
        print(f"{v:5d}  {k}")
    print(f"record files: {sum(by_change.values())} relabeled "
          f"({len(changed)} unique ids) · registry entries: {reg_fixed}"
          + (" (dry run, nothing written)" if args.dry_run else ""))

    if changed and not args.dry_run:
        registry.save()
        store = Store(data_dir)
        Indexer(data_dir).rebuild(store.iter_records(), taxonomy=cfg.taxonomy,
                                  relations=cfg.relations)
        print("views rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
