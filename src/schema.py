"""information-hub — deep-dive content schema and validation.

Every stored record must conform to this enforced JSON Schema
(jsonschema) plus word-count and required-field checks.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft7Validator

DEEP_DIVE_SCHEMA = {
    "$schema": "https://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "id", "key", "date", "content_type", "topic", "region",
        "categories", "source", "title", "tldr", "background",
        "analysis", "key_facts", "implications", "outlook",
        "entities", "tags", "related_items", "word_count",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^info:item:[\w-]+:[\w-]+:\d{4}-\d{2}-\d{2}-\d+$"},
        "key": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}-\d+$"},
        "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "content_type": {"type": "string"},
        "topic": {"type": "string"},
        "region": {"type": "string"},
        "categories": {"type": "array", "items": {"type": "string"}},
        "source": {
            "type": "object",
            "required": ["name", "url", "type"],
            "properties": {
                "name": {"type": "string"},
                "url": {"type": "string"},
                "type": {"type": "string"},
            },
        },
        "title": {"type": "string", "minLength": 5},
        "tldr": {"type": "string", "minLength": 10},
        "background": {"type": "string", "minLength": 20},
        "analysis": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["heading", "content"],
                "properties": {
                    "heading": {"type": "string", "minLength": 2},
                    "content": {"type": "string", "minLength": 20},
                },
            },
        },
        "key_facts": {"type": "array", "minItems": 2, "items": {"type": "string"}},
        "implications": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "outlook": {"type": "string", "minLength": 20},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "name", "relation"],
                "properties": {
                    "type": {"type": "string", "enum": ["concept", "company", "model", "person"]},
                    "name": {"type": "string", "minLength": 1},
                    "relation": {"type": "string"},
                },
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "related_items": {"type": "array", "items": {"type": "string"}},
        "related_taxonomy": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["node", "relation"],
                "properties": {
                    "node": {"type": "string"},
                    "relation": {"type": "string"},
                },
            },
        },
        "provenance": {
            "type": "object",
            "properties": {
                "generated_by": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "model": {"type": "string"},
                        "prompt_version": {"type": "string"},
                        "supports_json": {"type": "boolean"},
                    },
                },
                "schema_version": {"type": "string"},
            },
        },
        "grounding": {
            "type": "object",
            "properties": {
                "checked_by": {"type": "object"},
                "checked_at": {"type": "string"},
                "grounding_score": {"type": ["number", "null"]},
                "claims_total": {"type": "integer"},
                "claims_grounded": {"type": "integer"},
                "sources_verified": {"type": "array", "items": {"type": "object"}},
                "method": {"type": "string"},
            },
        },
        "review": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["verified", "pending_review"]},
                "approved_by": {"type": "object"},
                "approved_at": {"type": "string"},
            },
        },
        "word_count": {"type": "integer", "minimum": 1},
    },
}

_VALIDATOR = Draft7Validator(DEEP_DIVE_SCHEMA)


def validate_record(record: dict[str, Any], min_words: int = 500) -> list[str]:
    """Return a list of validation errors (empty == valid)."""
    errors: list[str] = []
    for err in sorted(_VALIDATOR.iter_errors(record), key=lambda e: list(e.path)):
        errors.append(f"{'/'.join(str(p) for p in err.path)}: {err.message}")
    body_words = word_count(record)
    if body_words < min_words:
        errors.append(f"body word count {body_words} < minimum {min_words}")
    return errors


def word_count(record: dict[str, Any]) -> int:
    """Approximate word count of the free-text body (excludes tldr/index fields)."""
    parts: list[str] = [record.get("background", "")]
    parts += [a.get("content", "") for a in record.get("analysis", [])]
    parts += [a.get("heading", "") for a in record.get("analysis", [])]
    parts += list(record.get("key_facts", []))
    parts += list(record.get("implications", []))
    parts.append(record.get("outlook", ""))
    text = " ".join(parts)
    return len(re.findall(r"\b[\w'-]+\b", text))
