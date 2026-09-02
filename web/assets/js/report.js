/* Information Hub — public pipeline report (client-side, date-range filterable).
   Reads data/views/index.json (per-record: date/collection/provider/model/verify)
   and renders KPIs + charts + model table + daily log for the selected range. */
"use strict";

const PALETTE = ["#818cf8", "#38bdf8", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#2dd4bf", "#f472b6", "#60a5fa", "#c084fc"];
const COL_LABEL = { "world-news": "World News", "tech-news": "Tech & AI", politics: "Politics", products: "Products" };
const VERIFY_META = { gemini: { label: "🤖 AI check (Gemini)", color: "#34d399" }, lexical: { label: "⚙️ Lexical check", color: "#38bdf8" }, unverified: { label: "Unverified", color: "#64748b" } };

let ITEMS = [];
let charts = {};

function canonColl(c) {
  const m = { world: "world-news", "ai-ml": "tech-news", "dev-oss": "tech-news", geopolitics: "politics" };
  return m[c] || c || "other";
}
function prettyLabel(c) { return COL_LABEL[c] || c; }

async function init() {
  try {
    const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
    ITEMS = (Array.isArray(index) ? index : (index.items || [])).map((i) => ({
      ...i,
      collection: canonColl(i.collection || i.topic),
      verify: i.verify || "unverified",
    }));
    setupRange();
    render();
    fetchJSON(`${DATA_DIR}/views/run-stats.json`).then((rs) => {
      RUN_STATS = rs;
      renderRunHealth();
    }).catch(() => { /* run log unavailable */ });
  } catch (e) {
    document.body.insertAdjacentHTML("beforeend",
      `<div class="max-w-6xl mx-auto px-5 py-16 text-red-400">Could not load report data: ${esc(e.message)}</div>`);
  }
}

let RUN_STATS = null;

/* run health from data/views/run-stats.json — respects the date range */
async function renderRunHealth() {
  const wrap = document.getElementById("run-health");
  if (!wrap) return;
  if (!RUN_STATS) return;
  const rs = RUN_STATS;
  const perDay = rs.per_day_provider_model || {};
  const errAll = rs.errors_by_day || {};
  // only days inside the current range
  const daysInRange = Object.keys(perDay).filter((d) => inRange(d)).sort();
  const agg = {};
  daysInRange.forEach((d) => {
    Object.entries(perDay[d] || {}).forEach(([prov, models]) => {
      Object.entries(models).forEach(([model, s]) => {
        const k = `${prov}/${model}`;
        const a = (agg[k] = agg[k] || { calls: 0, ok: 0, errors: 0, tokens: 0 });
        a.calls += s.calls || 0; a.ok += s.ok || 0; a.errors += s.errors || 0; a.tokens += s.tokens || 0;
      });
    });
  });
  const rows = Object.entries(agg).sort((a, b) => b[1].calls - a[1].calls);
  const maxCalls = rows.length ? rows[0][1].calls : 1;
  // error codes only within range
  const errCodes = {};
  daysInRange.forEach((d) => {
    Object.entries(errAll[d] || {}).forEach(([c, n]) => { errCodes[c] = (errCodes[c] || 0) + n; });
  });
  const errTop = Object.entries(errCodes).sort((a, b) => b[1] - a[1]).slice(0, 5);

  wrap.innerHTML = `
    <div class="space-y-2.5 mb-6">
      ${rows.length ? rows.map(([k, s]) => {
        const failRate = s.calls ? Math.round((s.errors / s.calls) * 100) : 0;
        const color = s.errors === 0 ? "#34d399" : failRate > 50 ? "#fb7185" : "#fbbf24";
        return `
        <div class="flex items-center gap-3 text-sm">
          <span class="w-72 truncate text-slate-300 shrink-0" title="${esc(k)}">${esc(k)}</span>
          <div class="flex-1 h-2 rounded-full bg-white/5 overflow-hidden">
            <div class="h-full rounded-full" style="width:${(s.calls / maxCalls) * 100}%;background:${color}"></div>
          </div>
          <span class="w-14 text-right text-slate-400 text-xs">${s.calls} calls</span>
          <span class="w-10 text-right text-emerald-300 text-xs">${s.ok}✓</span>
          <span class="w-10 text-right ${s.errors ? "text-rose-300" : "text-slate-600"} text-xs">${s.errors}✗</span>
          <span class="w-24 text-right text-slate-500 text-xs">${s.tokens.toLocaleString()} tok</span>
        </div>`;
      }).join("") : `<div class="text-sm text-slate-500">No run log yet.</div>`}
    </div>
    <div class="border-t border-white/5 pt-4">
      <div class="text-xs text-slate-400 font-semibold uppercase tracking-wider mb-2">Error types <span class="text-slate-600 normal-case font-normal">(${range.from} → ${range.to})</span></div>
      <div class="flex flex-wrap gap-2">
        ${errTop.length ? errTop.map(([c, n]) => `<span class="badge type">${esc(c)} · ${n}</span>`).join("") : `<span class="text-xs text-slate-500">No errors recorded</span>`}
      </div>
      <p class="text-xs text-slate-600 mt-3">HTTP 429 = rate-limited (free tier) · HTTP 400/404 = model config/retired · network = empty/truncated response. Quarantined models are excluded from picks but remain listed here.</p>
    </div>`;
}

/* ---- date range ---- */
let range = { from: null, to: null }; // inclusive ISO dates

function setupRange() {
  const dates = ITEMS.map((i) => i.date).sort();
  const min = dates[0], max = dates[dates.length - 1];
  const df = document.getElementById("date-from"), dt = document.getElementById("date-to");
  df.min = min; df.max = max; dt.min = min; dt.max = max;
  // default: last 7 days (the most useful view)
  const d = new Date(max + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - 6);
  df.value = d.toISOString().slice(0, 10);
  dt.value = max;
  document.getElementById("presets").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.days === "7"));
  document.getElementById("presets").querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      document.getElementById("presets").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const days = parseInt(b.dataset.days, 10);
      if (days === 0) { df.value = min; dt.value = max; }
      else {
        const d2 = new Date(max + "T00:00:00Z");
        d2.setUTCDate(d2.getUTCDate() - (days - 1));
        df.value = d2.toISOString().slice(0, 10);
        dt.value = max;
      }
      applyRange();
    });
  });
  df.addEventListener("change", () => { document.getElementById("presets").querySelectorAll("button").forEach((x) => x.classList.remove("active")); applyRange(); });
  dt.addEventListener("change", () => { document.getElementById("presets").querySelectorAll("button").forEach((x) => x.classList.remove("active")); applyRange(); });
  document.getElementById("clear-range").addEventListener("click", () => {
    document.getElementById("presets").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.days === "0"));
    df.value = min; dt.value = max; applyRange();
  });
}
function applyRange() {
  range = { from: document.getElementById("date-from").value, to: document.getElementById("date-to").value };
  render();
}
function inRange(d) { return (!range.from || d >= range.from) && (!range.to || d <= range.to); }

/* ---- render ---- */
function filtered() {
  return ITEMS.filter((i) => inRange(i.date)).sort((a, b) => a.date.localeCompare(b.date));
}

function render() {
  const items = filtered();
  const meta = document.getElementById("report-meta");
  const allFrom = ITEMS.length ? ITEMS[ITEMS.length - 1].date : "—";
  const allTo = ITEMS.length ? ITEMS[0].date : "—";
  meta.textContent = `${items.length} briefings in range (${range.from || "start"} → ${range.to || "now"}) · dataset ${allFrom} → ${allTo}`;
  document.getElementById("range-summary").textContent =
    items.length ? `${items.length} briefings · ${new Set(items.map((i) => i.date)).size} days` : "no data in range";
  renderKPIs(items);
  renderCharts(items);
  renderModelTable(items);
  renderDailyLog(items);
  if (RUN_STATS) renderRunHealth();  // re-filter run health to the new range
}

function countBy(items, keyFn) {
  const o = {};
  items.forEach((i) => { const k = keyFn(i); o[k] = (o[k] || 0) + 1; });
  return o;
}
function dateSeq(from, to) {
  const out = []; const d = new Date(from + "T00:00:00Z"); const end = new Date(to + "T00:00:00Z");
  while (d <= end) { out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 1); }
  return out;
}

function renderKPIs(items) {
  if (!items.length) { document.getElementById("kpis").innerHTML = ""; return; }
  const days = new Set(items.map((i) => i.date)).size;
  const words = items.reduce((s, i) => s + (i.word_count || 0), 0);
  const models = new Set(items.map((i) => i.provider + "/" + i.model).filter(Boolean)).size;
  const gemini = items.filter((i) => i.verify === "gemini").length;
  const perDay = countBy(items, (i) => i.date);
  const busiest = Object.entries(perDay).sort((a, b) => b[1] - a[1])[0];
  const kpis = [
    { n: items.length, l: "Briefings" },
    { n: days, l: "Days" },
    { n: Math.round(items.length / Math.max(1, days)), l: "Avg / day" },
    { n: busiest ? `${busiest[1]}` : "—", l: `Peak day ${busiest ? busiest[0].slice(5) : ""}` },
    { n: models, l: "Models used" },
    { n: gemini, l: "🤖 AI verified" },
  ];
  document.getElementById("kpis").innerHTML = kpis.map((k) =>
    `<div class="stat-card"><div class="num">${k.n}</div><div class="lbl">${k.l}</div></div>`).join("");
  void words;
}

function renderCharts(items) {
  Object.values(charts).forEach((c) => c && c.destroy());
  charts = {};
  const dates = items.length ? dateSeq(items[0].date, items[items.length - 1].date) : [];
  const byDay = countBy(items, (i) => i.date);

  // 1. per-day line
  charts.day = new Chart(ctx("chart-day"), {
    type: "line",
    data: { labels: dates, datasets: [{ data: dates.map((d) => byDay[d] || 0), borderColor: "#818cf8", backgroundColor: "rgba(129,140,248,.15)", fill: true, tension: .35, pointRadius: 3 }] },
    options: baseOpts({ legend: false }),
  });

  // 2. per-day stacked by provider
  const provs = [...new Set(items.map((i) => i.provider).filter(Boolean))];
  const provColor = {};
  provs.forEach((p, idx) => { provColor[p] = PALETTE[idx % PALETTE.length]; });
  charts.dayProvider = new Chart(ctx("chart-day-provider"), {
    type: "bar",
    data: {
      labels: dates,
      datasets: provs.map((p) => ({
        label: p, data: dates.map((d) => items.filter((i) => i.date === d && i.provider === p).length),
        backgroundColor: provColor[p], stack: "s",
      })),
    },
    options: baseOpts({ legend: true, stacked: true }),
  });

  // 3. collection
  const coll = countBy(items, (i) => i.collection);
  const collE = Object.entries(coll).sort((a, b) => b[1] - a[1]);
  charts.collection = new Chart(ctx("chart-collection"), {
    type: "bar",
    data: { labels: collE.map(([k]) => prettyLabel(k)), datasets: [{ data: collE.map(([, v]) => v), backgroundColor: PALETTE, borderRadius: 5 }] },
    options: baseOpts({ legend: false }),
  });

  // 4. verify doughnut
  const ver = countBy(items, (i) => i.verify);
  charts.verify = new Chart(ctx("chart-verify"), {
    type: "doughnut",
    data: { labels: Object.keys(ver).map((k) => (VERIFY_META[k] || { label: k }).label), datasets: [{ data: Object.values(ver), backgroundColor: Object.keys(ver).map((k) => (VERIFY_META[k] || { color: "#64748b" }).color), borderColor: "#0b1020", borderWidth: 2 }] },
    options: baseOpts({ legend: true }),
  });
}

function renderModelTable(items) {
  const byModel = {};
  items.forEach((i) => {
    const k = `${i.provider}/${i.model}`;
    if (!k.includes("/undefined") && !k.endsWith("/")) byModel[k] = (byModel[k] || 0) + 1;
  });
  const rows = Object.entries(byModel).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const max = rows.length ? rows[0][1] : 1;
  document.getElementById("model-table").innerHTML = rows.length
    ? rows.map(([k, v]) => `
        <div class="flex items-center gap-3 text-sm">
          <span class="w-64 truncate text-slate-300 shrink-0" title="${esc(k)}">${esc(k)}</span>
          <div class="flex-1 h-2 rounded-full bg-white/5 overflow-hidden">
            <div class="h-full rounded-full" style="width:${(v / max) * 100}%;background:linear-gradient(90deg,#6366f1,#8b5cf6)"></div>
          </div>
          <span class="w-8 text-right text-slate-400 font-semibold">${v}</span>
        </div>`).join("")
    : `<div class="text-sm text-slate-500">No models in this range.</div>`;
}

function renderDailyLog(items) {
  const byDay = {};
  items.forEach((i) => { (byDay[i.date] = byDay[i.date] || []).push(i); });
  const days = Object.keys(byDay).sort().reverse();
  const el = document.getElementById("daily-log");
  if (!days.length) { el.innerHTML = `<div class="py-3 text-sm text-slate-500">No days in this range.</div>`; return; }
  el.innerHTML = days.map((d) => {
    const list = byDay[d];
    const colls = Object.entries(countBy(list, (i) => i.collection)).sort((a, b) => b[1] - a[1]);
    const provs = [...new Set(list.map((i) => i.provider).filter(Boolean))];
    return `
      <div class="py-3 grid grid-cols-1 sm:grid-cols-[110px_1fr] gap-1 sm:gap-4 items-start">
        <div>
          <a href="./library.html?d=${esc(d)}" class="text-slate-100 font-semibold text-sm hover:text-indigo-300 hover:underline">${esc(d)}</a>
          <div class="text-xs text-slate-500">${list.length} briefings</div>
        </div>
        <div class="min-w-0">
          <div class="flex flex-wrap gap-1.5 mb-1">
            ${colls.map(([c, n]) => `<span class="badge ${badgeCls(c)}">${esc(prettyLabel(c))} ${n}</span>`).join("")}
            ${provs.map((p) => `<span class="badge type">${esc(p)}</span>`).join("")}
          </div>
          <div class="text-xs text-slate-500 truncate">
            ${list.slice(0, 2).map((i) => esc(i.title)).join(" · ")}${list.length > 2 ? ` · +${list.length - 2} more` : ""}
          </div>
        </div>
      </div>`;
  }).join("");
}
function badgeCls(c) {
  return { "world-news": "collection-w", "tech-news": "collection-t", politics: "collection-p", products: "collection-pr" }[c] || "collection-default";
}

/* ---- chart helpers ---- */
function ctx(id) { const el = document.getElementById(id); return el ? el.getContext("2d") : null; }
function baseOpts({ legend = false, stacked = false } = {}) {
  return {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: legend, labels: { color: "#cbd5e1", boxWidth: 10, font: { size: 10 } } } },
    scales: {
      x: { stacked, grid: { color: "rgba(148,163,184,.1)" }, ticks: { color: "#94a3b8", font: { size: 10 }, maxRotation: 60, maxTicksLimit: 20 } },
      y: { stacked, grid: { color: "rgba(148,163,184,.1)" }, ticks: { color: "#94a3b8", font: { size: 10 }, precision: 0 } },
    },
  };
}

document.addEventListener("DOMContentLoaded", init);
