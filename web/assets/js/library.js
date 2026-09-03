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
  view: "grid",
  day: null,
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
      `<div class="col-span-full text-center py-16 text-red-400">Could not load library data: ${esc(e.message)}</div>`;
    return;
  }
  buildChips(state.items);
  buildFilters(state.items);
  bindEvents();
  // view toggle default grid
  document.getElementById("view-grid").classList.add("on");
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
  // ?d=YYYY-MM-DD — day-by-day view (from report daily log / library links)
  const preD = queryParam("d");
  if (preD && /^\d{4}-\d{2}-\d{2}$/.test(preD)) {
    state.day = preD;
  }
  applyFilters();
  renderDayBanner();
  renderCollectionHead();
}

const COLL_META = {
  "world-news": { name: "World News", icon: "🌍", desc: "Geopolitics, conflicts, diplomacy and global affairs." },
  "tech-news": { name: "Tech & AI", icon: "🤖", desc: "AI research, models, open source and the companies building the future." },
  politics: { name: "Politics", icon: "🏛️", desc: "Policy, elections and political economy from UK and global sources." },
  products: { name: "Products", icon: "📦", desc: "New launches and tools worth knowing — from Product Hunt and beyond." },
};

function renderCollectionHead() {
  const meta = COLL_META[state.activeCollection];
  const h = document.getElementById("lib-title");
  const s = document.getElementById("lib-sub");
  if (meta) {
    h.textContent = `${meta.icon} ${meta.name}`;
    if (s) s.textContent = meta.desc;
  } else {
    h.textContent = "Every briefing, searchable";
    if (s) s.textContent = "";
  }
}

function renderDayBanner() {
  const bar = document.getElementById("day-bar");
  if (!bar) return;
  if (state.day) {
    bar.classList.remove("hidden");
    bar.innerHTML = `
      <span class="text-xs text-slate-400 font-semibold uppercase tracking-wider">Viewing</span>
      <span class="text-sm font-semibold text-white">${esc(state.day)}</span>
      <span class="text-xs text-slate-500">· ${state.filtered.length} briefings</span>
      <button id="day-clear" class="chip ml-2">Show all days</button>`;
    document.getElementById("day-clear").addEventListener("click", () => {
      state.day = null;
      document.getElementById("day-bar").classList.add("hidden");
      applyFilters();
    });
    // hide collection chips/topic selects? keep but they still apply
    const libHead = document.getElementById("lib-head");
    if (libHead) libHead.scrollIntoView({ block: "start" });
  } else {
    bar.classList.add("hidden");
  }
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
      renderCollectionHead();
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
  // view toggle
  const setView = (v) => {
    state.view = v;
    document.getElementById("view-grid").classList.toggle("on", v === "grid");
    document.getElementById("view-list").classList.toggle("on", v === "list");
    renderGrid();
  };
  document.getElementById("view-grid").addEventListener("click", () => setView("grid"));
  document.getElementById("view-list").addEventListener("click", () => setView("list"));
}

function applyFilters() {
  const q = state.query;
  state.filtered = state.items.filter((i) => {
    if (state.day && i.date !== state.day) return false;
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

function collectionBadge(i, compact) {
  const c = canonicalCollection(i.collection || i.topic);
  const meta = { "world-news": { name: "World News", icon: "🌍" }, "tech-news": { name: "Tech & AI", icon: "🤖" }, politics: { name: "Politics", icon: "🏛️" }, products: { name: "Products", icon: "📦" } }[c] || { name: pretty(i.collection || i.topic || ""), icon: "🗞️" };
  const cls = { "world-news": "collection-w", "tech-news": "collection-t", politics: "collection-p", products: "collection-pr" }[c] || "collection-default";
  return `<span class="badge ${cls}">${meta.icon}${compact ? "" : " " + esc(meta.name)}</span>`;
}

function renderGrid() {
  const grid = document.getElementById("grid");
  const list = document.getElementById("list");
  const slice = state.filtered.slice(0, state.shown);
  const has = slice.length > 0;
  if (state.view === "grid") {
    grid.innerHTML = slice.map(cardHTML).join("");
    grid.classList.remove("hidden");
    list.classList.add("hidden");
  } else {
    list.innerHTML = slice.map(listRowHTML).join("");
    list.classList.remove("hidden");
    grid.classList.add("hidden");
  }
  document.getElementById("empty").classList.toggle("hidden", has);
  document.getElementById("load-more-wrap").classList.toggle("hidden", state.shown >= state.filtered.length);
}

function verifyBadge(v) {
  if (v === "gemini") return `<span class="badge" style="background:rgba(52,211,153,.13);color:#6ee7b7;border:1px solid rgba(52,211,153,.25)" title="Verified with Gemini web-search">🤖 AI-check</span>`;
  if (v === "web") return `<span class="badge" style="background:rgba(56,189,248,.13);color:#7dd3fc;border:1px solid rgba(56,189,248,.25)" title="Corroborated against independent outlets via Google News">🌐 Web-corroborated</span>`;
  if (v === "lexical") return `<span class="badge type" title="Checked against source text (no AI search quota)">⚙️ Lexical</span>`;
  return `<span class="badge" style="background:rgba(255,255,255,.05);color:#94a3b8;border:1px solid rgba(255,255,255,.1)" title="Not yet through a check pass">◌ Unchecked</span>`;
}

function cardHTML(i) {
  const tags = (i.tags || []).slice(0, 2).map((t) => `<span class="badge type">${esc(t)}</span>`).join(" ");
  return `
  <a href="${articleHref(i)}" class="lib-card p-5">
    <div class="flex items-center justify-between mb-3">
      ${collectionBadge(i)}
      ${verifyBadge(i.verify)}
    </div>
    <h3 class="font-semibold text-white leading-snug mb-2">${esc(i.title)}</h3>
    <p class="text-sm text-slate-400 clamp mb-4 flex-1">${esc(i.tldr || "")}</p>
    <div class="flex items-center justify-between text-xs text-slate-500 pt-3 border-t border-white/8">
      <div class="flex flex-wrap gap-1">${tags}</div>
      <span class="read-arrow text-indigo-300 font-semibold inline-flex items-center gap-1">Read <span aria-hidden="true">→</span></span>
    </div>
  </a>`;
}

function listRowHTML(i) {
  return `
  <a href="${articleHref(i)}" class="list-row group">
    <span class="flex-1 min-w-0">
      <span class="flex items-center gap-2 text-[11px] text-slate-500 mb-0.5">
        <span class="badge type">${esc(pretty(i.collection || i.topic || ""))}</span>
        <span>${esc(formatDate(i.date))}</span>
        ${i.word_count ? `<span>· ${i.word_count.toLocaleString()} words</span>` : ""}
      </span>
      <span class="block font-semibold text-slate-100 leading-snug line-clamp-1 group-hover:text-white">${esc(i.title)}</span>
    </span>
    <svg class="w-4 h-4 text-slate-600 group-hover:text-indigo-300 group-hover:translate-x-0.5 transition-all shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
  </a>`;
}

document.addEventListener("DOMContentLoaded", init);
