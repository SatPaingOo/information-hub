"""information-hub — layer-based deep-dive intelligence pipeline.

Package layout (standard category-based structure):

  main.py        CLI entry — orchestrates the two phases
  config.py      configuration loader (taxonomy engine + providers)

  models/        shared data contracts (schema validation, Candidate)
  collect/       phase collect — fetchers, fulltext, dedup, prompts, mock
  llm/           AI provider layer — self-managing ProviderManager + clients
  quality/       phase check — Gemini search-grounding verification
  storage/       persistence — 3-way store, key-value registry, indexer
  render/        output renderers — Obsidian markdown views
  utils/         shared helpers — structured logging

Pipeline: ``main.run`` executes ``collect`` (generate deep-dives via
Groq/OpenRouter free models) then ``check`` (verify claims via Gemini
search grounding).  Every item carries a full provenance trail.

Run:  python -m src.main --phase both
"""
