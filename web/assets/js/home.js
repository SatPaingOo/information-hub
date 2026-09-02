/* Information Hub — library homepage: stats, filter chips, grid, search, load-more. */
"use strict";

const state = {
  items: [],
  filtered: [],
  shown: 0,
  pageSize: 18,
  activeCollection: "all",
  topic: "all",
  region: "all",
  query: "",
  sort: "newest",
};

async function init() {
  try {
    const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
    state.items = Array.isArray(index) ? index : (index.items || []);
  } catch (e) {
    document.getElementById("grid").innerHTML =
      `<div class="col-span-full text-center py-16 text-red-500">Could not load library data: ${esc(e.message)}</div>`;
    return;
  }
  renderStats(state.items);
  buildChips(state.items);
  buildFilters(state.items);
  bindEvents();
  applyFilters();
}

function renderStats(items) {
  const days = new Set(items.map((i) => i.date));
  const entities = new Set();
  items.forEach((i) => (i.entities || []).forEach((en) => entities.add(en)));
  const collections = new Set(items.map((i) => i.collection || i.topic).filter(Boolean));
  setText("stat-items", items.length);
  setText("stat-days", days.size);
  setText("stat-entities", entities.size);
  setText("stat-collections", collections.size);
}

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

function buildChips(items) {
  const counts = {};
  items.forEach((i) => {
    const c = i.collection || i.topic || "other";
    counts[c] = (counts[c] || 0) + 1;
  });
  const chipsEl = document.getElementById("chips");
  chipsEl.innerHTML = "";
  const all = mkChip("all", `All (${items.length})`, true);
  chipsEl.appendChild(all);
  Object.entries(counts).sort((a, b) => b[1] - a[1]).forEach(([name, n]) => {
    chipsEl.appendChild(mkChip(name, `${pretty(name)} (${n})`, false));
  });
}

function mkChip(name, label, active) {
  const b = document.createElement("button");
  b.className = "chip collection" + (active ? " active" : "");
  b.textContent = label;
  b.dataset.name = name;
  b.onclick = () => {
    state.activeCollection = name;
    document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("active", c.dataset.name === name));
    applyFilters();
  };
  return b;
}

function pretty(name) {
  return name.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function buildFilters(items) {
  const topics = {}, regions = {};
  items.forEach((i) => {
    if (i.topic) topics[i.topic] = (topics[i.topic] || 0) + 1;
    if (i.region) regions[i.region] = (regions[i.region] || 0) + 1;
  });
  fillSelect("topic-filter", topics, "All topics");
  fillSelect("region-filter", regions, "All regions");
}

function fillSelect(id, map, allLabel) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="all">${allLabel}</option>`;
  Object.entries(map).sort((a, b) => b[1] - a[1]).forEach(([k, n]) => {
    const o = document.createElement("option");
    o.value = k;
    o.textContent = `${pretty(k)} (${n})`;
    sel.appendChild(o);
  });
}

function bindEvents() {
  document.getElementById("search").addEventListener("input", (e) => {
    state.query = e.target.value.toLowerCase();
    applyFilters();
  });
  document.getElementById("topic-filter").addEventListener("change", (e) => {
    state.topic = e.target.value; applyFilters();
  });
  document.getElementById("region-filter").addEventListener("change", (e) => {
    state.region = e.target.value; applyFilters();
  });
  document.getElementById("sort").addEventListener("change", (e) => {
    state.sort = e.target.value; applyFilters();
  });
  document.getElementById("load-more").addEventListener("click", () => {
    state.shown += state.pageSize;
    renderGrid();
  });
}

function applyFilters() {
  const q = state.query;
  state.filtered = state.items.filter((i) => {
    if (state.activeCollection !== "all" && (i.collection || i.topic || "other") !== state.activeCollection) return false;
    if (state.topic !== "all" && i.topic !== state.topic) return false;
    if (state.region !== "all" && i.region !== state.region) return false;
    if (q) {
      const hay = [i.title, i.tldr, i.topic, i.region, (i.tags || []).join(" "), (i.entities || []).join(" ")]
        .join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (state.sort === "oldest") state.filtered.sort((a, b) => a.date.localeCompare(b.date));
  else if (state.sort === "wordcount") state.filtered.sort((a, b) => (b.word_count || 0) - (a.word_count || 0));
  else state.filtered.sort((a, b) => b.date.localeCompare(a.date));

  state.shown = state.pageSize;
  setText("meta-line", `${state.filtered.length} briefings`);
  renderGrid();
}

function renderGrid() {
  const grid = document.getElementById("grid");
  const slice = state.filtered.slice(0, state.shown);
  grid.innerHTML = slice.map(cardHTML).join("");
  document.getElementById("empty").classList.toggle("hidden", slice.length > 0);
  document.getElementById("load-more-wrap").classList.toggle("hidden", state.shown >= state.filtered.length);
}

function cardHTML(i) {
  const col = i.collection || i.topic || "other";
  const tags = (i.tags || []).slice(0, 3).map((t) => `<span class="chip">${esc(t)}</span>`).join(" ");
  return `
  <a href="${articleHref(i)}" class="card block bg-white border border-slate-200 rounded-xl p-5">
    <div class="flex items-center justify-between mb-2">
      <span class="chip collection">${esc(pretty(col))}</span>
      <span class="text-xs text-slate-400">${esc(formatDate(i.date))}</span>
    </div>
    <h3 class="font-semibold text-slate-900 leading-snug mb-2">${esc(i.title)}</h3>
    <p class="text-sm text-slate-500 line-clamp-3 mb-3">${esc(i.tldr || "")}</p>
    <div class="flex flex-wrap gap-1 items-center text-xs text-slate-400">
      ${tags}
      <span class="ml-auto">${i.word_count ? i.word_count.toLocaleString() + " words" : ""}</span>
    </div>
  </a>`;
}

document.addEventListener("DOMContentLoaded", init);
