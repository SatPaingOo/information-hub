"""information-hub — indexer (storage layer).

Rebuilds the generated classification layer views (under ``data/views/`` —
derived, always rebuildable from the canonical records in ``data-set/``):

  views/by-topic/<topic>.json          -> item ids + titles
  views/by-region/<region>.json
  views/by-content-type/<type>.json
  views/by-category/<category>.json
  views/by-date/<date>.json
  views/by-entity/<entity>.json
  views/index.json                     master flat view
  views/graph.json                     nodes + edges (GraphRAG-ready)
  views/taxonomy.json                  hierarchy flat view (parent -> children + counts)
  preview/daily/<date>.md              Obsidian daily hub (generated)
  preview/entities/<type>/<name>.md    entity node notes (generated)
  preview/taxonomy/<layer>/<node>.md   taxonomy node notes (generated)

Taxonomy nodes/edges combine hierarchy-derived (parent_of), config static
relations, item-derived cross-layer edges, and AI semantic related_taxonomy.

Role: both phases — consumed by main and seed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.render.markdown import daily_index_markdown, entity_markdown, taxonomy_note_markdown


class Indexer:
    def __init__(self, data_dir: Path):
        base = Path(data_dir)
        self.preview = base / "collections" / "preview"
        self.index_dir = base / "views"          # derived views (rebuildable)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def rebuild(self, records: list[dict[str, Any]],
                taxonomy: Any | None = None,
                relations: list[dict[str, str]] | None = None) -> None:
        """Regenerate all layer views + master index + graph + taxonomy views."""
        relations = relations or []
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
            by_topic.setdefault(rec["topic"], []).append(item_id)
            by_region.setdefault(rec["region"], []).append(item_id)
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
            "file": f"data-set/{_record_name(r)}.json",
        } for r in sorted(records, key=lambda x: x["date"], reverse=True)]
        (self.index_dir / "index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # ---- graph (items + entities + taxonomy) -----------------------
        self._write_graph(records, taxonomy, relations)

        # ---- taxonomy hierarchy flat view ------------------------------
        self._write_taxonomy_index(records, taxonomy)

        # ---- generated Obsidian views ---------------------------------
        self._write_daily_hubs(records)
        self._write_entity_notes(entity_meta, records)
        self._write_taxonomy_notes(records, taxonomy, relations)

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

    # ---- graph ---------------------------------------------------------
    def _write_graph(self, records: list[dict[str, Any]],
                     taxonomy: Any | None,
                     relations: list[dict[str, str]]) -> None:
        nodes: dict[str, dict[str, str]] = {}
        edges: list[dict[str, str]] = []

        def add_node(node_id: str, **attrs: str) -> None:
            if node_id not in nodes:
                nodes[node_id] = {"id": node_id, **attrs}

        def add_edge(source: str, target: str, relation: str) -> None:
            if source == target:
                return
            edges.append({"source": source, "target": target, "relation": relation})

        for rec in records:
            add_node(rec["id"], type="item", date=rec["date"], title=rec["title"])
            for e in rec.get("entities", []):
                entity_id = f"info:entity:{e['type']}:{e['name']}"
                add_node(entity_id, type=e["type"], name=e["name"])
                add_edge(rec["id"], entity_id, e.get("relation", "references"))
            for rel in rec.get("related_items", []):
                add_edge(rec["id"], rel, "related")

            # item-derived cross-layer edges
            add_node(f"taxonomy/topic/{rec['topic']}", type="taxonomy", layer="topic",
                     name=rec["topic"])
            add_edge(rec["id"], f"taxonomy/topic/{rec['topic']}", "classified_in")
            add_node(f"taxonomy/region/{rec['region']}", type="taxonomy", layer="region",
                     name=rec["region"])
            add_edge(rec["id"], f"taxonomy/region/{rec['region']}", "classified_in")
            for cat in rec.get("categories", []):
                add_node(f"taxonomy/category/{cat}", type="taxonomy", layer="category",
                         name=cat)
                add_edge(rec["id"], f"taxonomy/category/{cat}", "classified_in")
            # AI semantic taxonomy relations
            for tr in rec.get("related_taxonomy", []):
                node = tr.get("node", "")
                if not node:
                    continue
                add_node(f"taxonomy/{_node_layer(taxonomy, node)}/{node}",
                         type="taxonomy", name=node)
                add_edge(rec["id"], f"taxonomy/{_node_layer(taxonomy, node)}/{node}",
                         tr.get("relation", "related"))

        # taxonomy nodes + hierarchy + static relations
        if taxonomy is not None:
            for layer_name, mapping in taxonomy.layers().items():
                short = _layer_short(layer_name)
                for parent, children in mapping.items():
                    add_node(f"taxonomy/{short}/{parent}", type="taxonomy",
                             layer=short, name=parent)
                    for child in children:
                        add_node(f"taxonomy/{short}/{child}", type="taxonomy",
                                 layer=short, name=child)
                        add_edge(f"taxonomy/{short}/{parent}",
                                 f"taxonomy/{short}/{child}", "parent_of")
            for rel in relations:
                frm, to = rel.get("from", ""), rel.get("to", "")
                rel_type = rel.get("type", "relates")
                if not frm or not to:
                    continue
                add_node(f"taxonomy/{_node_layer(taxonomy, frm)}/{frm}",
                         type="taxonomy", name=frm)
                add_node(f"taxonomy/{_node_layer(taxonomy, to)}/{to}",
                         type="taxonomy", name=to)
                add_edge(f"taxonomy/{_node_layer(taxonomy, frm)}/{frm}",
                         f"taxonomy/{_node_layer(taxonomy, to)}/{to}", rel_type)

        # entity co-occurrence edges
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

        graph = {"nodes": list(nodes.values()), "edges": edges}
        (self.index_dir / "graph.json").write_text(
            json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- taxonomy index ------------------------------------------------
    def _write_taxonomy_index(self, records: list[dict[str, Any]],
                              taxonomy: Any | None) -> None:
        counts: dict[str, int] = {}
        for rec in records:
            for node in (rec["topic"], rec["region"], *rec.get("categories", [])):
                counts[node] = counts.get(node, 0) + 1
            for tr in rec.get("related_taxonomy", []):
                counts[tr.get("node", "")] = counts.get(tr.get("node", ""), 0) + 1

        view: dict[str, Any] = {"nodes": {}}
        if taxonomy is not None:
            for layer_name, mapping in taxonomy.layers().items():
                short = _layer_short(layer_name)
                for parent, children in mapping.items():
                    view["nodes"][parent] = {
                        "layer": short,
                        "children": children,
                        "parents": [],
                        "item_count": counts.get(parent, 0),
                    }
                    for child in children:
                        entry = view["nodes"].setdefault(child, {
                            "layer": short, "children": [],
                            "parents": [], "item_count": 0,
                        })
                        entry["parents"] = entry.get("parents", []) + [parent]
                        entry["item_count"] = counts.get(child, 0)
        (self.index_dir / "taxonomy.json").write_text(
            json.dumps(view, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- generated Obsidian views ---------------------------------------
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

    def _write_taxonomy_notes(self, records: list[dict[str, Any]],
                              taxonomy: Any | None,
                              relations: list[dict[str, str]]) -> None:
        if taxonomy is None:
            return
        root = self.preview / "taxonomy"
        root.mkdir(parents=True, exist_ok=True)

        # items per taxonomy node
        items_by_node: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            for node in (rec["topic"], rec["region"], *rec.get("categories", [])):
                items_by_node.setdefault(node, []).append(rec)
            for tr in rec.get("related_taxonomy", []):
                items_by_node.setdefault(tr.get("node", ""), []).append(rec)

        # cross-layer relations (static + item-derived)
        node_relations: dict[str, set[tuple[str, str]]] = {}
        for rel in relations:
            frm, to = rel.get("from", ""), rel.get("to", "")
            rel_type = rel.get("type", "relates")
            if frm and to:
                node_relations.setdefault(frm, set()).add((to, rel_type))
                node_relations.setdefault(to, set()).add((frm, rel_type))
        for rec in records:
            topic, region = rec.get("topic"), rec.get("region")
            for cat in rec.get("categories", []):
                node_relations.setdefault(topic, set()).add((cat, "classified_in"))
                node_relations.setdefault(region, set()).add((cat, "classified_in"))
            node_relations.setdefault(topic, set()).add((region, "regional_scope"))
            for tr in rec.get("related_taxonomy", []):
                node_relations.setdefault(tr.get("node", ""), set()).add(
                    (topic, tr.get("relation", "related")))

        for layer_name, mapping in taxonomy.layers().items():
            layer_dir = root / layer_name
            layer_dir.mkdir(parents=True, exist_ok=True)
            for node in (list(mapping.keys()) +
                         [c for ch in mapping.values() for c in ch]):
                parents = taxonomy.parents_of(node)
                children = taxonomy.children_of(node)
                md = taxonomy_note_markdown(
                    node=node, layer=_layer_short(layer_name),
                    children=children, parents=parents,
                    items=items_by_node.get(node, []),
                    related_nodes=sorted(node_relations.get(node, set())),
                )
                (layer_dir / _safe_name(node)).with_suffix(".md").write_text(
                    md + "\n", encoding="utf-8")


def _record_name(rec: dict[str, Any]) -> str:
    """Flat readable record filename: ``<key>-<title-slug>``."""
    from src.storage.store import record_filename
    return record_filename(rec)


def _layer_of(rec: dict[str, Any]) -> str:
    return rec.get("topic", "misc")


def _node_layer(taxonomy: Any | None, node: str) -> str:
    if taxonomy is not None:
        layer = taxonomy.layer_of(node)
        if layer:
            return layer
    return "misc"


def _layer_short(layer_name: str) -> str:
    return {"regions": "region", "topics": "topic", "categories": "category"}.get(
        layer_name, layer_name)


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
