"use strict";

const $ = (id) => document.getElementById(id);
const fmtUsd = (v, d = 0) =>
  v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: d });

let currentFilter = "deal";
let historyChart, verdictChart;
let allListings = [];
let currentScanId = null;   // null = latest scan; set to view a historical scan

// Genuine free = $0, not a trade/sale/mislist/dealer-ad, not broken/sold, AND — when the LLM
// actually read the description — it confirmed a real giveaway (detail_checked but not
// genuinely_free means the LLM looked and it was NOT specifically free → exclude).
const isGenuineFree = (r) =>
  r.price_usd === 0 && !r.false_free && !r.for_parts && !r.sold && !r.is_advertisement &&
  !(r.detail_checked && !r.genuinely_free);

// Comp-confidence color for the (n) sample-size chip: green=solid, amber=thin, red=very thin.
const confColor = (c) => (c == null ? "text-slate-500" : c >= 0.66 ? "text-emerald-400" : c >= 0.33 ? "text-amber-400" : "text-rose-400");

const scanParam = () => (currentScanId ? "?scan_id=" + currentScanId : "");
const withScan = (url) => url + (currentScanId ? (url.includes("?") ? "&" : "?") + "scan_id=" + currentScanId : "");

// Human labels for verdicts — "skip" reads as an error to an exec; it means "fairly priced".
const VERDICT = {
  deal: ["✅ Deal", "bg-emerald-500/15 text-emerald-300"],
  review: ["⚠️ Review", "bg-amber-500/15 text-amber-300"],
  "low-confidence": ["Low conf.", "bg-slate-500/15 text-slate-400"],
  skip: ["Fair price", "bg-slate-500/10 text-slate-500"],
};
const SEVERITY = {
  high: ["High risk", "bg-rose-500/20 text-rose-300"],
  medium: ["Wear", "bg-amber-500/20 text-amber-300"],
  low: ["Minor", "bg-lime-500/15 text-lime-300"],
  none: ["Clean", "bg-emerald-500/15 text-emerald-300"],
};

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

// ---------- KPI cards ----------
function kpiCard(label, value, accent, sub) {
  const c = { emerald: "text-emerald-400", amber: "text-amber-400", sky: "text-sky-400", indigo: "text-indigo-300", slate: "text-slate-200" }[accent];
  return `<div class="kpi glass rounded-2xl p-5">
      <div class="text-[11px] uppercase tracking-wider text-slate-400">${label}</div>
      <div class="mt-1 text-2xl font-bold display ${c}">${value}</div>
      <div class="text-[11px] text-slate-500 mt-0.5">${sub || ""}</div>
    </div>`;
}

async function loadScans() {
  const scans = await getJSON("/api/scans");
  const sel = $("scan-select");
  sel.innerHTML = scans
    .map((s) => `<option value="${s.id}">${s.is_latest ? "● Latest" : "◷"} ${(s.ts || "").replace("T", " ")} — ${s.deals_count}✅/${s.review_count}⚠️</option>`)
    .join("");
  if (currentScanId) sel.value = String(currentScanId);
}

async function loadSummary() {
  const s = await getJSON(withScan("/api/summary"));
  if (!s.has_data) { $("empty").classList.remove("hidden"); $("kpis").innerHTML = ""; return false; }
  $("empty").classList.add("hidden");
  $("hist-indicator").classList.toggle("hidden", !!s.is_latest);
  $("last-scan").textContent = (s.is_latest ? "Last scan: " : "Scan: ") + (s.last_scan_ts || "—").replace("T", " ");
  // FB Marketplace vs eBay comparison band.
  if (s.fb_avg_ask != null && s.ebay_avg_sold != null) {
    $("fb-ebay").classList.remove("hidden");
    $("cmp-fb").textContent = fmtUsd(s.fb_avg_ask);
    $("cmp-ebay").textContent = fmtUsd(s.ebay_avg_sold);
    $("cmp-disc").textContent = (s.fb_vs_ebay_discount_pct != null ? s.fb_vs_ebay_discount_pct + "%" : "—");
  } else {
    $("fb-ebay").classList.add("hidden");
  }
  const freeFinds = allListings.filter(isGenuineFree).length;
  $("kpis").innerHTML = [
    kpiCard("Deals found", s.deals_count, "emerald", "buy-worthy margin"),
    kpiCard("Potential profit", fmtUsd(s.total_potential_profit), "emerald", "sum of est. profit"),
    kpiCard("Needs review", s.review_count, "amber", "too-good / defects"),
    kpiCard("Free finds", freeFinds, "sky", "$0 listings surfaced"),
    kpiCard("Listings scanned", s.scanned_count, "indigo", `${s.watchlist_size} watchlist items`),
    kpiCard("Scans on record", s.total_scans, "slate", "price history retained"),
  ].join("");
  return true;
}

// ---------- Charts ----------
function darkAxis() {
  return { axisLine: { lineStyle: { color: "#334155" } }, axisLabel: { color: "#94a3b8", fontSize: 10 }, splitLine: { lineStyle: { color: "rgba(255,255,255,.05)" } } };
}

function loadVerdictChart() {
  const counts = {};
  allListings.forEach((r) => (counts[r.verdict] = (counts[r.verdict] || 0) + 1));
  const palette = { deal: "#34d399", review: "#fbbf24", "low-confidence": "#64748b", skip: "#475569" };
  const data = Object.entries(counts).map(([k, v]) => ({ name: (VERDICT[k] || [k])[0].replace(/[^\w ]/g, "").trim(), value: v, itemStyle: { color: palette[k] || "#475569" } }));
  verdictChart = verdictChart || echarts.init($("verdict-chart"), null, { renderer: "canvas" });
  verdictChart.setOption({
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: "#94a3b8", fontSize: 11 } },
    series: [{ type: "pie", radius: ["48%", "72%"], center: ["50%", "44%"], label: { color: "#cbd5e1", formatter: "{b}\n{c}", fontSize: 11 }, data }],
  });
}

const CAT_COLORS = ["#34d399", "#818cf8", "#fbbf24", "#f472b6", "#22d3ee", "#a3e635", "#fb923c", "#c084fc", "#2dd4bf", "#f87171", "#60a5fa", "#facc15"];

async function loadProfitCategories() {
  const cats = await getJSON("/api/profit/categories");
  const sel = $("product-select");
  // "All categories" is the catch-all (default).
  const opts = ['<option value="__all__">★ All categories</option>'];
  for (const c of cats) {
    opts.push(`<option value="${encodeURIComponent(c.canonical_key)}">${c.canonical_name || c.canonical_key} · best $${Math.round(c.best_profit)}</option>`);
  }
  sel.innerHTML = opts.join("");
  loadProfit("__all__");
}

async function loadProfit(key) {
  const rows = await getJSON("/api/profit?key=" + encodeURIComponent(key));
  historyChart = historyChart || echarts.init($("history-chart"));
  if (!rows.length) {
    historyChart.clear();
    historyChart.setOption({ graphic: [{ type: "text", left: "center", top: "center", style: { text: "No expected-profit data yet — run a scan", fill: "#64748b", fontSize: 13 } }] });
    return;
  }
  const single = key !== "__all__";

  // Single scatter series, one color (no per-category color coding) — each bubble is an
  // opportunity, clickable to open the listing.
  const series = [{
    name: "opportunities",
    type: "scatter",
    symbolSize: (v) => Math.max(10, Math.min(36, 9 + Math.sqrt(Math.max(0, v[1])))),
    itemStyle: { color: "#34d399", opacity: 0.7 },
    emphasis: { scale: 1.3 },
    cursor: "pointer",
    data: rows.map((r) => ({
      value: [r.ts.replace("T", " "), Math.round(r.est_profit)],
      name: r.canonical_name, url: r.url, price: r.price_usd, median: r.ebay_median, verdict: r.verdict,
    })),
  }];

  // ONE trend line: average expected profit per scan over time. Straight segments (not
  // smoothed) so the line never overshoots into an apparent second line.
  const byTs = {};
  rows.forEach((r) => { const t = r.ts.replace("T", " "); (byTs[t] = byTs[t] || []).push(r.est_profit); });
  const trend = Object.keys(byTs).sort().map((t) => [t, Math.round(byTs[t].reduce((a, b) => a + b, 0) / byTs[t].length)]);
  series.push({
    name: "avg expected profit (trend)", type: "line", data: trend, smooth: false,
    symbol: "circle", symbolSize: 7, z: 10,
    lineStyle: { color: "#f8fafc", width: 3 }, itemStyle: { color: "#f8fafc" },
  });

  historyChart.setOption(
    {
      tooltip: {
        trigger: "item", backgroundColor: "rgba(15,23,42,.95)", borderColor: "#334155", textStyle: { color: "#e2e8f0" },
        formatter: (p) => {
          const d = p.data || {};
          return `<b>${d.name || ""}</b><br/>Expected profit: <b style="color:#34d399">$${Number(p.value[1]).toLocaleString()}</b>`
            + `<br/>Asking: ${d.price === 0 ? "Free" : "$" + Math.round(d.price).toLocaleString()} · median ${d.median ? "$" + Math.round(d.median).toLocaleString() : "—"}`
            + `<br/><span style="color:#94a3b8">${p.value[0]} · ${d.verdict || ""}</span>`
            + (d.url ? `<br/><span style="color:#818cf8">🔗 click to open</span>` : "");
        },
      },
      legend: { data: ["avg expected profit (trend)"], bottom: 0, textStyle: { color: "#94a3b8", fontSize: 11 } },
      grid: { left: 78, right: 20, top: 30, bottom: 40, containLabel: false },
      xAxis: { type: "category", ...darkAxis() },
      yAxis: { type: "value", name: "price ($)", min: 0, nameGap: 16, nameTextStyle: { color: "#64748b", align: "left" }, ...darkAxis(), axisLabel: { color: "#94a3b8", formatter: (v) => "$" + v.toLocaleString() } },
      graphic: [],
      series,
    },
    { replaceMerge: ["series", "legend", "graphic"] }
  );
  // Click a bubble -> open that listing.
  historyChart.off("click");
  historyChart.on("click", (p) => {
    if (p.data && p.data.url) window.open(p.data.url, "_blank");
  });
}

// ---------- Table ----------
function defectCell(r) {
  const tag = (cls, text, title = "") => `<span class="px-1.5 py-0.5 rounded ${cls} text-[10px] font-semibold" title="${title}">${text}</span>`;
  const badges = [];
  if (r.sold) badges.push(tag("bg-rose-600/40 text-rose-100", "SOLD", r.availability || "sold"));
  if (r.is_advertisement) badges.push(tag("bg-rose-600/30 text-rose-200", "dealer ad", "storefront / solicitation post"));
  if (r.price_dropped_to_zero) badges.push(tag("bg-amber-500/25 text-amber-200", "was priced → $0", "this listing had a price in a prior scan, now $0"));
  if (r.false_free) badges.push(tag("bg-rose-600/30 text-rose-200", `not really free (${r.listing_intent || "?"})`, "the $0 price isn't genuine"));
  else if (r.genuinely_free) badges.push(tag("bg-emerald-500/15 text-emerald-300", "✓ genuine free", "confirmed giveaway"));
  if (r.price_in_description) badges.push(tag("bg-slate-500/20 text-slate-200", `listed $${Math.round(r.price_in_description)} in text`, "real price found in the description"));
  if (r.is_bundle) badges.push(tag("bg-violet-500/20 text-violet-200", "bundle", "includes extra items — single-item comp may understate resale"));

  let sev = "";
  if (r.detail_checked) {
    const [label, cls] = SEVERITY[r.defect_severity] || SEVERITY.none;
    const summary = (r.defect_summary || "").replace(/"/g, "&quot;");
    sev = `<span class="px-2 py-0.5 rounded-full text-xs ${cls}" title="${summary}">${label}</span>`;
    try {
      const d = r.defects_json ? JSON.parse(r.defects_json) : null;
      const flags = (d && d.risk_flags) || [];
      if (flags.length) sev += `<div class="mt-0.5 flex flex-wrap gap-1">${flags.slice(0, 3).map((f) => `<span class="px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 text-[10px]">${f}</span>`).join("")}</div>`;
    } catch (e) {}
  }
  if (!badges.length && !sev) return `<span class="text-slate-600 text-xs">—</span>`;
  return `<div class="flex flex-wrap items-center gap-1">${badges.join("")}${sev}</div>`;
}

function render(rows) {
  const body = $("deals-body");
  $("table-empty").classList.toggle("hidden", rows.length > 0);
  body.innerHTML = rows.map((r) => {
    const [vlabel, vcls] = VERDICT[r.verdict] || [r.verdict, "bg-slate-500/10 text-slate-400"];
    const title = (r.title || "").slice(0, 64);
    const titleCell = r.url ? `<a href="${r.url}" target="_blank" class="text-indigo-300 hover:text-indigo-200 hover:underline">${title}</a>` : title;
    const sub = r.canonical_name && r.canonical_name !== r.title
      ? `<div class="text-[11px] text-slate-500">${r.canonical_name}</div>` : "";
    const freeBadge = r.price_usd === 0 ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 text-[10px] font-semibold">FREE</span>` : "";
    const profitColor = r.est_profit > 0 ? "text-emerald-400" : "text-slate-500";
    return `<tr class="border-b border-white/5 hover:bg-white/5">
      <td class="px-5 py-2.5 font-semibold ${profitColor}">${r.est_profit == null ? "—" : fmtUsd(r.est_profit)}</td>
      <td class="px-3 py-2.5">${r.price_usd === 0 ? '<span class="text-sky-300 font-semibold">Free</span>' : fmtUsd(r.price_usd)}</td>
      <td class="px-3 py-2.5">${r.ebay_median ? fmtUsd(r.ebay_median) + ` <span class="${confColor(r.confidence)}" title="comp confidence ${r.confidence != null ? Math.round(r.confidence * 100) + "%" : "?"} · ${r.comp_method || ""}">(${r.ebay_count})</span>` : "—"}</td>
      <td class="px-3 py-2.5">${r.ratio == null ? "—" : r.ratio.toFixed(2)}</td>
      <td class="px-3 py-2.5">${defectCell(r)}</td>
      <td class="px-3 py-2.5">${titleCell}${freeBadge}${sub}</td>
      <td class="px-3 py-2.5 text-slate-400">${r.location || ""}</td>
      <td class="px-3 py-2.5"><span class="px-2 py-0.5 rounded-full text-xs ${vcls}">${vlabel}</span></td>
    </tr>`;
  }).join("");
}

function applyFilter() {
  let rows = allListings;
  if (currentFilter === "deal") rows = allListings.filter((r) => r.verdict === "deal");
  else if (currentFilter === "review") rows = allListings.filter((r) => r.verdict === "review");
  else if (currentFilter === "free") rows = allListings.filter(isGenuineFree);
  render(rows);
}

// ---------- Scan trigger ----------
async function triggerScan() {
  currentScanId = null;   // a new scan -> snap back to the live/latest view
  const btn = $("scan-btn");
  btn.disabled = true; $("scan-btn-label").textContent = "Scanning…";
  try { await fetch("/api/scan", { method: "POST" }); } catch (e) {}
  pollScan();
}
async function pollScan() {
  try {
    const st = await getJSON("/api/scan/status");
    if (st.running) { $("scan-btn").disabled = true; $("scan-btn-label").textContent = "Scanning…"; setTimeout(pollScan, 4000); }
    else { $("scan-btn").disabled = false; $("scan-btn-label").textContent = "Run scan"; await refresh(); }
  } catch (e) { $("scan-btn").disabled = false; $("scan-btn-label").textContent = "Run scan"; }
}

async function refresh() {
  await loadScans();
  allListings = await getJSON(withScan("/api/listings"));
  const has = await loadSummary();
  if (!has) return;
  applyFilter();
  loadVerdictChart();
  await loadProfitCategories();
}

// ---------- clear history (double verification) ----------
const CLEAR_PHRASE = "DELETE ALL HISTORY";

async function clearHistory() {
  // Verification 1: explicit intent confirmation.
  if (!confirm("Clear ALL scan history?\n\nThis permanently deletes every stored listing, scan, and price-history point. This CANNOT be undone."))
    return;
  // Verification 2: exact-phrase typed validation (case-sensitive).
  const typed = prompt(`Type the exact phrase to confirm permanent deletion:\n\n${CLEAR_PHRASE}`);
  if (typed === null) return;                       // user cancelled
  if (typed !== CLEAR_PHRASE) {
    alert(`Cancelled — the phrase did not match exactly.\nNothing was deleted.`);
    return;
  }
  const btn = $("clear-btn");
  btn.disabled = true;
  btn.textContent = "Clearing…";
  try {
    const r = await fetch("/api/clear?confirm=" + encodeURIComponent(CLEAR_PHRASE), { method: "POST" });
    const j = await r.json();
    if (j.cleared) {
      alert(`Cleared. Deleted ${j.deleted.listings} listings across ${j.deleted.scans} scans.`);
      location.reload();
    } else {
      alert("Clear failed: " + (j.reason || "unknown"));
    }
  } catch (e) {
    alert("Clear failed: " + e);
  } finally {
    btn.disabled = false;
    btn.textContent = "Clear all scan history";
  }
}

// ---------- wire up ----------
$("scan-btn").addEventListener("click", triggerScan);
$("clear-btn").addEventListener("click", clearHistory);
$("scan-select").addEventListener("change", (e) => {
  // Latest option is the first; selecting it returns to live view.
  const opts = e.target.options;
  currentScanId = e.target.selectedIndex === 0 ? null : Number(e.target.value);
  refresh();
});
$("product-select").addEventListener("change", (e) => loadProfit(decodeURIComponent(e.target.value)));
$("filter-tabs").addEventListener("click", (e) => {
  const f = e.target.getAttribute("data-f");
  if (!f) return;
  currentFilter = f;
  [...e.currentTarget.children].forEach((b) => b.classList.toggle("ring-2", b === e.target));
  applyFilter();
});
window.addEventListener("resize", () => { historyChart && historyChart.resize(); verdictChart && verdictChart.resize(); });

refresh();
getJSON("/api/scan/status").then((st) => { if (st.running) pollScan(); });
