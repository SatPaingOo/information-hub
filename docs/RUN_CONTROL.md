# Run Control — the dynamic self-scheduling engine

information-hub has **no fixed run times**. A "run-control engine" decides
*when* collection is actually possible (based on provider rate-limit state and
daily budgets) and points the GitHub Actions cron at exactly that moment.

## Problem it solves

Free-tier providers have hard limits:

- **Groq** — tokens-per-minute (TPM) quotas; a deep-dive (~1500–2000 output
  tokens) exhausts the free TPM after a couple of items.
- **OpenRouter** — its free model pool is frequently 429 "temporarily
  rate-limited upstream".
- **Gemini** — search-grounding has a small free daily quota.

A naive scheduler (fixed cron) either hammers rate-limited providers or sits
idle; a naive budget (call/item counts) ignores tokens, so Groq's TPM blows
through mid-run. This engine makes the pipeline **self-aware** of its rate
limits.

## The pre-call gate (`ProviderManager.can_call`)

Before ANY HTTP request, a candidate model must pass:

1. **Persisted cooldown** — is the provider's `cooldown_until`
   (`data/state/providers.json`) in the future? (set on 429/5xx, survives runs)
2. **Daily token budget** — `tokens_used + est_output <= max_daily_tokens`
   (tokens counted from real API `usage`)
3. **Daily item budget** — `items < max_daily_items`

Budgets are **per-provider** (aggregate across its models), so Groq's two
models share one budget instead of doubling it.

## Persisted rate-limit state

`data/state/providers.json` per provider:

```json
{
  "groq": {
    "quota_date": "2026-08-14",
    "tokens_used": 2960,
    "items": 4,
    "calls": 6,
    "errors": 1,
    "cooldown_until": null,
    "models": { "openai/gpt-oss-120b": { "calls": 4, "items": 2, "errors": 0, "healthy": true } }
  }
}
```

- **Auto daily reset** — when `quota_date != today (UTC)` the counters and
  cooldown reset at run start (no manual reset commits).
- **Retry-After aware** — `429` responses' `Retry-After` / `x-ratelimit-reset`
  / "try again in Ns" are parsed; the persisted cooldown uses the server's
  suggested wait (capped, with exponential backoff).

## Next-run decision (`RunController.decide_next_run`)

After every collect run, the controller computes the next time collection is
possible. It ALWAYS returns a concrete UTC time:

| State | Next run |
|---|---|
| Some provider in cooldown | earliest cooldown expiry + jitter |
| Budget remains, no cooldown | + `heartbeat_minutes` re-check delay |
| Daily target met, or all collect providers exhausted | next day 01:00 UTC (fresh quotas/target) |

The result is written to `data/state/schedule.json`
(`collect_next_run`, `check_next_run`, `target_remaining`).

## Dynamic workflow cron

The scheduler (`src/run/scheduler.py`) runs when `scheduler.yml` fires, then:

1. Reads `schedule.json` — runs the due phase(s) under the pipeline lock.
2. After running, recomputes the next run and **rewrites the dynamic cron
   line** in `.github/workflows/scheduler.yml` to a date-specific
   `M H D M *` expression (e.g. `53 5 14 8 *`).
3. The workflow's commit step pushes the change, so GitHub's next schedule
   evaluation uses the new time.

`scheduler.yml` keeps a static `0 1 * * *` safety cron that is never
rewritten — it keeps the system alive if a dynamic cron ever goes stale
(e.g. a crashed mid-run).

## Pipeline lock + git safety

- `concurrency: group: scheduler` — one workflow job at a time.
- `data/state/meta.json` lock — the scheduler acquires it before running and
  releases it after (double safety against overlapping heartbeat runs).
- Every workflow commit uses `git pull --rebase origin main || true` before
  `git push` — no push races between jobs.

## Config knobs (`config.yml`)

```yaml
targets:
  total_per_day: 10                 # daily content goal
  collections: { world-news: 3, tech-news: 3, politics: 2, products: 2 }
run:
  max_job_minutes: 25               # stop before the 30-min Actions timeout
  heartbeat_minutes: 15             # re-check delay when budget remains
  cooldown_base_seconds: 30         # exponential backoff base
providers:
  groq:       { budget: { max_daily_items: 4, max_daily_tokens: 3000, max_output_tokens: 1500 } }
  openrouter: { budget: { max_daily_items: 6, max_daily_tokens: 4000, max_output_tokens: 2048 } }
  gemini:     { budget: { max_daily_items: 10, max_daily_tokens: 5000 } }
```

## Failure handling

- **429/5xx (transient)** — whole provider enters a persisted cooldown; the
  next pick rotates to a different provider. Repeated 429s mark the model down
  for the run.
- **Hard 4xx** (decommissioned model, bad config) — model marked down
  immediately, rotates.
- **All providers exhausted** — the collect phase stops, logs
  `target N/10 — next run ≈ ...`, writes the schedule, exits 0 (Actions does
  not report a false failure). The scheduler resumes when the cooldown lapses.
- **Unexpected exceptions** — caught defensively; never crash the pipeline.
