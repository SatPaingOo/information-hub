# Architecture

information-hub is a **layer-based deep-dive intelligence system**: it collects
stories from free public sources every day, generates structured deep-dive
records with free AI providers, verifies their claims, and stores everything
as a versioned dataset in git.

This document describes the system architecture at a glance. Companion docs:

- [DATA_FORMAT.md](./DATA_FORMAT.md) — record schema, storage layout, provenance
- [RUN_CONTROL.md](./RUN_CONTROL.md) — the dynamic run-control engine
- [CONFIGURATION.md](./CONFIGURATION.md) — config.yml reference

---

## Big picture

```
config.yml (taxonomy engine + targets + provider budgets)
policies.yml (what to prioritize / exclude)
        │
        ▼
scheduler.yml (dynamic cron — the pipeline rewrites it after each run)
        │
        ▼  src.run.scheduler: reads data/state/schedule.json → runs due phases
── Phase collect ──────────────────────────────────────────────
  sources (RSS / arXiv / HackerNews / GitHub) → fulltext → dedup
  → Groq / OpenRouter free models (auto-discover + rate-limit gate + rotate)
  → deep-dive generate (enforced JSON schema) → store + provenance
── Phase check ────────────────────────────────────────────────
  Gemini google_search (or lexical fallback) → claim verification
  → grounding_score → review status + approval trail → source reputation
        │
        ▼
data/
├─ collections/   ← the collected product (raws + previews + data-set)
├─ state/         ← system state (quotas, cooldowns, schedule — auto-run brain)
├─ views/         ← derived lookup views (rebuildable)
└─ logs/          ← run logs
```

## Two phases

### Phase `collect` — generate

1. **Fetch** — for each enabled collection, its configured sources
   (`RSS`/`arXiv`/`HackerNews`/`GitHub`) return candidate stories (capped).
2. **Full-text** — the top candidates' article pages are fetched and cleaned
   (`trafilatura`, with a raw-strip fallback), capped at
   `content.fulltext_max_chars`.
3. **Dedup** — exact URL/title hashes + token-overlap similarity vs recent
   records mark duplicates; the selector prompt carries these flags.
4. **Select** — the collect provider picks which stories to publish today
   (priority policies + per-collection quota + global target).
5. **Deep-dive** — each selected story is expanded into a 600–1000 word
   structured record that must pass the enforced JSON schema
   (`src/models/schema.py`). Invalid output is retried then dropped.
6. **Store** — the record is written flat to `data/collections/data-set/`
   (`<key>-<title-slug>.json`) plus an Obsidian-ready `.md` in
   `data/collections/preview/`; a raw AI frame goes to `raws/`.

### Phase `check` — verify

For each record dated today, the check provider (Gemini with `google_search`
grounding) verifies the record's claims and returns:

- `grounding_score` (grounded claims / total)
- `sources_verified` (cited URLs + titles)
- `review_status` (`verified` when score ≥ threshold, else `pending_review`)
- an **approval trail** (who checked/approved, when)

When the Gemini search quota is unavailable, a **lexical fallback** scores
claim token-overlap against the source fulltext — the pipeline always produces
a grounding result and never crashes on a quota error.

## Layered package layout (`src/`)

| Layer | Modules | Responsibility |
|---|---|---|
| `src/main.py` | — | CLI entry, phase orchestration |
| `src/config.py` | — | configuration loader (taxonomy engine) |
| `src/models/` | schema, candidate | shared data contracts + validation |
| `src/collect/` | fetchers, fulltext, dedup, prompts, mock | phase collect data gathering |
| `src/llm/` | providers, clients, mock | self-managing provider layer + HTTP clients |
| `src/quality/` | grounding | phase check verification |
| `src/run/` | controller, scheduler | dynamic run-control + scheduling |
| `src/storage/` | store, registry, indexer | persistence + state + derived views |
| `src/render/` | markdown | Obsidian markdown renderers |
| `src/utils/` | logging_util | structured logging + run log |

Dependencies flow downward: `main` → `run`/`quality`/`collect` → `llm` →
`storage` → `render`/`utils` → `models`/`config`.

## Source of truth

- **Collected data** (`data/collections/`) is committed to git — it is the
  product (share / rent / GraphRAG / training).
- **System state** (`data/state/`) is committed too — it lets the scheduler
  and run-control survive across runs (self-healing).
- **Derived views** (`data/views/`) are rebuildable at any time from
  `data-set/` via `Indexer.rebuild()`.
- **Logs** (`data/logs/`) are operational.

## GitHub Actions

| Workflow | Trigger | Purpose |
|---|---|---|
| `scheduler.yml` | dynamic cron (pipeline-rewritten) + daily-01:00 safety | runs due phases, then recomputes + rewrites the cron |
| `collect.yml` | manual dispatch | on-demand collect |
| `check.yml` | manual dispatch | on-demand check |

Every workflow runs `pytest` before the pipeline, then commits generated data
with `git pull --rebase` before pushing (git-conflict safe).
