# CLAUDE.md — information-hub

Guidance for AI agents working in this repo. Read this before making changes.

## What this is

A **layer-based deep-dive intelligence system**. It collects data daily via
**GitHub Actions + free AI providers** and stores every item three ways:
**raw data frame → human preview (`.md`) → machine dataset (`.json`)**. Each
item is classified across **region / content-type / topic / category** layers;
the **date is an attribute, never a folder**. See `README.md` and `docs/`.

- Live site (GitHub Pages, static, self-updating): https://satpaingoo.github.io/information-hub/web/
- Dual license: CC BY-NC (share) + commercial. Public repo. **100% free tiers.**

## Two-phase pipeline

```
collect  → Groq / OpenRouter free models WRITE deep-dives
check    → Gemini search-grounding VERIFIES the claims
```

Every record carries a **full provenance trail** (which provider/model wrote
it, who checked and approved it). Preserve provenance on every write — it is
the integrity guarantee of the dataset.

## Golden rules (do not break these)

1. **Keep the provenance trail intact.** Never write or mutate a record without
   recording who produced/checked it. Never mark an item verified unless the
   `check` phase actually verified it.
2. **`check` = Gemini only.** Gemini is used *solely* for search-grounding
   verification in the check phase. Do not use it to write content, and do not
   let a collect provider mark its own output approved.
3. **Free-tier only.** Groq / OpenRouter / Gemini free tiers. Do not add paid
   calls or raise limits without asking the user.
4. **Missing keys must never crash the run.** Key-less providers are
   auto-disabled and logged; the pipeline still completes. Preserve this.
5. **Date is an attribute, not a folder.** Storage is layered by
   region/content-type/topic/category. Don't reintroduce date-based folders.
6. **Secrets never get committed** — keys come from env only (`.env` is gitignored).

## Commands

```bash
python -m src.main --mock --phase both        # offline, NO API keys — safe default for dev
python -m src.main --phase collect            # real collect (needs a collect key)
python -m src.main --phase check              # verify today's items (needs GEMINI_API_KEYS)
python -m src.main --collection <name> --force # re-run one collection
```

### Tests

```bash
python -m pytest tests/ -q     # CI runs this before every phase
```

- 14 test files under `tests/`. **Run tests before and after any change** — this
  is the feedback loop. CI (`check.yml`) runs them as a gate.
- No installed package — modules are invoked as `python -m src.main` from repo root.
- **When keys are unavailable, use `--mock`** — it exercises the full pipeline
  offline so you can verify logic without spending quota.

## Environment

- Python 3.12 (CI). Deps: `pip install -r requirements.txt`.
- Keys (all optional, all free) in `.env`:
  - `GROQ_API_KEY` — collect
  - `OPENROUTER_API_KEY` — collect (auto-discovers free models at runtime)
  - `GEMINI_API_KEYS` — check; **comma-separated** for multi-key rotation (`key1,key2`)

## Layout

```
src/collect/     source fetchers + LLM deep-dive writing
src/llm/         provider clients (self-managing: cooldowns, budgets)
src/quality/     grounding / verification
src/storage/     layered store, indexer, naming, registry
src/render/      markdown rendering
src/run/         dynamic self-scheduling engine (controller, scheduler)
src/models/      record schema + candidate models
config.yml       collections, layers, targets — tune here
policies.yml     run policies
data/            committed records (raw frame / preview .md / dataset .json / views)
web/             static viewer — vanilla HTML + Tailwind (CDN) + D3, NO build step
docs/            ARCHITECTURE / DATA_FORMAT / RUN_CONTROL / CONFIGURATION
```

## Scheduling (how it runs itself)

The **`scheduler`** workflow is **dynamic** — after each run the pipeline
computes the next possible collection moment (provider cooldowns, token
budgets, daily target) and **rewrites its own cron** to that time. A daily
`01:00 UTC` safety cron keeps it alive if a dynamic cron goes stale. Manual:
Actions tab → `scheduler` / `collect` / `check` → Run workflow.

> Real runs spend daily free-LLM quota. Confirm with the user before triggering
> a live `collect`/`check` run; use `--mock` for development.

## Repo note

This repo used to sit nested one level deep
(`information-hub/information-hub-github-action/`). It has been flattened — the
repo root is now this folder. There is a single `.git` here; there is no outer
wrapper repo.

## Git conventions

- Match the history: lowercase type prefix — `chore:`, `fix:`, `feat:`, `docs:`.
  Automated pipeline commits use `chore: scheduler run <timestamp>` and
  `chore: scheduler cron -> ...` — **leave those to the workflow**; don't
  hand-write or amend them.
- **Never add AI / Co-Authored-By trailers.**
- Commit or push only when the user asks. (Note: there are pre-existing
  uncommitted data changes in the working tree from prior scheduler runs —
  leave them unless the user says otherwise.)
