# information-hub-github-action

Layer-based deep-dive intelligence system — GitHub Actions + free AI providers နဲ့ နေ့စဉ်
data စုပြီး **raw data frame** → **human preview (.md)** → **machine dataset (.json)** အဖြစ်
သိမ်းတဲ့ knowledge system။ Item တိုင်းကို **region / content-type / topic / category** layers
နဲ့ ခွဲပြီး — "Myanmar နဲ့ဆိုင်တာ" ရှာရင် `index/by-region/myanmar.json` ချက်ချင်း။
Date က folder မဟုတ်ဘဲ attribute။

**Two-phase pipeline**: `collect` (Groq/OpenRouter free models နဲ့ deep-dive ရေး) →
`check` (Gemini search-grounding နဲ့ claims verify) — item တိုင်းက **full provenance trail**
(ဘယ် provider/model က ရေးတယ်၊ ဘယ်သူက check/approve လုပ်တယ်) ပါ။

- **Monorepo root**: `information-hub/` — နောက်ပိုင်း app support (information-hub-app စသဖြင့်) ထည့်ဖို့
- **100% free**: GitHub Actions free tier + Gemini/Groq/OpenRouter free tiers + public repo
- **Dual license**: CC BY-NC (share) + commercial license (rent/API/training)

---

## ⚠️ Setup — သေချာဖတ်ပါ (Important)

### 1. လိုအပ်တဲ့ API keys (အကုန် free)

| Provider | အခန်းကဏ္ဍ (Role) | Key ရတဲ့နေရာ |
|---|---|---|
| **Groq** | `collect` — deep-dive ရေးတယ် (Llama open models) | https://console.groq.com/keys |
| **OpenRouter** | `collect` — free models တွေကို runtime မှာ auto-discover ပြီး ရေးတယ် | https://openrouter.ai/settings/keys |
| **Gemini** | `check` — search grounding နဲ့ claims verify | https://aistudio.google.com/apikey |

**သတိပြုစရာ (အရေးကြီး):**
- **Key မထည့်ထားရင် ကိစ္စမရှိပါဘူး** — ရှိတဲ့ provider တွေနဲ့ပဲ **fully auto** run ပါတယ် (missing = auto-disabled, log မှာ မြင်ရမယ်)။ ဒါပေမယ့် collect key လုံးဝမရှိရင် items မထွက်ဘူး၊ check key မရှိရင် verify မလုပ်ဘဲ `pending_review` ဖြစ်နေမယ်။
- Gemini က **check-only** — search grounding အတွက်ပဲ သုံးတယ်။
- **Gemini multi-key**: `GEMINI_API_KEYS` မှာ comma-separated ထည့်လို့ရတယ် (`key1,key2`) — budget/rotation ကိုယ်တိုင် လုပ်ပေးတယ်။

### 2. GitHub Actions (repo ပေါ်မှာ run) — Secrets ထည့်နည်း

1. Repo → **Settings → Secrets and variables → Actions → New repository secret**
2. အောက်ပါ ၃ ခုကို ထည့် (ရှိတာတွေပဲ ထည့်ရင်ရတယ်):

| Secret name | Value |
|---|---|
| `GROQ_API_KEY` | your groq key |
| `OPENROUTER_API_KEY` | your openrouter key |
| `GEMINI_API_KEYS` | your_gemini_key_1,your_gemini_key_2 (multi-key ရ) |

3. Workflow `daily.yml` က နေ့စဉ် **အလိုအလျောက်** run ပါမယ်:
   - **`collect`** — cron `0 1 * * *` UTC (≈06:30 မြန်မာစံတော်ချိန်)
   - **`check`** — cron `0 13 * * *` UTC (≈18:30 မြန်မာစံ)
4. **Manual run** ချင်ရင် → **Actions tab → daily-collect-check → Run workflow** → `phase` ရွေး (`both` / `collect` / `check`)

> **Note**: Secrets ထဲမှာ မထည့်ထားရင် Actions run က fail မဖြစ်ဘူး — ရှိတဲ့ providers နဲ့ပဲ ဆက်လည်ပါတယ်။ (pipeline မှာ key-less providers ကို auto-disable လုပ်လို့)

### 3. Local dev (ကိုယ့်စက်ပေါ်မှာ)

```bash
# 1. .env ဖန်တီး (repo root) — .env.example ကနေ copy
cp .env.example .env
#    .env ထဲမှာ ကိုယ့် keys ထည့်ပါ (မထည့်လဲ mock mode နဲ့ စမ်းလို့ရ)

# 2. venv + dependencies
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt      # macOS/Linux

# 3. စမ်းကြည့်တာ — API key မလို (offline mock)
.venv/Scripts/python -m src.main --mock --phase both --force

# 4. အကုန်စမ်းပြီးရင် — test
.venv/Scripts/python -m pytest tests/ -q
```

**Local run commands:**

```bash
python -m src.main --mock --phase both              # offline demo (keys မလို)
python -m src.main --phase collect                  # real collect
python -m src.main --phase check                    # verify today's items
python -m src.main --phase both                     # collect + check
python -m src.main --collection ai-research --force # collection တစ်ခုတည်း + due skip ကျော်
python -m src.main --date 2026-08-14                # သီးသန့်နေ့အတွက် run
python -m src.seed                                  # sample dataset generate
```

---

## Architecture

```
config.yml (taxonomy engine) + policies.yml (ဘာကို ပိုစုမယ်/ဖယ်မယ်)
        │
        ▼  GitHub Actions cron
── Phase collect (01:00 UTC) ──────────────────────────────
  sources (RSS/arXiv/HN/GitHub) → fulltext → dedup
  → Groq/OpenRouter free models (auto-discover + health-check + rotate)
  → deep-dive generate → store + provenance
── Phase check (13:00 UTC) ───────────────────────────────
  Gemini google_search → claims verify → grounding_score
  → review status + approval trail → source reputation
        │
        ▼
data/collections/
├─ raws/           AI output data frame (UTC datetime) — audit + dedup reference
├─ preview/        .md human view (Obsidian-ready: frontmatter + wikilinks + Related)
├─ data-set/       .json machine dataset (schema enforced — AI training/commercial)
├─ index/          generated layer views: by-topic/region/content-type/category/date/entity
│                  + index.json + graph.json (GraphRAG-ready) + taxonomy.json
├─ registry/       key-value tracking: sources / items / meta / providers / collections
│                  + run-log.jsonl (full provenance events)
└─ logs/           run-<date>-<phase>.log (human-readable)
```

## Provenance trail (item တိုင်း — "ဘာက ဘာလုပ်တယ်")

```json
{
  "provenance": { "generated_by": {"provider": "openrouter", "model": "...", "prompt_version": "v3.1"} },
  "grounding":  { "checked_by": {"provider": "gemini", "model": "gemini-2.5-flash"},
                  "grounding_score": 0.83, "sources_verified": [{"url": "...", "title": "..."}] },
  "review":     { "status": "verified|pending_review",
                  "approved_by": {"type": "ai", "provider": "gemini", "model": "..."} }
}
```
- `registry/items.json` — per-item approval trail (provider/model/score/review/approved_by)
- `registry/run-log.jsonl` + `logs/` — provider pick/call/rotate/grounding events အကုန်
- Score `< 0.5` → `pending_review` (data မဖျက်) — လူက registry ပြင်ရုံနဲ့ verified လုပ်လို့ရ
- **Source reputation**: `registry/sources.json` — per-source `avg_grounding_score` + failures → ဘယ် source ယုံလို့ရလဲ

## Self-managing providers (fully auto)

- **OpenRouter**: run ချိန်မှာ `GET /api/v1/models` → `pricing==0` free models auto-discover (hardcode မရှိ — provider ပြောင်းရင် ကိုယ်တိုင် update)
- **Health-check + rotation**: cheap ping → healthy/unhealthy → 429/5xx → next model → next provider → graceful skip + log
- **Budget**: per provider+model `max_calls` / `max_items` per run (`registry/providers.json`)
- JSON-mode မရတဲ့ free model ရှိရင် prompt-JSON + tolerant parser နဲ့ handle

## Config — taxonomy engine

`config.yml` မှာ အားလုံး ပြင်လို့ရ (GitHub UI မှာ code မထိ):

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

`policies.yml` — priority (weight) + exclude rules — Gemini selector က လိုက် rank တယ်။

## Data format (deep-dive record)

Key = `YYYY-MM-DD-NNN` · Stable ID = `info:item:<topic>:<region>:<key>` (foreign key)
`.md` preview က `.json` ကနေ generated — Obsidian: `data/collections/preview` ကို vault အဖြစ် ဖွင့်ရုံ။
GraphRAG ingestion = `data/collections/index/graph.json` + `data-set/**` records တိုက်ရိုက် ထည့်စားလို့ရ။

## Project layout (standard layered)

```
information-hub/                        ← monorepo root
└── information-hub-github-action/      ← ဒီ repo
    ├── config.yml  policies.yml  .env.example
    ├── src/                           # layered subpackages
    │   ├── main.py                    # CLI — phase orchestration
    │   ├── config.py                  # taxonomy engine loader
    │   ├── models/    schema + candidate
    │   ├── collect/   fetchers, fulltext, dedup, prompts, mock
    │   ├── llm/       providers (self-managing), clients, mock
    │   ├── quality/   grounding engine (check)
    │   ├── storage/   store, registry, indexer
    │   ├── render/    markdown views
    │   └── utils/     logging
    ├── tests/                         # mirrors src/ layout
    ├── data/
    └── .github/workflows/daily.yml
```

## Developer

- **Sat Paing Oo**
- Repo: https://github.com/SatPaingOo/information-hub-github-action

---

© 2026 information-hub — CC BY-NC 4.0 (see LICENSE) · Commercial use: see COMMERCIAL.md
