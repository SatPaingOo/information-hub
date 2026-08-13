"""information-hub — source fetchers.

Generic collectors for RSS, arXiv, Hacker News, and GitHub trending.
Each returns a list of Candidate dicts with a stable shape:

    {
      "collection": str,
      "source": {"name": str, "url": str, "type": str},
      "title": str,
      "url": str,
      "summary": str,          # snippet from feed/API
      "published": str | None, # ISO date
    }
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

import feedparser
import requests

UA = {"User-Agent": "information-hub/0.1 (research aggregator)"}


class Candidate:
    __slots__ = ("collection", "source", "title", "url", "summary", "published",
                 "key", "stable_id", "date")

    def __init__(self, collection: str, source: dict[str, str], title: str,
                 url: str, summary: str = "", published: str | None = None,
                 key: str | None = None, stable_id: str | None = None,
                 date: str | None = None):
        self.collection = collection
        self.source = source
        self.title = title
        self.url = url
        self.summary = summary
        self.published = published
        self.key = key
        self.stable_id = stable_id
        self.date = date

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published": self.published,
        }


def fetch_rss(url: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Parse an RSS/Atom feed into raw entries."""
    try:
        feed = feedparser.parse(url, request_headers=UA)
    except Exception:
        return []
    out = []
    for e in feed.entries[:30]:
        published = None
        for key in ("published", "updated", "date"):
            if e.get(key):
                try:
                    parsed = dt.datetime(*e.updated_parsed[:6]) if e.get("updated_parsed") else None
                    if parsed is None and e.get(key):
                        # best-effort ISO parse
                        parsed = _parse_iso(e[key])
                    published = parsed.isoformat(timespec="seconds") if parsed else None
                    break
                except Exception:
                    continue
        out.append({
            "title": getattr(e, "title", "") or "",
            "url": getattr(e, "link", "") or "",
            "summary": (getattr(e, "summary", "") or "").strip(),
            "published": published,
        })
    return out


def _parse_iso(text: str) -> dt.datetime | None:
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def fetch_arxiv(query: str, max_results: int = 20, timeout: int = 20) -> list[dict[str, Any]]:
    """Query the arXiv API (Atom)."""
    params = {"search_query": query, "start": 0, "max_results": max_results}
    url = "http://export.arxiv.org/api/query"
    try:
        resp = requests.get(url, params=params, headers=UA, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    feed = feedparser.parse(resp.content)
    out = []
    for e in feed.entries[:max_results]:
        summary = re.sub(r"\s+", " ", getattr(e, "summary", "")).strip()
        out.append({
            "title": getattr(e, "title", "").replace("\n", " ").strip(),
            "url": getattr(e, "link", "") or "",
            "summary": summary[:1200],
            "published": _feed_date(e),
        })
    return out


def _feed_date(entry: Any) -> str | None:
    try:
        if entry.get("published_parsed"):
            return dt.datetime(*entry.published_parsed[:6]).isoformat(timespec="seconds")
    except Exception:
        pass
    return None


def fetch_hackernews(max_results: int = 20, timeout: int = 20) -> list[dict[str, Any]]:
    """Top Hacker News stories via the Firebase API."""
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers=UA, timeout=timeout,
        )
        resp.raise_for_status()
        ids = resp.json()[:max_results]
    except requests.RequestException:
        return []
    out = []
    for item_id in ids:
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
                headers=UA, timeout=timeout,
            )
            item = r.json()
        except requests.RequestException:
            continue
        if not item or item.get("type") != "story" or not item.get("title"):
            continue
        out.append({
            "title": item["title"],
            "url": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
            "summary": (item.get("text") or "")[:800],
            "published": dt.datetime.fromtimestamp(
                item.get("time", 0), tz=dt.timezone.utc
            ).isoformat(timespec="seconds") if item.get("time") else None,
        })
    return out


def fetch_github_trending(topic: str = "machine-learning", timeout: int = 20) -> list[dict[str, Any]]:
    """GitHub repos for a topic, sorted by stars (search API, no auth)."""
    params = {"q": f"topic:{topic}", "sort": "stars", "order": "desc", "per_page": 20}
    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params=params, headers=UA, timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except requests.RequestException:
        return []
    out = []
    for it in items:
        out.append({
            "title": f"{it.get('full_name')} — {it.get('description') or ''}"[:200],
            "url": it.get("html_url") or "",
            "summary": (it.get("description") or "")[:800],
            "published": it.get("pushed_at"),
        })
    return out


# type -> fetcher mapping (feed entry -> candidate)
_FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "rss": fetch_rss,
    "arxiv": fetch_arxiv,
    "hackernews": fetch_hackernews,
    "github": fetch_github_trending,
}


def fetch_collection(collection_name: str, sources: list[dict[str, Any]],
                     max_candidates: int) -> list[Candidate]:
    """Fetch candidates for one collection from its configured sources."""
    candidates: list[Candidate] = []
    for src in sources:
        src_type = src.get("type", "")
        fetcher = _FETCHERS.get(src_type)
        if not fetcher:
            continue
        kwargs: dict[str, Any] = {"timeout": 20}
        if src_type == "arxiv":
            kwargs["query"] = src.get("query", "cat:cs.AI")
            kwargs["max_results"] = max_candidates
        elif src_type == "rss":
            kwargs["url"] = src.get("url", "")
        elif src_type == "github":
            kwargs["topic"] = src.get("topic", "machine-learning")
            kwargs["max_results"] = max_candidates
        elif src_type == "hackernews":
            kwargs["max_results"] = max_candidates

        try:
            entries = fetcher(**kwargs)
        except Exception:
            entries = []
        for e in entries[:max_candidates]:
            if not e.get("title") or not e.get("url"):
                continue
            candidates.append(Candidate(
                collection=collection_name,
                source={"name": _source_name(src_type, src), "url": src.get("url", ""),
                        "type": src_type},
                title=e["title"],
                url=e["url"],
                summary=e.get("summary", ""),
                published=e.get("published"),
            ))
    # cap + de-dup same url within this fetch
    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = c.url.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique[:max_candidates]


def _source_name(src_type: str, src: dict[str, Any]) -> str:
    if src_type == "arxiv":
        return "arXiv"
    if src_type == "github":
        return f"github:{src.get('topic', '')}"
    if src_type == "hackernews":
        return "HackerNews"
    url = src.get("url", "")
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host or "rss"
