# information-hub-github-action

A layer-based deep-dive intelligence system that collects data daily via
**GitHub Actions + free AI providers** and stores it three ways: **raw data
frame** → **human preview (.md)** → **machine dataset (.json)**. Every item
is classified across **region / content-type / topic / category** layers — to
find "Myanmar-related" content, open `views/by-region/myanmar.json`. The date
is an attribute, never a folder.

**Two-phase pipeline**: `collect` (Groq/OpenRouter free models write
deep-dives) → `check` (Gemini search-grounding verifies claims). Every item
carries a **full provenance trail** (which provider/model wrote it, who
checked and approved it).

- **Monorepo root**: `information-hub/` — room for future apps (e.g. `information-hub-app`)
- **100% free**: GitHub Actions free tier + Gemini/Groq/OpenRouter free tiers + public repo
- **Dual license**: CC BY-NC (share) + commercial license (rent/API/training)

---

## ⚠️ Setup — read carefully

### 1. Required API keys (all free)

| Provider | Role | Where to get a key |
|---|---|---|
| **Groq** | `collect` — writes deep-dives (Llama open models) | https://console.groq.com/keys |
| **OpenRouter** | `collect` — auto-discovers free models at runtime and writes | https://openrouter.ai/settings/keys |
| **Gemini** | `check` — verifies claims with search grounding | https://aistudio.google.com/apikey |

**Important notes:**
- **Missing keys are fine** — the pipeline runs fully automatically with
  whatever providers have keys configured (missing ones are auto-disabled and
  logged). But with no collect key no items are produced, and with no check
  key items stay `pending_review` instead of being verified.
- Gemini is **check-only** — used solely for search-grounding verification.
- **Gemini multi-key**: set `GEMINI_API_KEYS` as a comma-separated list
  (`key1,key2`) — rotation and budget tracking are handled automatically.

### 2. GitHub Actions (run on the repo) — adding Secrets

1. Repo → **Settings → Secrets and variables → Actions → New repository secret**
2. Add any of the following three (only the ones you have):

| Secret name | Value |
|---|---|
| `GROQ_API_KEY` | your groq key |
| `OPENROUTER_API_KEY` | your openrouter key |
| `GEMINI_API_KEYS` | your_gemini_key_1,your_gemini_key_2 (multi-key allowed) |

3. The **`scheduler`** workflow then runs the pipeline **dynamically** — it
   has no fixed run times. After each run the pipeline computes the next
   moment collection is possible (provider cooldowns, token budgets, daily
   target) and rewrites the scheduler's own cron to that time. A daily
   `01:00 UTC` safety cron keeps the system alive if a dynamic cron ever
   goes stale.
4. **Manual run**: **Actions tab → `scheduler` → Run workflow** (runs due
   phases now) — or `collect` / `check` workflows for a single phase.

> **Note**: if the Secrets are missing, the Actions run does **not** fail — it
> simply continues with the providers that have keys (key-less providers are
> auto-disabled by the pipeline).

### 3. Local development (on your machine)

```bash
# 1. Create .env (repo root) — copy from .env.example
cp .env.example .env
#    Put your keys in .env (or skip them — mock mode works without keys)

# 2. venv + dependencies
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt      # macOS/Linux

# 3. Try it — no API keys needed (offline mock)
.venv/Scripts/python -m src.main --mock --phase both --force

# 4. Run the test suite
.venv/Scripts/python -m pytest tests/ -q
```

**Local run commands:**

```bash
python -m src.main --mock --phase both              # offline demo (no keys)
python -m src.main --phase collect                  # real collect
python -m src.main --phase check                    # verify today's items
python -m src.main --phase both                     # collect + check
python -m src.main --collection tech-news --force # single collection, bypass due check
python -m src.main --date 2026-08-14                # run for a specific date
python -m src.seed                                  # generate the sample dataset
```

---

## Architecture

```
config.yml (taxonomy engine + targets + provider budgets)
policies.yml (what to prioritize/exclude)
        │
        ▼  scheduler.yml (dynamic cron — pipeline rewrites it)
── Dynamic run-control ────────────────────────────────
  scheduler reads state/schedule.json → runs due phases
  → RunController gates model calls (cooldown + token budget)
  → recomputes next run time → rewrites workflow cron
── Phase collect ──────────────────────────────────────
  sources (RSS/arXiv/HN/GitHub) → fulltext → dedup
  → Groq/OpenRouter free models (auto-discover + gate + rotate)
  → deep-dive generate → store + provenance
── Phase check ────────────────────────────────────────
  Gemini google_search (or lexical fallback) → claims verify
  → grounding_score → review status + approval trail → source reputation
        │
        ▼
data/
├─ collections/          ← PURE DATA (the collected product)
│   ├─ raws/             AI output data frame (UTC datetime) — audit + dedup reference
│   ├─ preview/          .md human view (Obsidian-ready: frontmatter + wikilinks + Related)
│   └─ data-set/         .json machine dataset (schema enforced — AI training/commercial)
├─ state/                ← SYSTEM STATE (auto-run brain)
│   ├─ items.json  sources.json  providers.json  meta.json
│   ├─ collections.json  keys.json  schedule.json  run-log.jsonl
├─ views/                ← DERIVED (always rebuildable from data-set/)
│   ├─ by-topic/ by-region/ by-content-type/ by-category/ by-date/ by-entity/
│   ├─ index.json  graph.json (GraphRAG-ready)  taxonomy.json
└─ logs/                 ← run-<date>-<phase>.log (human-readable)
```

## Dynamic run control (no fixed run times)

- **Pre-call gate**: every model call is checked against a persisted provider
  cooldown and daily token/item budget BEFORE any HTTP request — rate-limited
  providers are never hammered.
- **Token-aware budgets**: provider daily token ceilings counted from API
  `usage` (auto-reset each UTC day — no manual resets).
- **Self-scheduling**: after a run the pipeline computes the next time
  collection is possible (cooldown expiry + jitter, or next day 01:00 when the
  daily target is met / providers are exhausted) and rewrites the dynamic cron
  line in `scheduler.yml` — the workflow fires only when it can actually
  collect. A static daily-01:00 safety cron is never rewritten.
- **Config**: `targets.total_per_day` (10 — free-tier daily capacity:
  Groq 4 + OpenRouter 6) + per-provider
  `budget: {max_daily_items, max_daily_tokens, max_output_tokens}`.
- **Collections**: `world-news`, `tech-news`, `politics`,
  `products` — world/tech/politics/product news prioritized; per-collection
  daily targets live in `targets.collections`.

## Provenance trail (per item — "who did what")

```json
{
  "provenance": { "generated_by": {"provider": "openrouter", "model": "...", "prompt_version": "v3.1"} },
  "grounding":  { "checked_by": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
                  "grounding_score": 0.83, "sources_verified": [{"url": "...", "title": "..."}] },
  "review":     { "status": "verified|pending_review",
                  "approved_by": {"type": "ai", "provider": "gemini", "model": "..."} }
}
```
- `state/items.json` — per-item approval trail (provider/model/score/review/approved_by)
- `state/run-log.jsonl` + `logs/` — provider pick/call/rotate/grounding events
- Score `< 0.5` → `pending_review` (data is kept, not deleted) — a human can mark
  it `verified` by editing the registry
- **Source reputation**: `state/sources.json` — per-source `avg_grounding_score`
  + failures → tells you which sources to trust

## Self-managing providers (fully auto)

- **OpenRouter**: at run time, `GET /api/v1/models` → free models (`pricing==0`)
  are auto-discovered (no hardcoding — the list updates itself when the
  provider changes)
- **Health-check + rotation**: cheap ping → healthy/unhealthy → on 429/5xx move
  to the next model → next provider → graceful skip + log
- **Budget**: per provider+model `max_calls` / `max_items` per run
  (`state/providers.json`)
- Free models without JSON mode are handled with prompt-JSON + tolerant parsing

## Config — taxonomy engine

Everything is configurable in `config.yml` (editable on GitHub, no code changes):

```yaml
taxonomy:    # hierarchical layers
  regions:   { asia: [myanmar, thailand, china], americas: [us, canada], ... }
  topics:    { ai-ml: [llm, agents, vision], geopolitics: [conflict, trade], ... }
  categories:{ economy: [macro, trade, fintech], ... }
relations:   # cross-layer links — [{from: myanmar, to: geopolitics, type: relates}]
providers:   # groq / openrouter (collect) + gemini (check) + budgets
quality:     # reject_threshold: 0.5, max_ai_verify_per_run
collections: # priority / frequency (daily|every-2-days|weekly) / sources / limits
```

`policies.yml` — priority (weight) + exclude rules — the Gemini selector ranks
against these.

## Data format (deep-dive record)

Key = `YYYY-MM-DD-NNN` · Stable ID = `info:item:<topic>:<region>:<key>`
(foreign key). The `.md` preview is generated from the `.json` — for Obsidian,
just open `data/collections/preview` as a vault. For GraphRAG, ingest
`data/views/graph.json` plus the `data-set/**` records directly.

## Project layout (standard layered)

```
information-hub/                        ← monorepo root
└── information-hub-github-action/      ← this repo
    ├── config.yml  policies.yml  .env.example
    ├── src/                           # layered subpackages
    │   ├── main.py                    # CLI — phase orchestration
    │   ├── config.py                  # taxonomy engine loader
    │   ├── models/    schema + candidate
    │   ├── collect/   fetchers, fulltext, dedup, prompts, mock
    │   ├── llm/       providers (self-managing), clients, mock
    │   ├── quality/   grounding engine (check)
    │   ├── run/       RunController + scheduler (dynamic cron)
    │   ├── storage/   store, registry, indexer
    │   ├── render/    markdown views
    │   └── utils/     logging
    ├── tests/                         # mirrors src/ layout
    ├── data/
    └── .github/workflows/  scheduler.yml (dynamic cron) · collect/check (manual)
```

## Developer

- **Sat Paing Oo**
- Repo: https://github.com/SatPaingOo/information-hub-github-action

---

© 2026 information-hub — CC BY-NC 4.0 (see LICENSE) · Commercial use: see COMMERCIAL.md
