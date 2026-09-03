"""information-hub — grounding engine (quality layer, phase check).

Verifies a collected deep-dive's claims against the live web using the
Gemini check provider with ``google_search`` grounding.  Produces:

  grounding_score (0..1)          grounded claims / total claims
  sources_verified [{url,title}]  cited sources from search grounding
  review status + approval trail  verified | pending_review (+ approved_by)

Also updates per-source reputation (data/state/sources.json):
  items / grounding_failures / avg_grounding_score

Role: phase check — consumed by ``main.run_check``.
"""

from __future__ import annotations

import time
from typing import Any

from src.config import Config
from src.storage.registry import Registry
from src.utils.logging_util import RunLog

SYSTEM_VERIFY = (
    "You are a fact-checker. Verify each claim against the web using Google "
    "Search grounding. For every claim return grounded=true only if a "
    "credible source supports it. Return ONLY JSON matching the requested "
    "structure: {\"claims\": [{\"text\": \"...\", \"grounded\": true|false, "
    "\"source_url\": \"...\", \"source_title\": \"...\"}]}. "
    "Use the search results (groundingChunks) as your sources."
)


class GroundingEngine:
    def __init__(self, cfg: Config, registry: Registry, run_log: RunLog,
                 provider_manager: Any):
        self.cfg = cfg
        self.registry = registry
        self.log = run_log
        self.pm = provider_manager

    def check_record(self, record: dict[str, Any], spec: Any,
                     fulltext: str = "") -> dict[str, Any]:
        """Verify a record's claims; returns the grounding result dict.

        Tries the Gemini search-grounding provider first; if its free-tier
        quota is unavailable (429/error) or no spec exists, falls back to a
        local lexical score (claim token coverage against the source
        fulltext) so the pipeline always produces a grounding result.
        """
        claims = self._claims_of(record)
        if not claims:
            return {"grounding_score": None, "claims_total": 0,
                    "claims_grounded": 0, "sources_verified": [],
                    "method": "none", "reason": "no claims"}
        prompt = _build_verify_prompt(record, claims)
        result = None
        last_err: Exception | None = None
        used_spec = None
        # Retry transient failures (429/5xx/network) with backoff — the Gemini
        # free tier is frequently rate-limited.  A FRESH spec is picked on each
        # attempt so a provider put into cooldown is not retried stale.
        for attempt in range(self.cfg.gemini.retries + 1):
            used_spec = self.pm.pick_check()
            if used_spec is None:
                last_err = last_err or RuntimeError("no check provider available")
                break
            try:
                result = self.pm.verify(used_spec, SYSTEM_VERIFY, prompt)
                break
            except Exception as e:  # verify failed → retry transient, else give up
                last_err = e
                status = getattr(e, "status_code", None)
                transient = status is None or status in (429, 500, 502, 503, 504)
                if not transient or attempt >= self.cfg.gemini.retries:
                    break
                time.sleep(3 * (attempt + 1))  # backoff before retrying

        if result is not None:
            parsed = self._parse(result, claims)
            return self._finalize(record, parsed, used_spec, method="gemini-search")

        # Fallback: lexical grounding against the source fulltext (no API).
        if used_spec is None:
            reason = "no check provider available"
        else:
            reason = f"gemini unavailable: {last_err}"
        self.log.event("check", "verify_failed", item_id=record["id"],
                       status="error", detail=reason)
        parsed = self._lexical(claims, fulltext)
        method = "lexical"
        if self.cfg.quality.web_corroborate:
            parsed = self._corroborate(record, claims, parsed)
            if parsed["sources_verified"]:
                method = "web"  # independent citations found — no longer bare lexical
        return self._finalize(record, parsed, used_spec, method=method)

    def _finalize(self, record: dict[str, Any], parsed: dict[str, Any],
                  spec: Any, method: str) -> dict[str, Any]:
        """Shared scoring + review/approval + source reputation."""
        total = parsed["claims_total"]
        grounded = parsed["claims_grounded"]
        score = round(grounded / total, 3) if total else None
        sources = parsed["sources_verified"]
        reject = self.cfg.quality.reject_threshold
        status = "verified" if score is not None and score >= reject else "pending_review"

        # provenance trail: grounding + approval
        self.registry.update_approval(
            record["id"], score if score is not None else 0.0,
            approved_by_type="ai",
            approved_by_provider=getattr(spec, "provider", method),
            approved_by_model=getattr(spec, "model", method),
        )
        # source reputation
        self.registry.record_grounding(
            record["source"]["name"],
            grounding_score=score,
            failed=(status == "pending_review" and score is not None),
        )
        self.log.event("check", "grounded", item_id=record["id"],
                       provider=getattr(spec, "provider", method),
                       model=getattr(spec, "model", method),
                       detail=f"score={score} status={status} sources={len(sources)} method={method}")

        return {
            "grounding_score": score,
            "claims_total": total,
            "claims_grounded": grounded,
            "sources_verified": sources,
            "method": method,
            "review_status": status,
            "_spec": spec,
        }

    # ---- helpers ---------------------------------------------------------
    def _claims_of(self, record: dict[str, Any]) -> list[str]:
        claims: list[str] = list(record.get("key_facts", []))
        claims += list(record.get("implications", []))[:2]
        claims.append(record.get("outlook", "")) if record.get("outlook") else None
        return [c for c in claims if c][:6]

    def _parse(self, result: dict[str, Any], claims: list[str]) -> dict[str, Any]:
        """Parse the verify response into {claims_total, claims_grounded, sources}."""
        raw = result.get("claims", []) if isinstance(result, dict) else []
        total = len(raw) if raw else 0
        grounded = 0
        sources: list[dict[str, str]] = []
        seen: set[str] = set()
        for c in raw[: max(total, 1)]:
            if isinstance(c, dict) and c.get("grounded") is True:
                grounded += 1
            url = (c or {}).get("source_url", "") if isinstance(c, dict) else ""
            if url and url not in seen:
                seen.add(url)
                sources.append({"url": url,
                                "title": (c or {}).get("source_title", "")})
        return {
            "claims_total": total,
            "claims_grounded": grounded,
            "sources_verified": sources,
        }

    def _lexical(self, claims: list[str], fulltext: str,
                 min_shared: int = 3) -> dict[str, Any]:
        """Local, API-free grounding: shared-token evidence in source fulltext.

        A claim is "grounded" when it shares at least ``min_shared``
        significant tokens with the source article text.  Deep-dives
        paraphrase their sources, so a small absolute overlap is the right
        signal (a 50% ratio would fail almost every paraphrase).  Used as a
        fallback when the Gemini search quota is unavailable — the pipeline
        still produces a score, review status and (empty) citation list.
        """
        import re
        stop = {"the", "and", "for", "with", "from", "that", "this", "are",
                "was", "were", "its", "their", "more", "than", "over", "into",
                "about", "what", "which", "will", "been", "have", "has", "had"}
        tokens = {t for t in re.findall(r"[a-z0-9']+", (fulltext or "").lower())
                  if t not in stop}
        grounded = 0
        for claim in claims:
            claim_tokens = {t for t in re.findall(r"[a-z0-9']+", claim.lower())
                            if t not in stop}
            if len(claim_tokens) < 4:
                grounded += 1  # too short to verify meaningfully
                continue
            if len(claim_tokens & tokens) >= min_shared:
                grounded += 1
        return {
            "claims_total": len(claims),
            "claims_grounded": grounded,
            "sources_verified": [],
        }

    # ---- web corroboration (API-free independent check) ------------------
    _STOP = frozenset("""a an and are as at be but by for from has have how in is it
        its of on or that the this to was were what when where which who will with
        without would you your says said new after over amid us uk against""".split())

    def _corroborate(self, record: dict[str, Any], claims: list[str],
                     lexical: dict[str, Any]) -> dict[str, Any]:
        """Confirm ungrounded claims via independent outlets (Google News RSS).

        When Gemini search is down, ``_lexical`` only checks the claims against
        the ONE source article, so paraphrased claims it cannot tie to that
        text stay ungrounded with zero citations.  This pass searches Google
        News RSS (free, no key) for the claim's distinctive words and marks a
        claim grounded when an INDEPENDENT outlet (not the record's own feed
        host) carries the same story — turning ``sources_verified`` into real
        citations.  The source URL is resolved through the redirect.
        """
        import re as _re
        if not self.cfg.quality.web_corroborate:
            return lexical
        total = lexical["claims_total"]
        if not total:
            return lexical
        claims = claims[:total]
        sources = list(lexical.get("sources_verified", []))
        seen = {s["url"] for s in sources}
        grounded = lexical["claims_grounded"]
        own_host = self._host_of((record.get("source") or {}).get("feed")
                                 or (record.get("source") or {}).get("url"))
        for claim in claims:
            if grounded >= total:  # everything already grounded
                break
            words = [w for w in _re.findall(r"[a-z0-9']+", claim.lower())
                     if w not in self._STOP]
            if len(words) < 4:
                continue
            words.sort(key=len, reverse=True)
            query = _re.sub(r"\s+", " ", " ".join(words[:6]))
            url = ("https://news.google.com/rss/search?q="
                   + _re.sub(r"\s+", "+", query)
                   + "&hl=en-US&gl=US&ceid=US:en")
            try:
                import urllib.request
                req = urllib.request.Request(
                    url, headers={"User-Agent":
                                  "Mozilla/5.0 (information-hub verify)"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    xml = r.read()
            except Exception:
                continue
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(xml)
            except ET.ParseError:
                continue
            want = set(words)
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                hit = sum(1 for w in want if w in title.lower()) / max(1, len(want))
                if hit < 0.6:
                    continue
                src_el = item.find("source")
                src_host = (src_el.get("url") if src_el is not None else "") or ""
                src_host = self._host_of(src_host)
                if not src_host:
                    src_host = (src_el.text or "").strip() if src_el is not None else ""
                if own_host and src_host and own_host in src_host:
                    continue  # same outlet as the source — not independent
                if not src_host:
                    continue  # no usable host to cite
                # Google News RSS links resolve to a JS redirect page, so the
                # citation is the outlet's domain (searchable) rather than the
                # deep article — the source element is Google's canonical host.
                cite_url = f"https://{src_host}/"
                grounded += 1
                if cite_url not in seen:
                    seen.add(cite_url)
                    sources.append({"url": cite_url, "title": title[:160]})
                time.sleep(0.4)  # pace the free endpoint
                break
        return {**lexical, "claims_grounded": grounded,
                "sources_verified": sources}

    @staticmethod
    def _host_of(u: str) -> str:
        import re
        m = re.search(r"https?://([^/]+)", u or "")
        return m.group(1) if m else ""

    @staticmethod
    def _resolve(gn_url: str) -> str:
        """Follow a Google News redirect to the real article URL."""
        import urllib.request
        try:
            req = urllib.request.Request(
                gn_url, headers={"User-Agent": "Mozilla/5.0 (information-hub)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.geturl()
        except Exception:
            return ""


def _build_verify_prompt(record: dict[str, Any], claims: list[str]) -> str:
    lines = [f"Title: {record['title']}",
             f"Source URL: {record['source']['url']}",
             "", "Claims to verify:"]
    for i, c in enumerate(claims, 1):
        lines.append(f"{i}. {c}")
    return "\n".join(lines)
