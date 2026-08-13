"""information-hub — configuration loader.

Loads config.yml (taxonomy engine) + policies.yml (collection rules)
and exposes them as typed plain dicts/dataclasses.

V2: nested (hierarchical) taxonomy, relations, multi-key policy,
    collection priority/frequency/api_key controls.
V3: providers (roles collect/check) + quality thresholds + run phases.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

VALID_FREQUENCIES = ("daily", "every-2-days", "weekly")
VALID_KEY_STRATEGIES = ("round_robin", "least_used")
VALID_PROVIDER_FORMATS = ("google", "openai")
VALID_ROLES = ("collect", "check")


@dataclass
class ProviderConfig:
    name: str
    enabled: bool = True
    keys_env: str = ""
    role: str = "collect"
    format: str = "openai"
    models: list[str] = field(default_factory=list)
    discover: str | None = None         # "free_models" → runtime auto-discover
    search_tool: str | None = None      # "google_search" for check providers
    base_url: str = ""
    max_items: int = 2
    max_calls: int = 10

    def env_keys(self) -> list[str]:
        raw = os.environ.get(self.keys_env, "") if self.keys_env else ""
        if not raw:
            raw = _read_dotenv(self.keys_env)
        if not raw:
            return []
        return [k.strip() for k in raw.split(",") if k.strip()]


@dataclass
class QualityConfig:
    reject_threshold: float = 0.5
    max_ai_verify_per_run: int = 10


@dataclass
class RunConfig:
    phases: list[str] = field(default_factory=lambda: ["collect", "check"])


@dataclass
class KeyPolicy:
    rotate_on_error: list[int] = field(default_factory=lambda: [429, 500])
    max_errors_per_key: int = 3


@dataclass
class GeminiConfig:
    model: str = "gemini-2.5-flash"
    temperature: float = 0.4
    max_output_tokens: int = 2048
    retries: int = 2
    key_strategy: str = "least_used"
    key_policy: KeyPolicy = field(default_factory=KeyPolicy)

    def api_keys(self) -> list[str]:
        """All configured keys from GEMINI_API_KEYS (multi) or GEMINI_API_KEY (legacy)."""
        keys = os.environ.get("GEMINI_API_KEYS", "")
        if not keys:
            keys = os.environ.get("GEMINI_API_KEY", "")
            if not keys:
                keys = _read_dotenv("GEMINI_API_KEYS") or _read_dotenv("GEMINI_API_KEY")
        if not keys:
            return []
        return [k.strip() for k in keys.split(",") if k.strip()]

    @property
    def api_key(self) -> str:
        """Legacy single-key accessor (first key)."""
        keys = self.api_keys()
        return keys[0] if keys else ""


def _read_dotenv(name: str) -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@dataclass
class StorageConfig:
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    max_daily_items_total: int = 6


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
    priority: int = 1
    frequency: str = "daily"
    api_key: str = "auto"           # key id from GEMINI_API_KEYS, or "auto"
    content_type: str = "article"
    topics: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_layer: str = "topic"
    sources: list[dict[str, Any]] = field(default_factory=list)
    max_candidates: int = 12
    max_daily_items: int = 1


class Taxonomy:
    """Hierarchical classification layers.

    Each layer maps parent -> children. A flat list (old format) is accepted
    and treated as leaf-only nodes under an implicit parent "".
    """

    def __init__(self, raw: dict[str, Any]):
        raw = raw or {}
        self.content_types: list[str] = list(raw.get("content_types", []))
        self.regions: dict[str, list[str]] = _normalize_layer(raw.get("regions", []))
        self.topics: dict[str, list[str]] = _normalize_layer(raw.get("topics", []))
        self.categories: dict[str, list[str]] = _normalize_layer(raw.get("categories", []))

    def layers(self) -> dict[str, dict[str, list[str]]]:
        return {
            "regions": self.regions,
            "topics": self.topics,
            "categories": self.categories,
        }

    def parents_of(self, node: str) -> list[str]:
        parents: list[str] = []
        for layer in (self.regions, self.topics, self.categories):
            for parent, children in layer.items():
                if node in children:
                    parents.append(parent)
        return parents

    def children_of(self, node: str) -> list[str]:
        for layer in (self.regions, self.topics, self.categories):
            if node in layer:
                return list(layer[node])
        return []

    def all_nodes(self) -> list[str]:
        nodes: set[str] = set()
        for layer in (self.regions, self.topics, self.categories):
            for parent, children in layer.items():
                nodes.add(parent)
                nodes.update(children)
        return sorted(nodes)

    def layer_of(self, node: str) -> str | None:
        for layer_name, mapping in (("region", self.regions),
                                    ("topic", self.topics),
                                    ("category", self.categories)):
            if node in mapping or any(node in v for v in mapping.values()):
                return layer_name
        return None

    def node_id(self, node: str) -> str:
        layer = self.layer_of(node) or "misc"
        return f"taxonomy/{layer}/{node}"


def _normalize_layer(raw: Any) -> dict[str, list[str]]:
    if isinstance(raw, dict):
        return {str(k): [str(c) for c in (v or [])] for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(n): [] for n in raw}
    return {}


@dataclass
class Config:
    gemini: GeminiConfig
    storage: StorageConfig
    content: ContentConfig
    taxonomy: Taxonomy
    collections: dict[str, CollectionConfig]
    relations: list[dict[str, str]]
    providers: dict[str, ProviderConfig]
    quality: QualityConfig
    run: RunConfig
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

        gemini_raw = raw.get("gemini", {})
        key_policy_raw = gemini_raw.get("key_policy", {}) or {}
        gemini = GeminiConfig(
            model=gemini_raw.get("model", "gemini-2.5-flash"),
            temperature=gemini_raw.get("temperature", 0.4),
            max_output_tokens=gemini_raw.get("max_output_tokens", 2048),
            retries=gemini_raw.get("retries", 2),
            key_strategy=gemini_raw.get("key_strategy", "least_used"),
            key_policy=KeyPolicy(
                rotate_on_error=list(key_policy_raw.get("rotate_on_error", [429, 500])),
                max_errors_per_key=int(key_policy_raw.get("max_errors_per_key", 3)),
            ),
        )
        storage = StorageConfig(
            data_dir=Path(str(raw.get("storage", {}).get("data_dir", "data"))),
            max_daily_items_total=raw.get("storage", {}).get("max_daily_items_total", 6),
        )
        content_raw = raw.get("content", {})
        target = content_raw.get("target_words", [600, 1000])
        content = ContentConfig(
            min_words=content_raw.get("min_words", 500),
            target_words=(target[0], target[1]) if isinstance(target, list) and len(target) == 2
                         else (600, 1000),
            fulltext_max_chars=content_raw.get("fulltext_max_chars", 6000),
            similarity_window=content_raw.get("similarity_window", 30),
            similarity_threshold=content_raw.get("similarity_threshold", 0.55),
        )
        taxonomy = Taxonomy(raw.get("taxonomy", {}))
        collections = {
            name: _collection_from(name, raw.get("collections", {}).get(name, {}))
            for name in raw.get("collections", {})
        }
        relations = [dict(r) for r in (raw.get("relations", []) or [])]

        providers_raw = raw.get("providers", {})
        providers = {
            name: _provider_from(name, providers_raw.get(name, {}))
            for name in providers_raw
        }
        quality_raw = raw.get("quality", {})
        quality = QualityConfig(
            reject_threshold=quality_raw.get("reject_threshold", 0.5),
            max_ai_verify_per_run=quality_raw.get("max_ai_verify_per_run", 10),
        )
        run_raw = raw.get("run", {})
        run = RunConfig(phases=list(run_raw.get("phases", ["collect", "check"])))

        return cls(
            gemini=gemini,
            storage=storage,
            content=content,
            taxonomy=taxonomy,
            collections=collections,
            relations=relations,
            providers=providers,
            quality=quality,
            run=run,
            policies=policies,
            path=cfg_path,
        )

    def enabled_collections(self) -> list[CollectionConfig]:
        return [c for c in self.collections.values() if c.enabled]

    def collections_by_priority(self) -> list[CollectionConfig]:
        """Enabled collections sorted by priority descending (များလေ အရင်ရလေ)."""
        return sorted(self.enabled_collections(), key=lambda c: c.priority, reverse=True)

    def providers_for_role(self, role: str) -> list[ProviderConfig]:
        """Enabled providers with keys available for the given role."""
        out: list[ProviderConfig] = []
        for p in self.providers.values():
            if p.enabled and p.role == role and p.env_keys():
                out.append(p)
        return out


def _provider_from(name: str, raw: dict[str, Any]) -> ProviderConfig:
    budget = raw.get("budget", {})
    return ProviderConfig(
        name=name,
        enabled=raw.get("enabled", True),
        keys_env=raw.get("keys_env", ""),
        role=raw.get("role", "collect"),
        format=raw.get("format", "openai"),
        models=[str(m) for m in raw.get("models", [])],
        discover=raw.get("discover"),
        search_tool=raw.get("search_tool"),
        base_url=raw.get("base_url", ""),
        max_items=int(budget.get("max_items", 2)),
        max_calls=int(budget.get("max_calls", 10)),
    )


def _collection_from(name: str, raw: dict[str, Any]) -> CollectionConfig:
    limits = raw.get("limits", {})
    return CollectionConfig(
        name=name,
        enabled=raw.get("enabled", True),
        priority=int(raw.get("priority", 1)),
        frequency=raw.get("frequency", "daily"),
        api_key=str(raw.get("api_key", "auto")),
        content_type=raw.get("content_type", "article"),
        topics=[str(t) for t in raw.get("topics", [])],
        regions=[str(r) for r in raw.get("regions", [])],
        categories=[str(c) for c in raw.get("categories", [])],
        primary_layer=raw.get("primary_layer", "topic"),
        sources=raw.get("sources", []),
        max_candidates=limits.get("max_candidates", 12),
        max_daily_items=limits.get("max_daily_items", 1),
    )
