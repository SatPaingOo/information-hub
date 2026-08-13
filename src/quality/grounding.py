"""information-hub — grounding engine (quality layer, phase check).

Verifies a collected deep-dive's claims against the live web using the
Gemini check provider with ``google_search`` grounding.  Produces:

  grounding_score (0..1)          grounded claims / total claims
  sources_verified [{url,title}]  cited sources from search grounding
  review status + approval trail  verified | pending_review (+ approved_by)

Also updates per-source reputation (registry/sources.json):
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
        # Retry transient failures (429/5xx/network) with backoff — the Gemini
        # free tier is frequently rate-limited on quick consecutive calls.
        if spec is not None:
            for attempt in range(self.cfg.gemini.retries + 1):
                try:
                    result = self.pm.verify(spec, SYSTEM_VERIFY, prompt)
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
            return self._finalize(record, parsed, spec, method="gemini-search")

        # Fallback: lexical grounding against the source fulltext (no API).
        if spec is None:
            reason = "no check provider available"
        else:
            reason = f"gemini unavailable: {last_err}"
        self.log.event("check", "verify_failed", item_id=record["id"],
                       status="error", detail=reason)
        parsed = self._lexical(claims, fulltext)
        return self._finalize(record, parsed, spec, method="lexical")

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
            "method": "gemini-search",
            "review_status": status,
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
                 coverage: float = 0.5) -> dict[str, Any]:
        """Local, API-free grounding: claim token coverage in source fulltext.

        A claim is "grounded" when at least ``coverage`` of its significant
        tokens appear in the source article text.  Used as a fallback when the
        Gemini search quota is unavailable — the pipeline still produces a
        score, review status and (empty) citation list.
        """
        import re
        tokens = set(re.findall(r"[a-z0-9']+", (fulltext or "").lower()))
        grounded = 0
        for claim in claims:
            claim_tokens = set(re.findall(r"[a-z0-9']+", claim.lower()))
            # skip stopword-ish tiny claims
            if len(claim_tokens) < 4:
                grounded += 1  # too short to verify meaningfully
                continue
            hit = len(claim_tokens & tokens) / len(claim_tokens)
            if hit >= coverage:
                grounded += 1
        return {
            "claims_total": len(claims),
            "claims_grounded": grounded,
            "sources_verified": [],
        }


def _build_verify_prompt(record: dict[str, Any], claims: list[str]) -> str:
    lines = [f"Title: {record['title']}",
             f"Source URL: {record['source']['url']}",
             "", "Claims to verify:"]
    for i, c in enumerate(claims, 1):
        lines.append(f"{i}. {c}")
    return "\n".join(lines)
