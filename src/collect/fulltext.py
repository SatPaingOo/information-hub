"""information-hub — full-text extraction (collect layer).

Fetches the article page and extracts readable text (trafilatura with a
raw-strip fallback), capped at ``content.fulltext_max_chars``.  The extracted
text feeds the deep-dive prompt and grounding verification.

Role: phase collect — consumed by ``main.run_collect``.
"""

from __future__ import annotations

import re
from typing import Any

import requests

UA = {"User-Agent": "information-hub/0.1 (research aggregator)"}


def extract(url: str, max_chars: int = 6000, timeout: int = 25) -> str:
    """Return clean article text (capped), or "" on failure."""
    html = _fetch(url, timeout)
    if not html:
        return ""
    text = _trafilatura_extract(html)
    if not text:
        text = _fallback_strip(html)
    return text[:max_chars]


def _fetch(url: str, timeout: int) -> str:
    try:
        resp = requests.get(url, headers=UA, timeout=timeout)
        if resp.status_code != 200:
            return ""
        return resp.text
    except requests.RequestException:
        return ""


def _trafilatura_extract(html: str) -> str:
    try:
        import trafilatura
        return trafilatura.extract(html, include_comments=False,
                                   include_tables=False) or ""
    except Exception:
        return ""


def _fallback_strip(html: str) -> str:
    """Minimal HTML-to-text fallback (script/style removal + tag strip)."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
