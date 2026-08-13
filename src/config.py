"""information-hub — configuration loader.

Loads config.yml (taxonomy engine) + policies.yml (collection rules)
and exposes them as typed plain dicts/dataclasses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class GeminiConfig:
    model: str = "gemini-2.5-flash"
    temperature: float = 0.4
    max_output_tokens: int = 2048
    retries: int = 2

    @property
    def api_key(self) -> str:
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            key = _read_dotenv_key()
        return key


def _read_dotenv_key() -> str:
    """Read GEMINI_API_KEY from a local .env file if present (dev only)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@dataclass
class StorageConfig:
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    max_daily_items_total: int = 3


@dataclass
class ContentConfig:
    min_words: int = 500
    target_words: tuple[int, int] = (600, 1000)
    fulltext_max_chars: int = 6000
    similarity_window: int = 30
    similarity_threshold: float = 0.55


@dataclass
class CollectionConfig:
    name: str
    enabled: bool = True
    content_type: str = "article"
    topics: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_layer: str = "topic"
    sources: list[dict[str, Any]] = field(default_factory=list)
    max_candidates: int = 12
    max_daily_items: int = 1


@dataclass
class Taxonomy:
    content_types: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass
class Config:
    gemini: GeminiConfig
    storage: StorageConfig
    content: ContentConfig
    taxonomy: Taxonomy
    collections: dict[str, CollectionConfig]
    policies: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, config_path: Path | str | None = None,
             policies_path: Path | str | None = None) -> "Config":
        cfg_path = Path(config_path) if config_path else ROOT / "config.yml"
        pol_path = Path(policies_path) if policies_path else ROOT / "policies.yml"
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        with open(pol_path, encoding="utf-8") as f:
            policies = yaml.safe_load(f) or {}

        gemini = GeminiConfig(**raw.get("gemini", {}))
        storage = StorageConfig(
            data_dir=Path(str(raw.get("storage", {}).get("data_dir", "data"))),
            max_daily_items_total=raw.get("storage", {}).get("max_daily_items_total", 3),
        )
        content = ContentConfig(**raw.get("content", {}))
        tax = raw.get("taxonomy", {})
        taxonomy = Taxonomy(
            content_types=tax.get("content_types", []),
            topics=tax.get("topics", []),
            regions=tax.get("regions", []),
            categories=tax.get("categories", []),
        )
        collections = {
            name: _collection_from(name, raw.get("collections", {}).get(name, {}))
            for name in raw.get("collections", {})
        }
        return cls(
            gemini=gemini,
            storage=storage,
            content=content,
            taxonomy=taxonomy,
            collections=collections,
            policies=policies,
            path=cfg_path,
        )

    def enabled_collections(self) -> list[CollectionConfig]:
        return [c for c in self.collections.values() if c.enabled]


def _collection_from(name: str, raw: dict[str, Any]) -> CollectionConfig:
    limits = raw.get("limits", {})
    return CollectionConfig(
        name=name,
        enabled=raw.get("enabled", True),
        content_type=raw.get("content_type", "article"),
        topics=raw.get("topics", []),
        regions=raw.get("regions", []),
        categories=raw.get("categories", []),
        primary_layer=raw.get("primary_layer", "topic"),
        sources=raw.get("sources", []),
        max_candidates=limits.get("max_candidates", 12),
        max_daily_items=limits.get("max_daily_items", 1),
    )
