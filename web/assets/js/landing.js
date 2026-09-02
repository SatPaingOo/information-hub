/* Information Hub — dark landing page: stats, latest spotlight, collections, how. */
"use strict";

const COLLECTION_META = {
  "world-news": { name: "World News", icon: "🌍", color: "#3b82f6", desc: "Geopolitics, conflicts, diplomacy and global affairs — distilled from world feeds." },
  "tech-news": { name: "Tech & AI", icon: "🤖", color: "#8b5cf6", desc: "AI research, models, open source and the companies building the future." },
  "politics": { name: "Politics", icon: "🏛️", color: "#ec4899", desc: "Policy, elections and political economy from UK and global sources." },
  "products": { name: "Products", icon: "📦", color: "#f59e0b", desc: "New launches and tools worth knowing — from Product Hunt and beyond." },
};
const fallbackMeta = { name: "Library", icon: "🗞️", color: "#64748b", desc: "Curated intelligence briefings." };

function collectionKey(i) {
  const c = i.collection || i.topic || "other";
  const map = { world: "world-news", "ai-ml": "tech-news", geopolitics: "politics", "dev-oss": "tech-news" };
  return map[c] || c;
}

async function init() {
  try {
    const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
    const items = Array.isArray(index) ? index : (index.items || []);
    renderStats(items);
    renderLatest(items);
    renderCollections(items);
    renderHow();
    // live badge: show most recent date
    if (items.length) {
      document.getElementById("live-text").textContent =
        `Live library · updated ${formatDate(items[0].date)}`;
    }
  } catch (e) {
    document.getElementById("latest").innerHTML =
      `<div class="col-span-full text-center py-16 text-slate-400">Could not load data: ${esc(e.message)}</div>`;
  }
}

function renderStats(items) {
  const days = new Set(items.map((i) => i.date));
  const entities = new Set();
  items.forEach((i) => (i.entities || []).forEach((en) => entities.add(en)));
  const stats = [
    { num: items.length, lbl: "Deep-dives" },
    { num: days.size, lbl: "Days covered" },
    { num: entities.size, lbl: "Entities mapped" },
    { num: items.reduce((s, i) => s + (i.word_count || 0), 0).toLocaleString(), lbl: "Words written" },
  ];
  document.getElementById("stats").innerHTML = stats.map((s) => `
    <div class="stat-card">
      <div class="num">${s.num}</div>
      <div class="lbl">${s.lbl}</div>
    </div>`).join("");
}

function badgeHTML(i) {
  const key = collectionKey(i);
  const meta = COLLECTION_META[key] || fallbackMeta;
  const cls = { "world-news": "collection-w", "tech-news": "collection-t", politics: "collection-p", products: "collection-pr" }[key] || "collection-default";
  return `<span class="badge ${cls}">${meta.icon} ${esc(meta.name)}</span>`;
}

function renderLatest(items) {
  const latest = items.slice(0, 5);
  const el = document.getElementById("latest");
  // feature = first (2/3 width); others smaller
  const featured = latest[0];
  const rest = latest.slice(1);
  const featuredCard = `
    <a href="${articleHref(featured)}" class="spotlight lg:col-span-3 p-6 flex flex-col justify-between min-h-[240px]">
      <div class="flex items-center gap-2 mb-4">
        ${badgeHTML(featured)}
        <span class="text-xs text-slate-500">${esc(formatDate(featured.date))}</span>
      </div>
      <div>
        <h3 class="text-xl sm:text-2xl font-display font-semibold text-white leading-snug mb-3 text-balance">${esc(featured.title)}</h3>
        <p class="text-slate-400 text-sm line-clamp-3">${esc(featured.tldr || "")}</p>
      </div>
      <div class="mt-4 text-xs text-slate-500">${featured.word_count ? featured.word_count.toLocaleString() + " words" : ""} · ${esc((featured.tags || []).slice(0, 3).join(" · "))}</div>
    </a>`;
  const restCards = rest.map((i) => `
    <a href="${articleHref(i)}" class="mini-card lg:col-span-1 p-4 flex flex-col gap-2.5">
      <div class="flex items-center justify-between">
        ${badgeHTML(i)}
        <span class="text-[11px] text-slate-500">${esc(formatDate(i.date))}</span>
      </div>
      <h3 class="text-[13.5px] font-semibold text-white leading-snug line-clamp-4">${esc(i.title)}</h3>
      <p class="text-[12px] text-slate-500 line-clamp-2 mt-auto">${esc(i.tldr || "")}</p>
    </a>`).join("");
  // 5 cols: featured 3 + 2 mini in row1; 2 more mini wrap below (grid auto handles)
  el.innerHTML = `<div class="lg:col-span-3 flex flex-col gap-5">${featuredCard}</div>
                  <div class="lg:col-span-2 flex flex-col gap-5">${restCards.slice(0, 2)}</div>
                  <div class="lg:col-span-2 lg:col-start-4 flex flex-col gap-5">${restCards.slice(2)}</div>`;
}

function renderCollections(items) {
  // group counts by canonical collection key
  const counts = {};
  const latestDate = {};
  items.forEach((i) => {
    const k = collectionKey(i);
    counts[k] = (counts[k] || 0) + 1;
    const d = i.date || "";
    if (!latestDate[k] || d > latestDate[k]) latestDate[k] = d;
  });
  const el = document.getElementById("collections");
  const order = ["world-news", "tech-news", "politics", "products"];
  el.innerHTML = order.map((k) => {
    if (!counts[k]) return "";
    const meta = COLLECTION_META[k];
    return `
    <a href="./library.html?c=${encodeURIComponent(k)}" class="collection-card group">
      <span class="glow" style="background:${meta.color}"></span>
      <span class="icon mb-3" style="background:${meta.color}22; border:1px solid ${meta.color}55">${meta.icon}</span>
      <h3 class="font-bold text-white text-lg mb-1">${esc(meta.name)}</h3>
      <p class="text-[13px] text-slate-400 leading-relaxed mb-4">${esc(meta.desc)}</p>
      <div class="flex items-center justify-between text-xs">
        <span class="text-slate-300 font-semibold">${counts[k]} briefings</span>
        <span class="text-indigo-300 group-hover:translate-x-0.5 transition-transform inline-flex items-center gap-1">Browse <span aria-hidden="true">→</span></span>
      </div>
    </a>`;
  }).join("");
}

function renderHow() {
  const steps = [
    { n: "01", icon: "🛰️", title: "Collect", desc: "A self-scheduling pipeline wakes on GitHub Actions, discovers free AI models across providers and collects until the daily target is met." },
    { n: "02", icon: "✍️", title: "Write", desc: "Each pick becomes a 500+ word structured deep-dive — background, analysis, key facts, implications, outlook — enforced by schema." },
    { n: "03", icon: "🔗", title: "Link", desc: "Entities, related briefings and taxonomy are cross-linked and rebuilt into a knowledge graph automatically every day." },
    { n: "04", icon: "🧾", title: "Verify & store", desc: "Every record carries its source, generating model, verification score and review status — a traceable machine dataset." },
  ];
  document.getElementById("how-grid").innerHTML = steps.map((s) => `
    <div class="how-card">
      <div class="step-no">STEP ${s.n}</div>
      <div class="text-2xl mt-2 mb-3">${s.icon}</div>
      <h3 class="font-semibold text-white mb-1.5">${esc(s.title)}</h3>
      <p class="text-[13px] text-slate-400 leading-relaxed">${esc(s.desc)}</p>
    </div>`).join("");
}

document.addEventListener("DOMContentLoaded", init);
