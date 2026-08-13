# information-hub-github-action

Layer-based deep-dive intelligence system — GitHub Actions + Gemini free API နဲ့ နေ့စဉ်
data စုပြီး **raw data frame** → **human preview (.md)** → **machine dataset (.json)** အဖြစ်
သိမ်းတဲ့ knowledge system။ Item တိုင်းကို **region / content-type / topic / category** layers
နဲ့ ခွဲပြီး — "Myanmar နဲ့ဆိုင်တာ" ရှာရင် `index/by-region/myanmar.json` ချက်ချင်း။
Date က folder မဟုတ်ဘဲ attribute။

- **Monorepo root**: `information-hub/` — နောက်ပိုင်း app support (information-hub-app စသဖြင့်) ထည့်ဖို့
- **100% free**: GitHub Actions free tier + Gemini free tier + public repo
- **Dual license**: CC BY-NC (share) + commercial license (rent/API/training)

## Architecture

```
config.yml (taxonomy engine) + policies.yml (ဘာကို ပိုစုမယ်/ဖယ်မယ်)
        │
        ▼  GitHub Actions cron (နေ့စဉ် 01:00 UTC)
Collect (RSS/arXiv/HN/GitHub) → Full-text extract → Dedup → Gemini select → Gemini deep-dive
        │
        ▼
data/collections/
├─ raws/           AI output data frame (UTC datetime) — audit + dedup reference
├─ preview/        .md human view (Obsidian-ready: frontmatter + wikilinks + Related)
├─ data-set/       .json machine dataset (schema enforced — AI training/commercial)
├─ index/          generated layer views: by-topic/region/content-type/category/date/entity
│                  + index.json + graph.json (GraphRAG-ready)
└─ registry/       key-value tracking: sources / items / meta status
```

## Quick start (local, API key မလို)

```bash
pip install -r requirements.txt
python -m src.main --mock            # mock data နဲ့ run — structure verify
python -m src.main --date 2026-08-14 # real sources + Gemini (အောက်က setup လုပ်ပြီးမှ)
```

## GitHub Actions setup

1. **Gemini API key** (free): https://aistudio.google.com/apikey
2. Repo **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `GEMINI_API_KEY`
   - Value: your key
3. Workflow `daily.yml` က နေ့စဉ် cron (`0 1 * * *` UTC ≈ 06:30 မြန်မာစံ) နဲ့ run
   — Manual run ချင်ရင် Actions tab → **Run workflow** (workflow_dispatch)

## Data format

**Deep-dive record** (`data-set/<layer>/<key>.json`) — enforced schema:

```json
{
  "id": "info:item:ai-ml:global:2026-08-14-001",
  "key": "2026-08-14-001",
  "date": "2026-08-14",
  "content_type": "briefing",
  "topic": "ai-ml",
  "region": "global",
  "categories": ["research"],
  "source": {"name": "arXiv", "url": "...", "type": "arxiv"},
  "title": "...",
  "tldr": "...",
  "background": "...",
  "analysis": [{"heading": "...", "content": "..."}],
  "key_facts": ["..."],
  "implications": ["..."],
  "outlook": "...",
  "entities": [{"type": "concept", "name": "RAG", "relation": "uses"}],
  "tags": ["..."],
  "related_items": ["info:item:ai-ml:global:2026-08-13-001"],
  "word_count": 742
}
```

- Key = `YYYY-MM-DD-NNN` · Stable ID = `info:item:<topic>:<region>:<key>` (foreign key)
- `.md` preview က `.json` ကနေ generated — frontmatter + wikilinks + Related section
- Obsidian: `data/collections/preview` ကို vault အဖြစ် ဖွင့်ရုံ

## Config — taxonomy engine

`config.yml` မှာ taxonomy (content_types/topics/regions/categories) + collections
(sources, limits, primary_layer) အားလုံး — GitHub UI မှာ code မထိဘဲ ပြင်လို့ရတယ်။
`policies.yml` မှာ priority (weight) + exclude rules — Gemini selector က လိုက် rank တယ်။

## Dedup control

1. Local exact — source URL hash + normalized title hash → registry မှာ ရှိရင် skip
2. Local similarity — token-overlap % + entity overlap vs last 30 items → duplicate flag
3. Gemini selector prompt မှာ duplicate flags → exclude → rank

## Project layout

```
information-hub/                        ← monorepo root
└── information-hub-github-action/      ← ဒီ repo
    ├── config.yml  policies.yml
    ├── src/  (config, sources, fulltext, gemini, schema, dedup, store, registry, indexer, views, main)
    ├── tests/
    ├── data/
    └── .github/workflows/daily.yml
```

## Developer

- **Sat Paing Oo**
- Repo: https://github.com/<your-user>/information-hub-github-action

---

© 2026 information-hub — CC BY-NC 4.0 (see LICENSE) · Commercial use: see COMMERCIAL.md
