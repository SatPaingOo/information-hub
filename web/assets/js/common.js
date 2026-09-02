/* Information Hub viewer — shared helpers. */
"use strict";

/* Data lives at the REPO ROOT (`data/...`), one level above this web/
   folder. GitHub Pages publishes the whole repo from `/`, so `../data`
   resolves correctly both locally (http.server) and on Pages. */
const DATA_DIR = "../data";

async function fetchJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load ${path} (${res.status})`);
  return res.json();
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function queryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function articleHref(item) {
  // item.file is like "data-set/2026-09-02-001-....json" (relative to data/)
  const file = (item && (item.file || (item.key ? `${item.key}-...` : null))) || "";
  return `article.html?file=${encodeURIComponent(file)}`;
}

function formatDate(d) {
  if (!d) return "";
  const [y, m, day] = String(d).split("-");
  if (!y || !m || !day) return String(d);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${day} ${months[parseInt(m, 10) - 1]} ${y}`;
}

function wordCount(w) {
  return w ? `${w.toLocaleString()} words` : "";
}

/* (landing.js manages its own .reveal fade-in) */
