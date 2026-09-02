/* Information Hub — library page: chips, search, filters, grid. */
"use strict";

const state = {
  items: [],
  filtered: [],
  shown: 0,
  pageSize: 20,
  activeCollection: "all",
  topic: "all",
  region: "all",
  query: "",
  sort: "newest",
};

function canonicalCollection(c) {
  const map = { world: "world-news", "ai-ml": "tech-news", geopolitics: "politics", "dev-oss": "tech-news", "world-news": "world-news", "tech-news": "tech-news", politics: "politics", products: "products" };
  return map[c] || "other";
}

async function init() {
  try {
    const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
    state.items = Array.isArray(index) ? index : (index.items || []);
  } catch (e) {
    document.getElementById("grid").innerHTML =
      `<div class="col-span-full text-center py-16 text-red-500">Could not load library data: ${esc(e.message)}</div>`;
    return;
  }
  buildChips(state.items);
  buildFilters(state.items);
  bindEvents();
  // ?c=collection preselect from landing collection cards
  const pre = queryParam("c");
  if (pre && ["world-news", "tech-news", "politics", "products"].includes(pre)) {
    state.activeCollection = pre;
    document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("active", c.dataset.name === pre));
  }
  // ?q= search preselect (entity click from article)
  const preQ = queryParam("q");
  if (preQ) {
    const search = document.getElementById("search");
    search.value = preQ;
    state.query = preQ.toLowerCase();
  }
  applyFilters();
}

function pretty(name) {
  return String(name || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function buildChips(items) {
  const counts = {};
  items.forEach((i) => {
    const c = canonicalCollection(i.collection || i.topic);
    counts[c] = (counts[c] || 0) + 1;
  });
  const chipsEl = document.getElementById("chips");
  chipsEl.innerHTML = "";
  const order = ["all", "world-news", "tech-news", "politics", "products"];
  const label = { all: "All", "world-news": "World News", "tech-news": "Tech & AI", politics: "Politics", products: "Products" };
  order.forEach((name) => {
    if (name !== "all" && !counts[name]) return;
    const b = document.createElement("button");
    b.className = "chip" + (name === "all" ? " active" : "");
    b.textContent = `${label[name]} (${name === "all" ? items.length : counts[name]})`;
    b.dataset.name = name;
    b.onclick = () => {
      state.activeCollection = name;
      document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("active", c.dataset.name === name));
      applyFilters();
    };
    chipsEl.appendChild(b);
  });
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
    o.value = k; o.textContent = `${pretty(k)} (${n})`;
    sel.appendChild(o);
  });
}

function bindEvents() {
  document.getElementById("search").addEventListener("input", (e) => { state.query = e.target.value.toLowerCase(); applyFilters(); });
  document.getElementById("topic-filter").addEventListener("change", (e) => { state.topic = e.target.value; applyFilters(); });
  document.getElementById("region-filter").addEventListener("change", (e) => { state.region = e.target.value; applyFilters(); });
  document.getElementById("sort").addEventListener("change", (e) => { state.sort = e.target.value; applyFilters(); });
  document.getElementById("load-more").addEventListener("click", () => { state.shown += state.pageSize; renderGrid(); });
}

function applyFilters() {
  const q = state.query;
  state.filtered = state.items.filter((i) => {
    if (state.activeCollection !== "all" && canonicalCollection(i.collection || i.topic) !== state.activeCollection) return false;
    if (state.topic !== "all" && i.topic !== state.topic) return false;
    if (state.region !== "all" && i.region !== state.region) return false;
    if (q) {
      const hay = [i.title, i.tldr, i.topic, i.region, (i.tags || []).join(" "), (i.entities || []).join(" ")].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (state.sort === "oldest") state.filtered.sort((a, b) => a.date.localeCompare(b.date));
  else if (state.sort === "wordcount") state.filtered.sort((a, b) => (b.word_count || 0) - (a.word_count || 0));
  else state.filtered.sort((a, b) => b.date.localeCompare(a.date));
  state.shown = state.pageSize;
  document.getElementById("meta-line").textContent = `${state.filtered.length} briefings · updated from live data`;
  renderGrid();
}

function collectionBadge(i) {
  const c = canonicalCollection(i.collection || i.topic);
  const meta = { "world-news": { name: "World News", icon: "🌍" }, "tech-news": { name: "Tech & AI", icon: "🤖" }, politics: { name: "Politics", icon: "🏛️" }, products: { name: "Products", icon: "📦" } }[c] || { name: pretty(i.collection || i.topic || ""), icon: "🗞️" };
  const cls = { "world-news": "collection-w", "tech-news": "collection-t", politics: "collection-p", products: "collection-pr" }[c] || "collection-default";
  return `<span class="badge ${cls}">${meta.icon} ${esc(meta.name)}</span>`;
}

function renderGrid() {
  const grid = document.getElementById("grid");
  const slice = state.filtered.slice(0, state.shown);
  grid.innerHTML = slice.map(cardHTML).join("");
  document.getElementById("empty").classList.toggle("hidden", slice.length > 0);
  document.getElementById("load-more-wrap").classList.toggle("hidden", state.shown >= state.filtered.length);
}

function cardHTML(i) {
  const tags = (i.tags || []).slice(0, 3).map((t) => `<span class="badge type">${esc(t)}</span>`).join(" ");
  return `
  <a href="${articleHref(i)}" class="lib-card p-5">
    <div class="flex items-center justify-between mb-2.5">
      ${collectionBadge(i)}
      <span class="text-xs text-slate-500">${esc(formatDate(i.date))}</span>
    </div>
    <h3 class="font-semibold text-white leading-snug mb-2">${esc(i.title)}</h3>
    <p class="text-sm text-slate-400 clamp mb-4 flex-1">${esc(i.tldr || "")}</p>
    <div class="flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-white/8">
      <div class="flex flex-wrap gap-1">${tags}</div>
      <span class="whitespace-nowrap ml-2">${i.word_count ? i.word_count.toLocaleString() + " words" : ""}</span>
    </div>
  </a>`;
}

document.addEventListener("DOMContentLoaded", init);
