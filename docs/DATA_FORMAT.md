# Data Format

## Storage layout

```
data/
├─ collections/          ← PURE DATA (the collected product)
│   ├─ raws/             AI output data frame per run (UTC timestamp filename)
│   │                    — audit trail + dedup reference + source fulltext
│   ├─ preview/          human .md per record — FLAT, filename = <key>-<title-slug>.md
│   │                    Obsidian-ready: frontmatter + wikilinks + Related section
│   └─ data-set/         machine .json per record — FLAT, filename = <key>-<title-slug>.json
├─ state/                ← SYSTEM STATE (the auto-run brain)
│   ├─ items.json        per-item approval trail (status, provider, score, review)
│   ├─ sources.json      per-source reputation (avg grounding score, failures)
│   ├─ providers.json    per-provider daily quotas, cooldowns, model health
│   ├─ meta.json         last run, quota used, pipeline lock
│   ├─ collections.json  per-collection due tracking
│   ├─ keys.json         legacy multi-key budget (back-compat)
│   ├─ schedule.json     next-run times + target_remaining (run-control)
│   └─ run-log.jsonl     machine event log (full provenance trail)
├─ views/                ← DERIVED (rebuildable from data-set/)
│   ├─ by-topic/ by-region/ by-content-type/ by-category/ by-date/ by-entity/
│   ├─ index.json        master flat view
│   ├─ graph.json        nodes + edges (GraphRAG-ready)
│   └─ taxonomy.json     hierarchy view (parents / children / item counts)
└─ logs/                 ← run-<date>-<phase>.log (human-readable)
```

## Filename rule (readable + unique)

`<date>-<NNN>-<title-slug>` — e.g. `2026-08-13-004-one-rohingya-from-every-household.json`

- **key** = `YYYY-MM-DD-NNN` — globally unique (foreign key for links/dedup)
- **slug** = the AI-generated content title, lowercased, ~60 chars
- The key + stable ID also live inside the record (filename is a readable alias)

## Deep-dive record schema (`data-set/<key>.json`)

Every stored record must pass the enforced JSON Schema in
`src/models/schema.py` plus a minimum body word count:

```json
{
  "id": "info:item:<topic>:<region>:<date>-<NNN>",
  "key": "2026-08-13-004",
  "date": "2026-08-13",
  "content_type": "digest",
  "topic": "geopolitics",
  "region": "myanmar",
  "categories": ["policy", "industry"],
  "source": {"name": "arXiv", "url": "https://...", "type": "arxiv"},
  "title": "...",
  "tldr": "2-3 sentence executive summary",
  "background": "context — prior events / related entities",
  "analysis": [{"heading": "...", "content": "..."}],
  "key_facts": ["..."],
  "implications": ["..."],
  "outlook": "...",
  "entities": [{"type": "concept|company|model|person", "name": "RAG", "relation": "uses"}],
  "tags": ["..."],
  "related_items": ["info:item:..."],
  "related_taxonomy": [{"node": "...", "relation": "relates"}],
  "provenance": {
    "generated_by": {"provider": "...", "model": "...", "prompt_version": "..."},
    "schema_version": "v3"
  },
  "word_count": 742
}
```

Validation: `jsonschema` + `word_count >= content.min_words` (default 500).
Failed validation → model retry (max 2) → dropped + logged.

## Provenance trail (per item — "who did what")

After the check phase, records gain `grounding` and `review` blocks:

```json
{
  "grounding": {
    "checked_by": {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                   "search_tool": "google_search"},
    "checked_at": "2026-08-14T13:00:00Z",
    "grounding_score": 0.83,
    "claims_total": 6, "claims_grounded": 5,
    "sources_verified": [{"url": "...", "title": "..."}],
    "method": "gemini-search"
  },
  "review": {
    "status": "verified | pending_review",
    "approved_by": {"type": "ai", "provider": "gemini", "model": "..."},
    "approved_at": "2026-08-14T13:01:00Z"
  }
}
```

- `grounding_score < quality.reject_threshold` (0.5) → `pending_review`
  (data is kept; a human can flip it to `verified` by editing the registry)
- `method: "lexical"` means the Gemini search quota was unavailable and the
  local token-coverage fallback was used

## Classification (data, not paths)

`content_type` / `topic` / `region` / `categories` are record metadata —
there are no topic/region folders. Lookup happens through the derived views:
`views/by-region/myanmar.json` → item IDs → `data-set/<key>-<slug>.json`.

## GraphRAG / Obsidian

- **GraphRAG**: ingest `data/views/graph.json` (nodes + edges:
  item→entity, item→related, entity co-occurrence, taxonomy hierarchy) plus
  the `data/collections/data-set/**` records.
- **Obsidian**: open `data/collections/preview` as a vault — frontmatter,
  wikilinks (`[[Entity]]`), daily hubs, entity notes and taxonomy notes.
