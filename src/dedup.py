"""information-hub — duplicate control.

Layers:
1. exact: source URL hash + normalized title hash (registry-based, O(1))
2. similarity: token-overlap ratio + entity overlap vs recent items
3. (Gemini selector receives duplicate flags and excludes duplicates)
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

TOKEN_RE = re.compile(r"[a-z0-9']+")


def normalize_title(title: str) -> str:
    return " ".join(TOKEN_RE.findall(title.lower()))


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def token_overlap(a: str, b: str) -> float:
    """Jaccard-ish overlap of token sets, 0..1."""
    ta = set(TOKEN_RE.findall(a.lower()))
    tb = set(TOKEN_RE.findall(b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_exact_duplicate(registry_items: dict[str, Any], title: str, url: str) -> bool:
    """True if this title/url was already stored (by hash)."""
    if not url or not title:
        return False
    if url_hash(url) in registry_items:
        return True
    if title_hash(title) in registry_items:
        return True
    return False


def similarity_flags(recent_records: list[dict[str, Any]],
                     title: str, summary: str = "",
                     entities: list[dict[str, Any]] | None = None,
                     threshold: float = 0.55) -> dict[str, Any]:
    """Compare against recent records; return best-match info + flag."""
    entities = entities or []
    best: dict[str, Any] = {"duplicate": False, "score": 0.0, "against": None}
    haystack_title = title + " " + summary
    for rec in recent_records:
        other_title = rec.get("title", "")
        other_summary = rec.get("tldr", "")
        score = token_overlap(haystack_title, other_title + " " + other_summary)
        if score > best["score"]:
            best["score"] = score
            best["against"] = rec.get("id")
    if best["score"] >= threshold:
        best["duplicate"] = True
    return best


def entity_overlap(recent_records: list[dict[str, Any]],
                   entities: list[dict[str, Any]]) -> list[str]:
    """Return names present in both candidate entities and recent records."""
    new_names = {e.get("name", "").lower() for e in entities}
    if not new_names:
        return []
    hits: set[str] = set()
    for rec in recent_records:
        for e in rec.get("entities", []):
            name = e.get("name", "").lower()
            if name in new_names:
                hits.add(name)
    return sorted(hits)
