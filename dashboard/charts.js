/*
 * charts.js — renders the reporting dashboard from the sync output.
 *
 * Reads data/items.json (produced by `python -m src.sync`) and falls back to
 * data/items.example.json, which is the only data file committed to the public
 * repo. Each candidate path is tried in order so the page works whether it is
 * opened from the repo root or with dashboard/ as the site root.
 *
 * Three views, all driven by the flat records in `items`:
 *   - submission volume over time  (toggle: date added vs. date published)
 *   - item-type breakdown
 *   - department breakdown
 */

"use strict";

const DATA_CANDIDATES = [
  "../data/items.json",
  "../data/items.example.json",
  "data/items.json",
  "data/items.example.json",
];

// Colorblind-friendly categorical palette (Tableau 10).
const PALETTE = [
  "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
];

let timeChart = null; // kept so the toggle can destroy + redraw it

document.addEventListener("DOMContentLoaded", init);

async function init() {
  let payload;
  try {
    payload = await loadData();
  } catch (err) {
    showError(
      "Could not load a data file. Run `python -m src.sync` to generate " +
      "data/items.json, or serve the repo over HTTP (browsers block " +
      "file:// fetches). Tried: " + DATA_CANDIDATES.join(", ")
    );
    return;
  }

  const items = Array.isArray(payload.data.items) ? payload.data.items : [];
  renderMeta(payload.data, items, payload.url);

  if (items.length === 0) {
    showError("The data file loaded but contains no items.");
    return;
  }

  document.getElementById("controls").hidden = false;
  document.getElementById("charts").hidden = false;

  renderTypeChart(items);
  renderDeptChart(items);

  const select = document.getElementById("date-view");
  const draw = () => renderTimeChart(items, select.value);
  select.addEventListener("change", draw);
  draw();
}

/* ---------- data loading ---------- */

async function loadData() {
  let lastErr;
  for (const url of DATA_CANDIDATES) {
    try {
      const resp = await fetch(url, { cache: "no-store" });
      if (resp.ok) return { data: await resp.json(), url };
      lastErr = new Error(`${resp.status} for ${url}`);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("no data file found");
}

/* ---------- header ---------- */

function renderMeta(payload, items, dataUrl) {
  const source = (payload.source || "unknown source").replace(/^https?:\/\//, "");
  const generated = payload.generated_at || "unknown";
  document.getElementById("meta").textContent =
    `${items.length} items · ${source} · generated ${generated}`;

  // The badge must always tell the truth about which file was loaded. The
  // committed sample is the only data file present in the public repo, so a
  // deployed copy always reads it; a local `python -m src.sync` run produces
  // data/items.json, which then wins the candidate order.
  const isSample = /(^|\/)items\.example\.json(\?.*)?$/.test(dataUrl);
  const badge = document.getElementById("data-badge");
  badge.textContent = isSample ? "Sample data" : "Live data · local sync output";
  badge.classList.add(isSample ? "badge--sample" : "badge--live");
  badge.title = `loaded from ${dataUrl}`;
}

function showError(message) {
  const el = document.getElementById("error");
  el.textContent = message;
  el.hidden = false;
  document.getElementById("meta").textContent = "";
  document.getElementById("data-badge").hidden = true;
}

/* ---------- bucketing helpers ---------- */

function countBy(items, key) {
  const counts = new Map();
  for (const it of items) {
    const raw = it[key];
    const label = raw === null || raw === undefined || raw === "" ? "(none)" : String(raw);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  // Sort by count descending for readable bar charts.
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

// year buckets for published dates (padding makes finer buckets misleading)
function bucketByYear(items, dateKey) {
  const counts = new Map();
  let missing = 0;
  for (const it of items) {
    const v = it[dateKey];
    if (!v) { missing += 1; continue; }
    const year = String(v).slice(0, 4);
    counts.set(year, (counts.get(year) || 0) + 1);
  }
  return { labels: fillYearGaps([...counts.keys()]), counts, missing };
}

// month buckets for the accession date, which is always a full timestamp
function bucketByMonth(items, dateKey) {
  const counts = new Map();
  let missing = 0;
  for (const it of items) {
    const v = it[dateKey];
    if (!v) { missing += 1; continue; }
    const ym = String(v).slice(0, 7); // YYYY-MM
    counts.set(ym, (counts.get(ym) || 0) + 1);
  }
  return { labels: fillMonthGaps([...counts.keys()]), counts, missing };
}

function fillYearGaps(years) {
  if (years.length === 0) return [];
  const nums = years.map(Number).sort((a, b) => a - b);
  const out = [];
  for (let y = nums[0]; y <= nums[nums.length - 1]; y += 1) out.push(String(y));
  return out;
}

function fillMonthGaps(months) {
  if (months.length === 0) return [];
  const sorted = [...months].sort();
  const out = [];
  let [y, m] = sorted[0].split("-").map(Number);
  const [ey, em] = sorted[sorted.length - 1].split("-").map(Number);
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return out;
}

/* ---------- charts ---------- */

function renderTimeChart(items, mode) {
  const isIssued = mode === "issued";
  const dateKey = isIssued ? "date_issued" : "date_accessioned";
  const { labels, counts, missing } = isIssued
    ? bucketByYear(items, dateKey)
    : bucketByMonth(items, dateKey);

  const note = document.getElementById("date-view-note");
  const grain = isIssued ? "yearly" : "monthly";
  note.textContent = missing
    ? `${grain} · ${missing} item(s) without this date are excluded`
    : `${grain}`;

  const data = labels.map((l) => counts.get(l) || 0);

  if (timeChart) timeChart.destroy();
  timeChart = new Chart(document.getElementById("chart-time"), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: isIssued ? "Items published" : "Items added",
        data,
        backgroundColor: PALETTE[0],
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      plugins: { legend: { display: false } },
    },
  });
}

function renderTypeChart(items) {
  barChart("chart-type", countBy(items, "type"), "Items");
}

function renderDeptChart(items) {
  barChart("chart-dept", countBy(items, "department"), "Items");
}

// Shared horizontal bar chart for the categorical breakdowns.
function barChart(canvasId, entries, label) {
  const labels = entries.map((e) => e[0]);
  const data = entries.map((e) => e[1]);
  new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label,
        data,
        backgroundColor: labels.map((_, i) => PALETTE[i % PALETTE.length]),
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
      plugins: { legend: { display: false } },
    },
  });
}
