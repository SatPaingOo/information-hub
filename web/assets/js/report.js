/* Information Hub — report page: pipeline charts from data/views/stats.json. */
"use strict";

const COLORS = {
  indigo: "#818cf8", violet: "#a78bfa", sky: "#38bdf8", emerald: "#34d399",
  amber: "#fbbf24", rose: "#fb7185", teal: "#2dd4bf",
};
const PALETTE = ["#818cf8", "#38bdf8", "#34d399", "#fbbf24", "#fb7185", "#a78bfa", "#2dd4bf", "#f472b6", "#60a5fa"];

function normalizeCollection(c) {
  const map = { world: "world-news", "ai-ml": "tech-news", geopolitics: "politics", "dev-oss": "tech-news", "world-news": "world-news", "tech-news": "tech-news", politics: "politics", products: "products" };
  return map[c] || c;
}
const COL_LABEL = { "world-news": "World News", "tech-news": "Tech & AI", politics: "Politics", products: "Products" };

async function init() {
  try {
    const s = await fetchJSON(`${DATA_DIR}/views/stats.json`);
    render(s);
  } catch (e) {
    document.body.insertAdjacentHTML("beforeend",
      `<div class="max-w-6xl mx-auto px-5 py-16 text-red-400">Could not load stats: ${esc(e.message)}</div>`);
  }
}

function render(s) {
  const total = s.total_items || 0;
  const days = s.days || [];
  document.getElementById("report-meta").textContent =
    `${total} briefings · ${days.length} days (${s.first_date} → ${s.last_date}) · ${(s.total_words || 0).toLocaleString()} words`;

  // KPIs
  const collCount = Object.keys(s.per_collection || {}).length;
  const provCount = Object.keys(s.per_provider_model || {}).length;
  const gemini = (s.per_verify_method || {}).gemini || 0;
  document.getElementById("kpis").innerHTML = [
    { n: total, l: "Total briefings" },
    { n: days.length, l: "Days covered" },
    { n: provCount, l: "Models used" },
    { n: gemini, l: "AI (Gemini) verified" },
  ].map((k) => `<div class="stat-card"><div class="num">${k.n}</div><div class="lbl">${k.l}</div></div>`).join("");

  // merge topic-only collections into canonical collections
  const merged = {};
  Object.entries(s.per_collection || {}).forEach(([k, v]) => {
    const c = normalizeCollection(k);
    merged[c] = (merged[c] || 0) + v;
  });

  lineChart("chart-day", s.per_day || {}, "briefings");
  barChart("chart-collection", merged, COL_LABEL);
  barChart("chart-provider", s.per_provider_model || {}, null, true);
  doughnutChart("chart-verify", s.per_verify_method || {});
}

function chartBase(kind) {
  const ctx = document.getElementById(kind);
  if (!ctx) return null;
  return ctx.getContext("2d");
}
function gridColor() { return "rgba(148,163,184,.12)"; }
function tickColor() { return "#94a3b8"; }

function lineChart(id, data, label) {
  const ctx = chartBase(id);
  if (!ctx) return;
  const dates = Object.keys(data).sort();
  const vals = dates.map((d) => data[d]);
  new Chart(ctx, {
    type: "line",
    data: { labels: dates, datasets: [{ label, data: vals, borderColor: COLORS.indigo, backgroundColor: "rgba(129,140,248,.15)", fill: true, tension: .35, pointRadius: 3, pointBackgroundColor: COLORS.indigo }] },
    options: baseOpts({ legend: false, ticksCallback: (v) => String(v).slice(5) }),
  });
}

function barChart(id, data, labelMap, rotate) {
  const ctx = chartBase(id);
  if (!ctx) return;
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const labels = entries.map(([k]) => labelMap ? (labelMap[normalizeCollection(k)] || k) : k);
  const vals = entries.map(([, v]) => v);
  const colors = entries.map((_, i) => PALETTE[i % PALETTE.length]);
  new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: vals, backgroundColor: colors, borderRadius: 5 }] },
    options: baseOpts({ legend: false, indexAxis: rotate ? "y" : "x" }),
  });
}

function doughnutChart(id, data) {
  const ctx = chartBase(id);
  if (!ctx) return;
  const labelMap = { gemini: "AI check (Gemini)", lexical: "Lexical check", unverified: "Unverified" };
  const colorMap = { gemini: COLORS.emerald, lexical: COLORS.sky, unverified: "#64748b" };
  const labels = Object.keys(data);
  new Chart(ctx, {
    type: "doughnut",
    data: { labels: labels.map((l) => labelMap[l] || l), datasets: [{ data: labels.map((l) => data[l]), backgroundColor: labels.map((l) => colorMap[l] || "#64748b"), borderColor: "#0b1020", borderWidth: 2 }] },
    options: baseOpts({ legend: true }),
  });
}

function baseOpts({ legend = false, ticksCallback = null, indexAxis = "x" }) {
  const o = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: legend, labels: { color: "#cbd5e1", boxWidth: 12 } } },
    scales: { x: { grid: { color: gridColor() }, ticks: { color: tickColor(), font: { size: 10 }, maxRotation: indexAxis === "y" ? 0 : 45 } }, y: { grid: { color: gridColor() }, ticks: { color: tickColor(), font: { size: 10 }, precision: 0 } } },
  };
  if (indexAxis === "y") o.scales.y.ticks.maxRotation = 0;
  if (ticksCallback) { o.scales.x.ticks.callback = ticksCallback; o.scales.y.ticks.precision = 0; }
  return o;
}

document.addEventListener("DOMContentLoaded", init);
