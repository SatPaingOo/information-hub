/* Information Hub — interactive knowledge graph (D3 force-directed). */
"use strict";

const TYPE_COLORS = {
  item: "#6366f1",
  person: "#0ea5e9",
  company: "#f59e0b",
  organization: "#f59e0b",
  product: "#ec4899",
  concept: "#10b981",
  model: "#8b5cf6",
  region: "#ef4444",
  event: "#f97316",
  taxonomy: "#94a3b8",
};
const TYPE_LABEL = {
  item: "Item", person: "Person", company: "Company", organization: "Organization",
  product: "Product", concept: "Concept", model: "Model", region: "Region",
  event: "Event", taxonomy: "Taxonomy",
};

async function init() {
  try {
    const g = await fetchJSON(`${DATA_DIR}/views/graph.json`);
    // item nodes in graph.json carry only id/type/date/title — resolve the
    // data-set file path from views/index.json when one is clicked.
    let idToFile = {};
    try {
      const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
      index.forEach((it) => { if (it.file) idToFile[it.id] = it.file; });
    } catch (e) { /* graph still renders without file resolution */ }
    build(g, idToFile);
  } catch (e) {
    document.getElementById("graph").innerHTML =
      `<text x="50%" y="50%" text-anchor="middle" fill="#f87171">Could not load graph: ${esc(e.message)}</text>`;
  }
}

function build(g, idToFile) {
  const nodes = (g.nodes || []).map((n) => Object.assign({}, n));
  const links = (g.edges || []).map((e) => ({
    source: e.source, target: e.target, relation: e.relation || "relates",
  }));
  // index by id for lookups
  const byId = {};
  nodes.forEach((n) => { byId[n.id] = n; });

  // Stats + legend
  const itemCount = nodes.filter((n) => n.type === "item").length;
  const entityCount = nodes.filter((n) => n.type !== "item" && n.type !== "taxonomy").length;
  document.getElementById("graph-stats").textContent =
    `${nodes.length} nodes · ${links.length} edges · ${itemCount} briefings · ${entityCount} entities`;
  buildLegend();

  // Keep graph responsive: show all nodes but make item nodes small.
  const width = Math.max(640, document.getElementById("graph").clientWidth || 900);
  const height = 640;

  const svg = d3.select("#graph")
    .attr("viewBox", [0, 0, width, height]);

  const zoom = d3.zoom()
    .scaleExtent([0.2, 6])
    .on("zoom", (ev) => gEl.attr("transform", ev.transform));
  const gEl = svg.append("g");
  svg.call(zoom);

  // color/type helpers
  const nodeType = (n) => (n.type === "item" ? "item" : n.type === "taxonomy" ? "taxonomy" : n.type || "concept");

  const link = gEl.append("g").selectAll("line").data(links).join("line")
    .attr("class", "link")
    .attr("stroke-width", (d) => (d.relation === "classified_in" || d.relation === "references" ? 0.6 : 1.2));

  const node = gEl.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", dragStart).on("drag", drag).on("end", dragEnd));

  // item = small dot; entity = circle with label
  const r = (n) => (n.type === "item" ? 2.5 : n.type === "taxonomy" ? 3.5 : Math.min(16, 5 + (n.title || n.name || "").length * 0.6));
  node.append("circle")
    .attr("r", r)
    .attr("fill", (n) => TYPE_COLORS[nodeType(n)] || "#94a3b8")
    .attr("stroke", "#0f172a")
    .attr("stroke-width", (n) => (n.type === "item" ? 0.5 : 1));
  node.append("text")
    .attr("class", "node-label")
    .attr("dx", (n) => r(n) + 3)
    .attr("dy", 3)
    .text((n) => (n.type === "item" ? "" : (n.title || n.name || "").slice(0, 24)));

  node.on("click", (ev, n) => showNodeDetail(n, byId, links, idToFile));

  const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance(28).strength(0.35))
    .force("charge", d3.forceManyBody().strength(-140))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("x", d3.forceX(width / 2).strength(0.05))
    .force("y", d3.forceY(height / 2).strength(0.05))
    .force("collide", d3.forceCollide().radius(6))
    .on("tick", () => {
      link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

  // stop after settle
  simulation.on("end", () => simulation.stop());

  function dragStart(ev, d) {
    if (!ev.active) simulation.alphaTarget(0.2).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function drag(ev, d) { d.fx = ev.x; d.fy = ev.y; }
  function dragEnd(ev, d) {
    if (!ev.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  }
}

function buildLegend() {
  const legend = document.getElementById("legend");
  const seen = new Set();
  Object.entries(TYPE_COLORS).forEach(([type, color]) => {
    const label = TYPE_LABEL[type] || type;
    if (seen.has(label)) return;
    seen.add(label);
    const span = document.createElement("span");
    span.className = "flex items-center gap-1.5";
    span.innerHTML = `<span class="inline-block w-2.5 h-2.5 rounded-full" style="background:${color}"></span>${label}`;
    legend.appendChild(span);
  });
}

function showNodeDetail(n, byId, links, idToFile) {
  const panel = document.getElementById("node-detail");
  panel.classList.remove("hidden");
  const type = n.type || "node";
  const label = n.title || n.name || n.id;
  const fileFor = (node) => (node.file || idToFile[node.id] || "").replace(/^data\//, "");

  if (type === "item") {
    const file = fileFor(n);
    panel.innerHTML = `
      <div class="text-xs text-slate-500 mb-1">Briefing · ${esc(n.date || "")}</div>
      <h2 class="text-lg font-bold text-white mb-2">${esc(label)}</h2>
      <p class="text-sm text-slate-400 mb-3">${esc(n.tldr || "")}</p>
      ${file ? `<a href="article.html?file=${encodeURIComponent(file)}" class="text-indigo-600 hover:underline text-sm">Open briefing →</a>` : ""}`;
    return;
  }

  // entity / taxonomy node: show neighbors
  const neighborIds = new Set();
  links.forEach((l) => {
    const s = l.source.id || l.source, t = l.target.id || l.target;
    if (s === n.id) neighborIds.add(t);
    if (t === n.id) neighborIds.add(s);
  });
  const neighbors = [...neighborIds].slice(0, 24).map((id) => byId[id]).filter(Boolean);
  const itemNeighbors = neighbors.filter((x) => x.type === "item");
  panel.innerHTML = `
    <div class="text-xs text-slate-500 mb-1">${esc(TYPE_LABEL[type] || pretty(type))}</div>
    <h2 class="text-lg font-bold text-white mb-3">${esc(label)}</h2>
    <div class="text-xs text-slate-400 mb-3">Connected to ${neighborIds.size} nodes${itemNeighbors.length ? ` · ${itemNeighbors.length} briefings` : ""}</div>
    <div class="flex flex-wrap gap-2 text-sm">
      ${itemNeighbors.slice(0, 8).map((x) => {
        const file = fileFor(x);
        return file ? `<a href="article.html?file=${encodeURIComponent(file)}" class="badge collection-default">${esc(x.title)}</a>` : "";
      }).join("")}
    </div>`;
}

function pretty(name) {
  return String(name || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

document.addEventListener("DOMContentLoaded", init);
