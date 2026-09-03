# INFORMATION HUB — လုပ်ငန်း မှတ်တမ်းနှင့် လည်ပတ်ရေး လမ်းညွှန်

> **ဘယ်သူ့အတွက်**: project ပိုင်ရှင် (နောက် session / အချိန်ကြာလျှင် ပြန်လာချိန် အခြေအနေ အလုံးစုံ ပြန်သိနိုင်ရန်)
> **နောက်ဆုံး မွမ်းမံ**: 2026-09-03 (UTC)

---

## 1. Project အနှစ်ချုပ်

**Information Hub** — GitHub Actions ဖြင့် အလုပ်လုပ်သော **အခမဲ့ AI provider များဖြင့် အလိုအလျောက် နေ့စဉ် intelligence collection** စနစ်။ မည်သည့် server မှ မလိုအပ်ပါ၊ မည်သည့် ကုန်ကျစရိုက်မှ မရှိပါ။

| အရာ | URL / တည်နေရာ |
|---|---|
| Live site (portfolio) | https://satpaingoo.github.io/information-hub/ |
| Repo | https://github.com/SatPaingOo/information-hub |
| RSS feed | https://satpaingoo.github.io/information-hub/data/views/feed.xml |
| Machine dataset | `data/collections/data-set/*.json` (repo အတွင်း) |
| Local | `D:\Projects\@Me\information-hub\information-hub-github-action` |
| License | Data = CC BY-NC 4.0 · Commercial = သီးခြား license (`COMMERCIAL.md`) |

## 2. Architecture အနှစ်ချုပ်

```
GitHub Actions (scheduler.yml)
 ├─ DYNAMIC cron   → scheduler က နောက် run အတိအကျ ပြန်ရေးတာ (BOT_PAT လို)
 ├─ HEARTBEAT 4-59/15 → ၁၅ မိနစ်တစ်ကြိမ် schedule.json စစ် (ပင်မ driver)
 └─ SAFETY daily 01:00  → နောက်ဆုံး backstop
        │
        ▼
src/run/scheduler.py → src/run/controller.py → src/main.py (collect / check phase)
        │                                    │
        ▼                                    ▼
src/llm/providers.py (ProviderManager)   src/collect/fetchers.py (RSS sources)
  Groq / OpenRouter / Gemini              src/quality/grounding.py (verify)
  auto-discover free models                 
  token budget · cooldown · quarantine     
        │
        ▼
src/storage/ → data/collections/data-set/*.json (records)
             → data/views/ (index.json, stats.json, run-stats.json, graph.json, feed.xml)
             → data/collections/preview/ (Obsidian notes: entities/taxonomy/daily)
             → data/state/ (schedule.json, providers.json, meta.json, run-log.jsonl)
        │
        ▼
GitHub Pages (web/) — index/library/article/graph/report/dataset
```

**ကိုယ်စားလှယ်များ (Secrets)**: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEYS`, `BOT_PAT` (fine-grained token `info-hub-bot` — Contents+Workflows read/write, 2026-09-03 တွင် rotate လုပ်ပြီး)။

**နေ့စဉ်ပန်းတိင်**: record အသစ် 10 ခု (world-news / tech-news / politics / products)။ Free-tier ကန့်သတ်ချက်ကြောင့် ယောက်ျားလေး ၆-၉ ခုပဲ ရတတ်ပါသည်။

---

## 3. ဇာတ်လမ်း အဆင့်ဆင့် (Timeline)

### Phase 1 — Pipeline တည်ဆောက် (Aug 13–17)
- Initial pipeline: collect (RSS → AI deep-dive) + check (Gemini search grounding verify) အဆင့်နှစ်ခု
- Provider self-management မူလအစ: JSON mode, 429/5xx transient handling, model rotation
- Lexical grounding fallback — Gemini quota မရှိလည်း အမှတ်ပေးနိုင်ရန် (`f2a0529` ခေတ်)
- V5 flat storage: `<date>-<NNN>-<title-slug>.json` ပုံစံ

### Phase 2 — အလိုအလျောက်စနစ် (Aug 18–28)
- Daily rollover fix, scheduler spin fix, dynamic cron rewrite (BOT_PAT)
- Auto model discovery (`discover: free_models`) — Groq/OpenRouter ရဲ့ free models တွေကို အလိုအလျောက် ရှာပြီး ထည့်သွင်းခြင်း၊ အောင်မြင်မှုအလားအလာ (success-rate) အလိုက် အလိုအလျောက် ရွေးချယ်ခြင်း၊ quarantine
- Near-duplicate prune (token containment), weekly dataset tags (`data-YYYY-MM-DD`)

### Phase 3 — Portfolio Web Viewer (Sep 2)
- Repo rename → `information-hub`၊ GitHub Pages (root deploy)
- Dark theme site အပြည့်အစုံ: landing / library / article / graph (D3) / report (Chart.js) / dataset
- **Mobile nav saga** — ဖုန်းမှာ nav menu မထွက်တဲ့ ပြဿနာ ကြိမ်ဖန်များစွာ ဖြေရှင်းခဲ့ရ (အောက်ပါ Bug archive ကြည့်ပါ)
- Report page — date-range filter + run health (model calls/failures/tokens)
- RSS feed + dataset catalog + unified footer

### Phase 4 — ယုံကြည်စိတ်ချရမှု + Data အရည်အသွေး (Sep 3 — ဒီနေ့)
- **"ဒီနေ့ data မရ" ပြဿနာ**: GitHub က schedule event ပျက်သွားတာကို တွေ့ရှိခြင်း → heartbeat restore + DYNAMIC marker anchor
- **Windows case-collision fix**: taxonomy notes တွေကို wipe-and-rebuild လုပ်ခြင်း၊ stale uppercase files ၉ ခု ဖျက်ခြင်း
- **SEO pack**: sitemap.xml + robots.txt + OG/canonical tags (page အားလုံး)
- **URL backfill**: record အဟောင်း ၄၅ ခုမှာ တကယ့် article URL ပြန်ရ (Wayback archived feeds ၃၇ ခု + arXiv API ၈ ခု) — script: `scripts/backfill_source_urls.py`
- **Web corroboration**: Gemini ပျက်နေချိန် လုံးဝမျိုးစုံ သတင်းများ (independent outlets) ဖြင့် အတည်ပြုခြင်း (Google News RSS) → verify method = `web` 🌐
- **Full-text search**: Minisearch (ranked, prefix+fuzzy) on library
- **Dataset sample download**: "Free sample · 3 records" button (in-browser JSON)
- **Zero-day alert**: တစ်နေ့လုံး record ၀ ⇒ GitHub issue အလိုအလျောက် ဖန်တီးခြင်း
- **Heartbeat offset**: `*/15` → `4-59/15` (GitHub ရဲ့ quarter-hour congestion ရှောင်ရန်)
- **Token rotation**: အဟောင်း PAT ယိုစိမ့်မှုကြောင့် `info-hub-bot` fine-grained token အသစ်နဲ့ လဲလှယ်ခြင်း (data + workflow push နှစ်ခုလုံး စမ်းသပ်အတည်ပြုပြီး)

---

## 4. Bug Archive — အကြောင်းအရင်းခြေရင်း + ဖြေရှင်းချက် (နောက်ပြန်ဖတ်ရန်)

| # | လက္ခဏာ | အကြောင်းအရင်းခြေရင်း | ဖြေရှင်းချက် |
|---|---|---|---|
| 1 | တစ်နေ့သုံး 0 items (Aug 18) | 昨日 rollover မလုပ်ဘဲ quota အဟောင်းနဲ့ တွက်ခြင်း | `rollover_daily()` — run အစတိုင်း UTC ရက်စစ် |
| 2 | Scheduler 20× spin | budget ရှိပေမယ့် model အားလုံး quarantined | `_provider_collectable` = budget **AND** usable model |
| 3 | Concurrent run rebase conflict | manual + scheduled တိုက်ဆိုင်ခြင်း | graceful degrade (drop commit / reset --hard) |
| 4 | Workflow push မရ | actions/checkout ရဲ့ extraheader က BOT_PAT ကို ဖုံးခြင်း | push မလုပ်မှီ `git config --unset-all http.https://github.com/.extraheader` |
| 5 | Windows မှာ file ပြင်လို့မရ | entity name ထဲမှာ `"` ပါခြင်း၊ case-variant ထပ်နေခြင်း | `_win_safe()` + case-fold dedup |
| 6 | Obsidian wikilink ပျက် | link က raw name၊ file က slug | `record_filename`/`safe_name` ဖြင့် render |
| 7 | Graph "node not found" | ဖျက်ပြီး item ဆီ ချိတ်ထားသော edge များ | `add_edge` — endpoint နှစ်ခုစလုံး ရှိမှ ထည့်ရန် |
| 8 | index.json `file` field 404 | `data-set/` path မှား | `collections/data-set/` (`8275fed`) |
| 9 | Article source link မှား | article URL အစား RSS feed URL | fetcher က `source.url`=article၊ `source.feed`=feed |
| 10 | Report date filter မထိ | run-health module `range` null | load-time init (`af347b9`) |
| 11 | ဖုန်း nav menu မထွက် (ကြိမ်ပေါင်းများစွာ) | duplicate @media၊ toggle invisible၊ cache-bust stale၊ backdrop-filter က fixed ကို ပိတ်ခြင်း၊ **နောက်ဆုံး**: တစ်နေရာတည်း hamburger↔X swap (38px box) | နောက်ဆုံးပုံစံ — in-flow absolute dropdown + single-button swap (`5d33196`) |
| 12 | Landing content ပျောက်နေခြင်း | keyframe animation ကို webview က မဖြစ်စေခြင်း | transition-based reveal — default မြင်ရ (`99c9867`) |
| 13 | **ဒီနေ့ data မရ (Sep 3)** | GitHub က 01:00 UTC event လုံးဝ မပေါက်ခြင်း၊ heartbeat ကို dynamic rewrite က နေ့စဉ် ဖျက်ခြင်း | DYNAMIC marker anchor + heartbeat permanent line (`decfa45`) |
| 14 | Heartbeat တစ်နေ့ ၄ ချက်ပဲ ပေါက်ခြင်း | GitHub scheduler က :00/:15/:30/:45 မှာ ထိပ်တိုက်တွေ့ခြင်း (congestion) | `4-59/15` offset (`36b8fa3`) |
| 15 | Windows မှာ phantom .md changes | taxonomy notes မ wipe ⇒ case-variant stale files | wipe-and-rebuild + stale ၉ ခု ဖျက် (`c842d42`) |
| 16 | Gemini verify 0% (Aug 19 ကတည်းက) | Gemini search API က နေ့ထက်နေ့ 429 ပြန်ခြင်း | code ပြဿနာ **မဟုတ်ပါ** — web corroboration fallback (`b97b071`) |
| 17 | Record အဟောင်းများတွင် feed URL / empty source | fetcher fix က record အသစ်တွေမှာပဲ သက်ရောက်ခြင်း | Wayback archived feeds + arXiv API backfill (`ba52544`) |

---

## 5. လက်ရှိ အခြေအနေ Snapshot (2026-09-03)

- **Records**: 111 အစု · ရက်ပေါင်း 18 ရက် · ဒီနေ့ 7 ခု · article URL ရှိမှု 55/111
- **Tests**: 102 passed
- **Verify mix**: web/lexical (Gemini 429 ကြောင့် AI-search မပါ)၊ corroboration citations နှင့်အတူ
- **Live site**: sitemap/robots/OG/search/sample-download အားလုံး deployed
- **နောက် run**: Sep 4, 01:00 UTC (fresh quota, target 10)
- **Security**: အဟောင်း PAT ဖျက်ပြီး၊ `info-hub-bot` (fine-grained, repo တစ်ခုတည်း scope) အသုံးပြုနေပြီး

## 6. ကျန်ရှိနေဆဲ (Known limitations)

1. **BBC analysis 56 ခု** — title တွေ LLM-rewrite ဖြစ်နေလို့ article URL ကို စိတ်ချစွာ match မလုပ်နိုင် (wrong-link ရန်သူဖြစ်စေနိုင်လို့ မထိထားခြင်း)
2. **Product Hunt 13 ခု** — archived feed အလွတ်များ၊ သီးခြားနည်း လိုအပ်
3. **Gemini 429** — key/region ဘက်က ကိစ္စ၊ quota ပြန်ရရင် verify method = gemini ပြန်စမယ်
4. **Target 10/day** — free-tier budget ကြောင့် ၆-၉ ခုနဲ့ ရပ်တတ်ခြင်း (provider အသစ် ထည့်ရင် တိုးလာနိုင် — Cerebras, GitHub Models, Mistral free)

## 7. Ops Runbook — ဘယ်လို စစ်/လုပ်ရလဲ

**ကျန်းမာရေး အမြန်စစ်ကြည့်ခြင်း:**
```bash
gh run list -R SatPaingOo/information-hub --workflow=scheduler.yml --limit 5   # run များ
cat data/state/schedule.json        # နောက် run အချိန် + target_remaining
tail -5 data/state/run-log.jsonl    # နောက်ဆုံး events
python -m pytest tests/ -q          # tests
```

**ချက်ချင်း collect လုပ်ခြင်း:** `gh workflow run scheduler.yml -f force_phase=collect`
**Run log ကြည့်ခြင်း:** `gh run view <id> -R SatPaingOo/information-hub --log`
**Live deploy စစ်ခြင်း:** `gh api repos/SatPaingOo/information-hub/pages/builds/latest --jq '.status,.commit'`

**အရေးကြီး စည်းမျဉ်းများ:**
- `web/` ထဲက CSS/JS ပြောင်းရင် **cache-bust `?v=N` အားလုံး page မှာ တိုးရမယ်** (ဒါက အရင်ပြဿနာ အများကြီးရဲ့ အကြောင်းရင်းခံဖြစ်ခဲ့ဖူးတယ်)
- Token/API key တွေ **chat ထဲ မထည့်ရ** — GitHub secrets မှာသာ
- Record တွေမှာ `source.url` = article၊ `source.feed` = feed၊ `source.url_source` = backfill provenance
- `docs/` ထဲက တခြားစာရွက်များ — ARCHITECTURE / CONFIGURATION / DATA_FORMAT / RUN_CONTROL

## 8. Monetization အခြေအနေ

- **ပြီးပြီ**: dataset catalog page · free sample download (3 records, in-browser) · dual license (CC BY-NC + commercial `satpaingoo777@gmail.com`) · weekly tags (`data-YYYY-MM-DD`) = versioned snapshots
- **နောက်ထပ် လုပ်နိုင်**: ဈေးကွက် page / payment link (Gumroad/LemonSqueezy) · API endpoint · data dictionary ချဲ့ခြင်း · provider အသစ်များ ထည့်သွင်း၍ record count တိုးမြှင့်ခြင်း
