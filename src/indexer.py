"""information-hub — indexer.

Rebuilds the generated classification layer views from canonical records:

  index/by-topic/<topic>.json          -> item ids + titles
  index/by-region/<region>.json
  index/by-content-type/<type>.json
  index/by-category/<category>.json
  index/by-date/<date>.json
  index/by-entity/<entity>.json
  index/index.json                     master flat view
  index/graph.json                     nodes + edges (GraphRAG-ready)
  preview/daily/<date>.md              Obsidian daily hub (generated)
  preview/entities/<type>/<name>.md    entity node notes (generated)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.views import daily_index_markdown, entity_markdown


class Indexer:
    def __init__(self, data_dir: Path):
        base = Path(data_dir)
        self.collections = base / "collections"
        self.index_dir = self.collections / "index"
        self.preview = self.collections / "preview"
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def rebuild(self, records: list[dict[str, Any]]) -> None:
        """Regenerate all layer views + master index + graph from records."""
        self._clean_index_dir()
        by_topic: dict[str, list[str]] = {}
        by_region: dict[str, list[str]] = {}
        by_type: dict[str, list[str]] = {}
        by_category: dict[str, list[str]] = {}
        by_date: dict[str, list[str]] = {}
        by_entity: dict[str, list[str]] = {}
        entity_meta: dict[str, dict[str, Any]] = {}

        for rec in records:
            item_id = rec["id"]
            for topic in [rec["topic"]]:
                by_topic.setdefault(topic, []).append(item_id)
            for region in [rec["region"]]:
                by_region.setdefault(region, []).append(item_id)
            by_type.setdefault(rec["content_type"], []).append(item_id)
            for cat in rec.get("categories", []):
                by_category.setdefault(cat, []).append(item_id)
            by_date.setdefault(rec["date"], []).append(item_id)
            for e in rec.get("entities", []):
                name = e["name"]
                by_entity.setdefault(name, []).append(item_id)
                meta = entity_meta.setdefault(name, {"type": e["type"], "first_seen": rec["date"],
                                                     "last_seen": rec["date"]})
                meta["type"] = e["type"]
                if rec["date"] < meta["first_seen"]:
                    meta["first_seen"] = rec["date"]
                if rec["date"] > meta["last_seen"]:
                    meta["last_seen"] = rec["date"]

        # ---- flat layer views ---------------------------------------
        self._write_views("by-topic", by_topic)
        self._write_views("by-region", by_region)
        self._write_views("by-content-type", by_type)
        self._write_views("by-category", by_category)
        self._write_views("by-date", by_date)
        self._write_views("by-entity", by_entity)

        # ---- master index --------------------------------------------
        index = [{
            "id": r["id"], "key": r["key"], "date": r["date"],
            "content_type": r["content_type"], "topic": r["topic"],
            "region": r["region"], "categories": r["categories"],
            "title": r["title"], "tldr": r["tldr"],
            "source": r["source"]["name"], "source_url": r["source"]["url"],
            "entities": [e["name"] for e in r.get("entities", [])],
            "tags": r.get("tags", []),
            "file": f"data-set/{_layer_of(r)}/{r['key']}.json",
        } for r in sorted(records, key=lambda x: x["date"], reverse=True)]
        (self.index_dir / "index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # ---- graph ----------------------------------------------------
        self._write_graph(records)

        # ---- generated Obsidian views ---------------------------------
        self._write_daily_hubs(records)
        self._write_entity_notes(entity_meta, records)

    # ---- helpers ------------------------------------------------------
    def _clean_index_dir(self) -> None:
        for child in self.index_dir.iterdir():
            if child.is_dir():
                for f in child.glob("*.json"):
                    f.unlink()
            elif child.suffix == ".json":
                child.unlink()

    def _write_views(self, folder: str, mapping: dict[str, list[str]]) -> None:
        target = self.index_dir / folder
        target.mkdir(parents=True, exist_ok=True)
        for key in sorted(mapping):
            ids = sorted(set(mapping[key]))
            (target / f"{key}.json").write_text(
                json.dumps(ids, indent=2) + "\n", encoding="utf-8")

    def _write_graph(self, records: list[dict[str, Any]]) -> None:
        nodes: list[dict[str, str]] = []
        edges: list[dict[str, str]] = []
        for rec in records:
            nodes.append({"id": rec["id"], "type": "item",
                          "date": rec["date"], "title": rec["title"]})
            for e in rec.get("entities", []):
                entity_id = f"info:entity:{e['type']}:{e['name']}"
                nodes.append({"id": entity_id, "type": e["type"], "name": e["name"]})
                edges.append({"source": rec["id"], "target": entity_id,
                              "relation": e.get("relation", "references")})
            for rel in rec.get("related_items", []):
                edges.append({"source": rec["id"], "target": rel, "relation": "related"})
        # entity -> entity co-occurrence edges
        co_occur: dict[tuple[str, str], int] = {}
        for rec in records:
            ents = [f"info:entity:{e['type']}:{e['name']}" for e in rec.get("entities", [])]
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    key = tuple(sorted((a, b)))
                    co_occur[key] = co_occur.get(key, 0) + 1
        for (a, b), weight in sorted(co_occur.items()):
            edges.append({"source": a, "target": b, "relation": "co-occurs",
                          "weight": str(weight)})

        dedup_nodes = {n["id"]: n for n in nodes}
        graph = {"nodes": list(dedup_nodes.values()), "edges": edges}
        (self.index_dir / "graph.json").write_text(
            json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _write_daily_hubs(self, records: list[dict[str, Any]]) -> None:
        daily = self.preview / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        by_date: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            by_date.setdefault(rec["date"], []).append(rec)
        for date, recs in sorted(by_date.items()):
            md = daily_index_markdown(date, sorted(recs, key=lambda r: r["key"]))
            (daily / f"{date}.md").write_text(md + "\n", encoding="utf-8")

    def _write_entity_notes(self, entity_meta: dict[str, dict[str, Any]],
                            records: list[dict[str, Any]]) -> None:
        entity_root = self.preview / "entities"
        entity_root.mkdir(parents=True, exist_ok=True)
        type_dir = {t: entity_root / t for t in
                    ("concept", "company", "model", "person")}
        for tdir in type_dir.values():
            tdir.mkdir(parents=True, exist_ok=True)

        backlinks: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            for e in rec.get("entities", []):
                backlinks.setdefault(e["name"], []).append({
                    "id": rec["id"], "title": rec["title"], "date": rec["date"],
                })
        for name, meta in entity_meta.items():
            md = entity_markdown(name, meta["type"], backlinks.get(name, []))
            (type_dir[meta["type"]] / _safe_name(name)).with_suffix(".md").write_text(
                md + "\n", encoding="utf-8")


def _layer_of(rec: dict[str, Any]) -> str:
    return rec.get("topic", "misc")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
