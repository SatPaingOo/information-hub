"""One-off maintenance: backfill real article URLs for old records.

Early records stored the RSS feed URL as ``source.url`` (or left it empty)
instead of the article link.  The fix that made fetchers store article URLs
only applied to NEW records — this script repairs history:

1. For each record missing a real article URL, take ``source.feed`` + date.
2. Find a Wayback Machine snapshot of that feed on the record's UTC day
   (±1 day) via the CDX API — the same feed the pipeline itself read.
3. Parse the archived feed (raw ``id_`` fetch, gzip-decompressed) into a
   normalized title → link map and match the record title (exact, then
   fuzzy ≥ 0.92).
4. Write the matched article link to ``source.url`` with provenance in
   ``source.url_source = "backfill:wayback-feed"``.

Nothing else in the record is touched.  Feed snapshots are cached per
(feed, timestamp) so repeated runs are cheap.

Usage:
    python scripts/backfill_source_urls.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import gzip
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

_ROOT = Path(__file__).resolve().parent.parent
DATA_SET = _ROOT / "data" / "collections" / "data-set"

_ARTICLE_RE = re.compile(r"\.(xml|rss|atom)(\?|$)|rss\.|/feed", re.IGNORECASE)
_UA = {"User-Agent": "Mozilla/5.0 (information-hub backfill)"}


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def is_article_url(u: str) -> bool:
    return bool(u) and not _ARTICLE_RE.search(u)


def feed_of(rec: dict) -> str:
    """The originating feed URL for a record.

    New records carry ``source.feed``; OLD records (pre-fix) stored the feed
    URL directly in ``source.url`` with no separate field — fall back to that.
    """
    src = rec.get("source") or {}
    if src.get("feed"):
        return src["feed"]
    u = src.get("url", "")
    if u and _ARTICLE_RE.search(u):
        return u
    return ""


def fetch_feed_links(url: str) -> dict[str, str]:
    """Normalized title → link map from an RSS/Atom document (gzip aware)."""
    raw = _get(url)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    links: dict[str, str] = {}
    for it in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if it.find("{http://www.w3.org/2005/Atom}link") is not None:
            link = it.find("{http://www.w3.org/2005/Atom}link").get("href", link)
        if title and link:
            links.setdefault(norm_title(title), link)
    return links


def wayback_snapshots(feed_url: str, frm: dt.date, to: dt.date) -> list[str]:
    """Archived feed timestamps across [frm, to] (oldest first).

    One CDX query per FEED (not per record) — the API throttles aggressively,
    so grouping the whole date range into a single call keeps us under it.
    """
    api = ("http://web.archive.org/cdx/search/cdx?url=" + feed_url
           + f"&from={frm.strftime('%Y%m%d')}&to={to.strftime('%Y%m%d')}"
           + "&output=json&filter=statuscode:200"
           + "&collapse=timestamp:8&limit=200")
    for attempt in (1, 2, 3):
        try:
            rows = json.loads(_get(api, timeout=60))
            return [r[1] for r in rows[1:]]
        except Exception as e:
            print(f"    ! cdx {feed_url} [{frm}..{to}] try {attempt}: {str(e)[:80]}")
            time.sleep(3 * attempt)
    return []


def archived_link_map(feed_url: str, ts: str, cache: dict) -> dict[str, str] | None:
    key = (feed_url, ts)
    if key in cache:
        return cache[key]
    url = f"http://web.archive.org/web/{ts}id_/{feed_url}"
    try:
        links = fetch_feed_links(url)
    except Exception as e:
        print(f"    ! snapshot {ts} failed: {e}")
        links = None
    time.sleep(0.5)  # be gentle with the Wayback API
    cache[key] = links
    return links


def match_link(title: str, links: dict[str, str]) -> str | None:
    key = norm_title(title)
    if key in links:
        return links[key]
    best, ratio = None, 0.0
    for cand, link in links.items():
        r = difflib.SequenceMatcher(None, key, cand).ratio()
        if r > ratio:
            best, ratio = link, r
    return best if ratio >= 0.92 else None


# ---- pass 2: Google News RSS search fallback ----

def gn_resolve(gn_url: str) -> str:
    """Resolve a Google News redirect link to the real article URL."""
    try:
        req = urllib.request.Request(gn_url, headers=_UA)
        r = urllib.request.urlopen(req, timeout=25)
        return r.geturl()
    except Exception:
        return gn_url


_STOP = set("""a an and are as at be but by for from has have how in is it its of on or
that the this to was were what when where which who will with without would you your
says said new after over amid as uk us against""".split())


def distinctive_tokens(title: str, limit: int = 6) -> set[str]:
    """Content words from a title, longest/most-specific first.

    Digest titles are often LLM rewrites of the real headline, so full-title
    matching fails — search on the distinctive words instead.
    """
    words = [w for w in norm_title(title).split() if w not in _STOP]
    words.sort(key=len, reverse=True)
    return set(words[:limit])


def arxiv_abs_link(title: str) -> str | None:
    """Resolve an arXiv abs page by exact title match (arXiv API)."""
    q = urllib.parse.quote(f'ti:"{title}"')
    u = "http://export.arxiv.org/api/query?search_query=" + q + "&max_results=3"
    try:
        xml = _get(u, timeout=30)
        root = ET.fromstring(xml)
    except Exception:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    want = norm_title(title)
    for e in root.findall("a:entry", ns):
        t = norm_title(e.findtext("a:title", namespaces=ns) or "")
        if t == want:
            return (e.findtext("a:id", namespaces=ns) or "").strip()
    return None


def google_news_search_link(title: str, day_s: str) -> str | None:
    """Resolve an article URL via Google News RSS search.

    Scores each result by distinctive-token coverage of the record title,
    plus a strong bonus when the result's source host matches the record's
    originating feed host and pubDate is within ±2 days.  Returns the best
    candidate's REAL source URL, or None when nothing clears the bar.
    """
    tokens = distinctive_tokens(title)
    if not tokens:
        return None
    q = urllib.parse.quote(" ".join(tokens))
    u = ("https://news.google.com/rss/search?q=" + q
         + "&hl=en-US&gl=US&ceid=US:en&when=30d")
    try:
        xml = _get(u, timeout=25)
        root = ET.fromstring(xml)
    except Exception:
        return None
    feed_host = ""  # filled by caller via param if needed
    day = dt.date.fromisoformat(day_s) if len(day_s) == 10 else None
    best, best_score = None, 0.0
    items = root.findall(".//item")
    for it in items[:15]:
        t = (it.findtext("title") or "").strip()
        pd = (it.findtext("pubDate") or "")[:16]
        if not t:
            continue
        cov = len(tokens & set(norm_title(t).split())) / len(tokens)
        score = cov
        # Date bonus: result published on the record's day ±2
        try:
            pd_d = dt.datetime.strptime(pd, "%a, %d %b %Y").date()
            if day and abs((pd_d - day).days) <= 2:
                score = cov * 0.6 + 0.4
        except ValueError:
            pass
        if score > best_score:
            best, best_score = (t, it.findtext("link")), score
    if best is None or best_score < 0.7:
        return None
    _, link = best
    if not link:
        return None
    time.sleep(0.3)
    real = gn_resolve(link)
    return None if "google.com/rss/articles" in real else real

def article_title(url: str) -> str | None:
    """Best-effort og:title / <title> from an article page."""
    try:
        html = _get(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return None
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html)
    return m.group(1).strip() if m else None


def token_coverage(a: str, b: str) -> float:
    """Fraction of a's tokens present in b (order-insensitive).

    Article titles often add a byline/prefix ("Dan Driscoll: US army
    secretary …") — a raw SequenceMatcher ratio then falls below a sane bar
    even though the record title is fully contained.  Coverage of the
    SHORTER title's tokens handles this.
    """
    ta = set(norm_title(a).split())
    tb = set(norm_title(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def bbc_search_link(title: str, day_s: str) -> str | None:
    """Resolve a BBC article URL via BBC site search, VERIFIED by title.

    Fetch the search page (no date filter — old articles are indexed),
    collect candidate /news/articles/ URLs, fetch each candidate and compare
    its og:title to the record title by token coverage (≥0.85) with a
    SequenceMatcher tiebreak.  Returns the best verified match, or None.
    """
    q = urllib.parse.quote(title)
    u = f"https://www.bbc.co.uk/search?q={q}"
    try:
        html = _get(u, timeout=25).decode("utf-8", "ignore")
    except Exception:
        return None
    links = list(dict.fromkeys(
        re.findall(r'href="(https://www\.bbc\.co\.uk/news/articles/[a-z0-9]+)"', html)))
    best, best_score = None, 0.0
    for cand in links[:5]:
        t = article_title(cand)
        if not t:
            continue
        cov = token_coverage(title, t)
        if cov > best_score:
            best, best_score = cand, cov
        time.sleep(0.35)
    return best if best_score >= 0.85 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pass2", action="store_true",
                    help="also run the BBC site-search fallback for BBC "
                         "records no Wayback snapshot matched")
    args = ap.parse_args()

    files = sorted(DATA_SET.glob("*.json"))
    todo: list[tuple[Path, dict]] = []
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        src = rec.get("source") or {}
        if not is_article_url(src.get("url", "")):
            todo.append((f, rec))
    print(f"records needing backfill: {len(todo)}/{len(files)}")

    # One CDX query per feed spanning all its records' dates (API throttles
    # hard — per-record queries got rate-limited into empty results).
    need = [(f, rec) for f, rec in todo if feed_of(rec)]
    by_feed: dict[str, list[dt.date]] = {}
    for _, rec in need:
        day = dt.date.fromisoformat(rec["date"][:10])
        feed = feed_of(rec)
        lo, hi = by_feed.get(feed, (day, day))
        by_feed[feed] = (min(lo, day), max(hi, day))
    snaps: dict[str, list[tuple[dt.date, str]]] = {}
    for feed, (lo, hi) in by_feed.items():
        ts_list = wayback_snapshots(feed, lo - dt.timedelta(days=1),
                                    hi + dt.timedelta(days=1))
        snaps[feed] = [(dt.datetime.strptime(t[:8], "%Y%m%d").date(), t)
                       for t in ts_list]
        print(f"  {feed}: {len(ts_list)} snapshots [{lo} .. {hi}]")

    link_cache: dict = {}
    matched = unmatched = 0
    for f, rec in todo:
        src = rec.get("source") or {}
        feed = feed_of(rec)
        title = rec.get("title", "")
        day_s = rec.get("date", "")[:10]
        day = dt.date.fromisoformat(day_s) if len(day_s) == 10 else None
        link = None
        method = None

        if feed and day:
            for sday, ts in snaps.get(feed, []):
                if abs((sday - day).days) > 3:
                    continue  # BBC items stay ~24-48h in the feed — a
                    # snapshot ±3 days may still carry the record's story
                links = archived_link_map(feed, ts, link_cache)
                if not links:
                    continue
                link = match_link(title, links)
                if link:
                    method = "wayback-feed"
                    print(f"  ✓ {day_s} [{ts}] {title[:55]}")
                    print(f"      → {link[:90]}")
                    break
        if not link and args.pass2 and title:
            # Pass 2 — records whose day had no Wayback snapshot.
            if "arxiv" in str(src) or "arxiv" in title.lower():
                link = arxiv_abs_link(title)
                method = "arxiv"
                tag = "[arxiv]"
            else:
                link = google_news_search_link(title, day_s)
                method = "gn-search"
                tag = "[gn-search]"
            if link and "google.com/rss/articles" not in link:
                print(f"  ✓ {day_s} {tag} {title[:55]}")
                print(f"      → {link[:90]}")
            else:
                link = None
        if not link:
            unmatched += 1
            print(f"  ✗ {day_s} no match: {title[:60]}")
            continue
        matched += 1
        if not args.dry_run:
            src["url"] = link
            src["url_source"] = f"backfill:{method}"
            src["feed"] = feed  # normalize old records onto the new schema
            rec["source"] = src
            f.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    print(f"\nmatched: {matched}, unmatched: {unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
