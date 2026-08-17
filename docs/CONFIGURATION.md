# Configuration Reference

Everything is configured through `config.yml` (editable directly on GitHub —
no code changes required) and `policies.yml`.

## `config.yml`

### providers

Token-aware daily budgets per provider (auto-reset each UTC day):

```yaml
providers:
  groq:
    enabled: true
    keys_env: GROQ_API_KEY            # env name (repo Secret)
    role: collect                     # collect | check
    format: openai                    # openai | google
    models: [openai/gpt-oss-120b, openai/gpt-oss-20b]   # current free-tier (llama retired)
    budget: { max_daily_items: 4, max_daily_tokens: 3000, max_output_tokens: 1500 }
  openrouter:
    enabled: true
    keys_env: OPENROUTER_API_KEY
    role: collect
    format: openai
    discover: free_models             # runtime auto-discover free models (no hardcode)
    budget: { max_daily_items: 6, max_daily_tokens: 4000, max_output_tokens: 2048 }
  gemini:
    enabled: true
    keys_env: GEMINI_API_KEYS         # comma-separated multi-key
    role: check                       # search-grounding verification ONLY
    format: google
    models: [gemini-3.5-flash-lite, gemini-3.5-flash]
    search_tool: google_search        # enable web-search grounding
    budget: { max_daily_items: 10, max_daily_tokens: 5000, max_output_tokens: 2048 }
```

- `role: collect` → deep-dive generation · `role: check` → verification
- Missing keys = provider auto-disabled (pipeline runs with what exists)
- `max_output_tokens` — per-call output cap (free tiers cap output too)

### targets

```yaml
targets:
  total_per_day: 10                   # daily content goal
  collections: { world-news: 3, tech-news: 3, politics: 2, products: 2 }
```

Per-collection values must sum to `total_per_day`.

### run (dynamic scheduler)

```yaml
run:
  phases: [collect, check]
  max_job_minutes: 25                 # stop before the 30-min Actions timeout
  heartbeat_minutes: 15               # re-check delay when budget remains
  cooldown_base_seconds: 30           # exponential backoff base for cooldowns
```

### quality

```yaml
quality:
  reject_threshold: 0.5               # grounding score below → pending_review
  max_ai_verify_per_run: 10           # Gemini free-tier search budget cap
```

### taxonomy (classification layers — hierarchical)

```yaml
taxonomy:
  regions:
    asia:     [myanmar, thailand, china]
    americas: [us, canada]
    europe:   [uk, germany]
  topics:
    ai-ml:    [llm, agents, vision]
    world:    []
    politics: []
    products: []
  categories:
    economy:  [macro, trade, fintech]
    society:  [human, education, health]
    industry: [product, open-source]
  content_types: [article, briefing, analysis, digest]
```

A flat list (e.g. `topics: [ai-ml]`) is also accepted — treated as leaf-only.

### relations (static cross-layer links)

```yaml
relations:
  - { from: llm,  to: agents, type: related }
  - { from: us,   to: dev-oss, type: relates }
```

These appear as edges in `data/views/graph.json`.

### collections

```yaml
collections:
  world-news:
    enabled: true
    priority: 3                       # higher = more quota, runs first
    frequency: daily                  # daily | every-2-days | weekly
    content_type: digest
    topics: [world, geopolitics]
    regions: [global]
    categories: [policy, industry]
    sources:
      - { type: rss, url: "https://feeds.bbci.co.uk/news/world/rss.xml" }
      - { type: rss, url: "https://www.aljazeera.com/xml/rss/all.xml" }
    limits: { max_candidates: 12, max_daily_items: 3 }
```

Source types: `rss` (RSS/Atom), `arxiv` (arXiv API), `hackernews`
(Hacker News API), `github` (GitHub search).

### content (deep-dive requirements)

```yaml
content:
  min_words: 500
  target_words: [600, 1000]
  fulltext_max_chars: 6000
  similarity_window: 30
  similarity_threshold: 0.55
```

## `policies.yml`

Controls what the selector prefers / excludes:

```yaml
priority:                             # higher weight = more likely selected
  - { type: topic,   value: "agentic AI", weight: 3 }
  - { type: region,  value: "myanmar",    weight: 2 }
  - { type: keyword, value: "regulation", weight: 1 }
exclude:                              # hard filter — drop if matched
  - { type: keyword, value: "press-release" }
  - { type: source,  value: "example.com" }
```

## Environment / Secrets

| Env var | Provider | Purpose |
|---|---|---|
| `GROQ_API_KEY` | Groq | collect |
| `OPENROUTER_API_KEY` | OpenRouter | collect (free-model discovery) |
| `GEMINI_API_KEYS` | Gemini | check (comma-separated multi-key) |

For GitHub Actions add them as **repo Secrets**; for local dev put them in
`.env` (copy from `.env.example`).
