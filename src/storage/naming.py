"""information-hub — shared filesystem-naming helpers (storage/render layer).

Both the writers (store/indexer) and the Obsidian renderers must agree on
how note files are named, because wikilinks point at the note BASENAME:

  item notes    preview/<key>-<title-slug>.md     -> ``record_filename``
  entity notes  preview/entities/<type>/<safe>.md -> ``safe_name``
  taxonomy      preview/taxonomy/<layer>/<safe>.md -> ``safe_name``

Keeping the helpers here (instead of inside store/indexer) lets
``src.render.markdown`` use the exact same naming without a circular import.
"""

from __future__ import annotations

import re
from typing import Any


def slugify(title: str, max_len: int = 60) -> str:
    """Convert a title into a filesystem-safe slug.

    Lowercases, keeps alphanumerics and spaces→hyphens, collapses repeats,
    strips edges and caps the length (keeping whole words where possible).

    Returns:
        A slug like ``"myanmar-economy-ministry-investment"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(slug) <= max_len:
        return slug
    # trim to whole words within the cap
    cut = slug[:max_len].rstrip("-")
    if "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut or slug[:max_len]


def record_filename(record: dict[str, Any]) -> str:
    """Flat readable filename for a record: ``<key>-<title-slug>``."""
    return f"{record['key']}-{slugify(record['title'])}"


def safe_name(name: str) -> str:
    """Obsidian note-safe name: non-alphanumerics become ``_``.

    Used for entity + taxonomy note FILES (``entities/<type>/<safe>.md``),
    so wikilinks to those notes must use the same transformation.
    """
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def win_safe(name: str) -> str:
    """Filename-safe on Windows: replace characters invalid in NTFS paths.

    ``by-entity`` JSON views keep raw names (spaces/unicode), so only the
    characters Windows forbids are replaced.
    """
    return "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in name)
