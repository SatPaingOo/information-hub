/* Information Hub — dataset catalog page: KPIs, schema, versions, quality. */
"use strict";

const SCHEMA_FIELDS = [
  ["id / key", "stable identifiers (topic:region:date-NNN)"],
  ["date / collection", "publish day + editorial collection"],
  ["content_type", "digest · briefing · article · analysis"],
  ["topic / region / categories", "taxonomy classification"],
  ["title / tldr", "headline + 2-3 sentence summary"],
  ["background / analysis[]", "deep context + headed analysis sections"],
  ["key_facts[] / implications[]", "structured facts + consequences"],
  ["outlook", "forward-looking assessment"],
  ["entities[]", "typed entities (person/org/product/…)"],
  ["related_items[] / related_taxonomy[]", "cross-links for graph / GraphRAG"],
  ["source{name,url,feed}", "original article + originating feed"],
  ["provenance.generated_by", "provider / model / prompt version"],
  ["grounding / review", "verification method, score, claims, status"],
  ["word_count", "schema-enforced ≥ 500 words"],
];

async function init() {
  try {
    const s = await fetchJSON(`${DATA_DIR}/views/stats.json`);
    renderKpis(s);
    renderSchema();
    renderVersions();
  } catch (e) {
    document.getElementById("kpis").innerHTML =
      `<div class="text-red-400 text-sm">Could not load dataset stats: ${esc(e.message)}</div>`;
  }
}

/* Free sample: fetch the 3 newest full records, pack with license metadata,
   and download as one JSON — an evaluation copy for prospective buyers.
   Built in-browser from the live data (no backend, no pipeline change). */
async function downloadSample() {
  const btn = document.getElementById("sample-dl");
  btn.textContent = "⏳ building sample…";
  try {
    const index = await fetchJSON(`${DATA_DIR}/views/index.json`);
    const items = (Array.isArray(index) ? index : (index.items || []));
    const newest = items.slice(0, 3);
    const records = [];
    for (const it of newest) {
      // file is "collections/data-set/<name>.json" — dataset.html lives in
      // web/, so the record is one level up under ../data/.
      const path = "../data/" + (it.file || "").replace(/^\/+/, "");
      try {
        records.push(await fetchJSON(path));
      } catch { /* skip a failed fetch rather than abort the sample */ }
    }
    if (!records.length) throw new Error("no sample records could be fetched");
    const payload = {
      sample: true,
      description: "Information Hub — machine-written intelligence briefings. This is a 3-record evaluation sample of a dataset that grows daily.",
      license: "CC BY-NC 4.0 — non-commercial use with attribution. Commercial use requires a separate license: satpaingoo777@gmail.com",
      catalog: "https://satpaingoo.github.io/information-hub/web/dataset.html",
      generated_at: new Date().toISOString(),
      records,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "information-hub-sample.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    btn.textContent = "✅ downloaded";
  } catch (e) {
    btn.textContent = "⚠️ " + e.message;
  }
  setTimeout(() => { btn.textContent = "⬇️ Free sample · 3 records"; }, 3000);
}

function renderKpis(s) {
  const items = s.total_items || 0;
  const verify = s.per_verify_method || {};
  const gemini = verify.gemini || 0;
  const lexical = verify.lexical || 0;
  const web = verify.web || 0;
  const aiPct = items ? Math.round((gemini / items) * 100) : 0;
  const first = s.first_date || "—";
  const last = s.last_date || "—";
  const kpis = [
    { icon: "🗂️", n: items, l: "Records", sub: `${first} → ${last}`, c: "#818cf8" },
    { icon: "📚", n: Object.keys(s.per_collection || {}).length, l: "Collections", sub: "world · tech · politics · products", c: "#38bdf8" },
    { icon: "✍️", n: (s.total_words || 0).toLocaleString(), l: "Words written", sub: "schema-enforced deep-dives", c: "#34d399" },
    { icon: aiPct > 0 ? "🤖" : web > 0 ? "🌐" : "⚙️", n: aiPct > 0 ? `${aiPct}%` : "—", l: "AI-verified", sub: aiPct > 0 ? `Gemini · ${gemini} records` : web > 0 ? `Web corroborated · ${web} · ${lexical} lexical` : `Gemini search busy · ${lexical} lexical`, c: aiPct > 0 ? "#34d399" : web > 0 ? "#38bdf8" : "#fbbf24" },
  ];
  document.getElementById("kpis").innerHTML = kpis.map((k) => `
    <div class="dstat" style="--accent:${k.c}">
      <div class="dstat-icon">${k.icon}</div>
      <div class="dstat-num">${k.n}</div>
      <div class="dstat-lbl">${k.l}</div>
      <div class="dstat-sub">${esc(k.sub)}</div>
    </div>`).join("");
}

function renderSchema() {
  document.getElementById("schema").innerHTML = SCHEMA_FIELDS.map(([k, v]) => `
    <div class="rounded-lg bg-white/[.03] border border-white/8 p-3">
      <div class="text-indigo-300 font-semibold mb-0.5 font-mono">${esc(k)}</div>
      <div class="text-slate-500 leading-snug">${esc(v)}</div>
    </div>`).join("");
}

async function renderVersions() {
  const el = document.getElementById("versions");
  try {
    // tags are not exposed to Pages — list weekly data tags from git via API is
    // not possible client-side; show the latest commit date instead.
    const s = await fetchJSON(`${DATA_DIR}/views/stats.json`);
    const last = s.last_date;
    const first = s.first_date;
    el.innerHTML = `
      <div class="flex items-center gap-2"><span class="text-emerald-300 font-semibold">●</span>
        <span>Dataset covers <span class="text-white font-semibold">${esc(first)} → ${esc(last)}</span> · regenerated daily at 01:00 UTC</span>
      </div>
      <div class="flex items-center gap-2 mt-2"><span class="text-indigo-300 font-semibold">▸</span>
        <span>Weekly snapshots are tagged <code class="text-indigo-300">data-YYYY-MM-DD</code> in the
        <a class="underline hover:text-white" href="https://github.com/SatPaingOo/information-hub/tags" target="_blank" rel="noopener">repository tags</a></span>
      </div>`;
  } catch (e) {
    el.textContent = "Version info unavailable.";
  }
}

document.addEventListener("DOMContentLoaded", init);
