"""information-hub — quality / grounding engine (phase check).

Verifies a collected deep-dive's claims against the live web using the
Gemini check provider with google_search grounding. Produces:

  grounding_score (0..1)          grounded claims / total claims
  sources_verified [{url,title}]  cited sources from search grounding
  review status + approval trail  verified | pending_review (+ approved_by)

Also updates per-source reputation (registry/sources.json):
  items / grounding_failures / avg_grounding_score
"""

from __future__ import annotations

from typing import Any

from src.config import Config
from src.logging_util import RunLog
from src.registry import Registry

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

    def check_record(self, record: dict[str, Any], spec: Any) -> dict[str, Any]:
        """Verify a record's claims; returns the grounding result dict."""
        claims = self._claims_of(record)
        if not claims:
            return {"grounding_score": None, "claims_total": 0,
                    "claims_grounded": 0, "sources_verified": [],
                    "method": "none", "reason": "no claims"}
        prompt = _build_verify_prompt(record, claims)
        try:
            result = self.pm.verify(spec, SYSTEM_VERIFY, prompt)
            parsed = self._parse(result, claims)
        except Exception as e:  # verification unavailable → leave unverified
            self.log.event("check", "verify_failed", item_id=record["id"],
                           status="error", detail=str(e))
            return {"grounding_score": None, "claims_total": len(claims),
                    "claims_grounded": 0, "sources_verified": [],
                    "method": "gemini-search", "reason": f"verify error: {e}"}

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
            approved_by_provider=spec.provider,
            approved_by_model=spec.model,
        )
        # source reputation
        self.registry.record_grounding(
            record["source"]["name"],
            grounding_score=score,
            failed=(status == "pending_review" and score is not None),
        )
        self.log.event("check", "grounded", item_id=record["id"],
                       provider=spec.provider, model=spec.model,
                       detail=f"score={score} status={status} sources={len(sources)}")

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


def _build_verify_prompt(record: dict[str, Any], claims: list[str]) -> str:
    lines = [f"Title: {record['title']}",
             f"Source URL: {record['source']['url']}",
             "", "Claims to verify:"]
    for i, c in enumerate(claims, 1):
        lines.append(f"{i}. {c}")
    return "\n".join(lines)
