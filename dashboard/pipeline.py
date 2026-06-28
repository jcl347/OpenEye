"""
OpenEye scan pipeline (Claude out of the hot loop, in the loop only for normalization).

End to end, for each watchlist item:
  1. Scrape Facebook Marketplace (reuse the FB MCP's scraper.py CLI as a subprocess).
  2. Normalize every title with the LLM extractor (normalize.py) -> structured identity,
     clean eBay query, and is_part / is_wanted_ad flags.
  3. Fetch eBay SOLD comps once per canonical product (reuse the ebay-sold-comps CLI).
  4. Score each listing against its comp (scoring.py, the CLAUDE.md rules).
  5. Write reports/<ts>.json (+ .md) and ingest into data/openeye.db for the dashboard.

Run:  uv run python pipeline.py
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

import db
import defects
import normalize
import scoring

REPO_ROOT = Path(__file__).resolve().parent.parent
EBAY_DIR = REPO_ROOT / "mcp" / "ebay-sold-comps"
WATCHLIST = REPO_ROOT / "config" / "watchlist.yaml"
REPORTS_DIR = REPO_ROOT / "reports"

MAX_LISTINGS_PER_ITEM = 20          # per query (anonymous FB returns ~1 page either way)
MAX_FREE_PER_ITEM = 8               # extra slots for the FREE sweep (high-value free finds)
FREE_SWEEP_DAYS = 30               # widen recency for free items (rarer)
SCROLL_ROUNDS = 0                  # logged-out scroll is walled; 0 until a login session exists
MAX_COMPS = 65                      # cap eBay lookups per scan (time/cost); excess -> low-confidence
DEFECT_CHECK_CAP = 24              # cap description reads per scan (deals + free vetting)
EBAY_CONDITIONS = {"new", "used", "open box", "refurbished"}

# --- Concurrency: parallelism speeds + effectively widens the scan. eBay tolerates it
#     freely (different site); FB gets modest concurrency (detection trade-off, jittered). ---
FB_CONCURRENCY = 5                 # parallel FB search queries (1 when a login profile is set)
EBAY_CONCURRENCY = 6               # parallel eBay comp lookups
DETAIL_CONCURRENCY = 5             # parallel FB detail fetches

# Geographic widening (best-effort; FB honors radius most reliably with explicit lat/long).
# Defaults anchor on Seattle; override via env (FB_RADIUS_KM / FB_LAT / FB_LNG).
RADIUS_KM = int(os.environ.get("FB_RADIUS_KM", "120")) or None    # ~75 mi
SEATTLE_LAT = float(os.environ.get("FB_LAT", "47.6062"))
SEATTLE_LNG = float(os.environ.get("FB_LNG", "-122.3321"))

# --- Pacing (avoid bot detection): randomized human-like gaps between FB searches. ---
PACE_FB = (2.5, 6.0)              # seconds between Facebook search queries


def _pace(bounds: tuple[float, float]) -> None:
    """Sleep a randomized interval to keep request cadence human-like."""
    time.sleep(random.uniform(*bounds))


def load_env() -> None:
    """Load repo-root .env into os.environ (so ANTHROPIC_API_KEY / FB_MCP_DIR are visible)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'").strip('"')
        os.environ.setdefault(name, value)


def _extract_json(text: str, open_ch: str, close_ch: str):
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def run_fb(query: str, location_id: str, days: int, max_price: int | None = None,
           scroll: int = 0) -> list[dict]:
    fb_dir = os.environ.get("FB_MCP_DIR")
    if not fb_dir or not Path(fb_dir).exists():
        print(f"  ! FB_MCP_DIR missing/invalid ({fb_dir}); skipping '{query}'")
        return []
    cmd = ["uv", "run", "python", "scraper.py", query,
           "--location", location_id, "--days", str(days), "--json"]
    if max_price is not None:
        cmd += ["--max-price", str(max_price)]
    if scroll:
        cmd += ["--scroll", str(scroll)]
    if RADIUS_KM:
        cmd += ["--radius-km", str(RADIUS_KM), "--lat", str(SEATTLE_LAT), "--lng", str(SEATTLE_LNG)]
    try:
        proc = subprocess.run(cmd, cwd=fb_dir, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f"  ! FB scrape timed out for '{query}'")
        return []
    data = _extract_json(proc.stdout, "[", "]")
    return data if isinstance(data, list) else []


def run_fb_details(listing_id: str) -> dict:
    """Fetch one listing's description/condition via the FB scraper's --details mode."""
    fb_dir = os.environ.get("FB_MCP_DIR")
    if not fb_dir or not Path(fb_dir).exists() or not listing_id:
        return {}
    cmd = ["uv", "run", "python", "scraper.py", "--details", str(listing_id)]
    try:
        proc = subprocess.run(cmd, cwd=fb_dir, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {}
    data = _extract_json(proc.stdout, "{", "}")
    return data if isinstance(data, dict) else {}


def _merge_dedup(*lists: list[dict]) -> list[dict]:
    """Merge listing lists, keeping first occurrence per listing_id."""
    seen: set[str] = set()
    out: list[dict] = []
    for lst in lists:
        for l in lst:
            lid = l.get("listing_id")
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(l)
    return out


def run_ebay(query: str, condition: str | None) -> dict:
    cmd = ["uv", "run", "python", "scraper.py", query]
    if condition in EBAY_CONDITIONS:
        cmd += ["--condition", condition]
    try:
        proc = subprocess.run(cmd, cwd=EBAY_DIR, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f"  ! eBay comp timed out for '{query}'")
        return {}
    data = _extract_json(proc.stdout, "{", "}")
    return data if isinstance(data, dict) else {}


def main() -> None:
    load_env()
    cfg = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8"))
    defaults = cfg.get("defaults", {})
    items = cfg.get("items", [])
    location_id = os.environ.get("FB_LOCATION_ID", "seattle")

    # Build the target list: curated watchlist items + broad discovery queries ("any items").
    disc = cfg.get("discovery", {}) or {}
    targets: list[tuple[str, dict, bool]] = [
        (it["query"], scoring.effective_thresholds(it, defaults), False) for it in items
    ]
    if disc.get("enabled"):
        disc_eff = scoring.effective_thresholds({}, defaults)
        for q in disc.get("queries", []):
            targets.append((q, disc_eff, True))
    print(f"Targets: {len(items)} watchlist + "
          f"{len(targets) - len(items)} discovery = {len(targets)} queries (paced).")

    now = dt.datetime.now()
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    stamp = now.strftime("%Y-%m-%d-%H%M")

    rows: list[dict] = []          # one dict per FB listing (with normalized fields)
    eff_by_key: dict[str, dict] = {}

    # FB query concurrency: gentle parallelism speeds the scan and effectively widens it.
    # A persistent profile locks its dir, so force serial when FB_PROFILE_DIR is set.
    fb_workers = 1 if os.environ.get("FB_PROFILE_DIR") else FB_CONCURRENCY

    def scan_query(target: tuple) -> list[dict]:
        qi, (query, eff, is_disc) = target
        tag = "disc" if is_disc else "watch"
        _pace(PACE_FB)   # jittered start so workers don't fire in lockstep
        # priced + free sweeps, serially within the query (parallelism is across queries).
        priced = run_fb(query, location_id, eff["days"], None, SCROLL_ROUNDS)[:MAX_LISTINGS_PER_ITEM]
        free = run_fb(query, location_id, FREE_SWEEP_DAYS, 0, SCROLL_ROUNDS)[:MAX_FREE_PER_ITEM]
        listings = _merge_dedup(priced, free)
        if not listings:
            print(f"[scan] ({tag}) {query}: no listings", flush=True)
            return []
        titles = [l.get("title", "") for l in listings]
        norm = normalize.normalize_titles(titles, category_hint=query)
        print(f"[scan] ({tag}) {query}: {len(listings)} listings (+{len(free)} free)", flush=True)
        out = []
        for l, nr in zip(listings, norm):
            row = dict(nr)
            row.update({
                "query": query, "_eff": eff, "_qi": qi,
                "listing_id": l.get("listing_id"),
                "price_usd": scoring.parse_price_usd(l.get("price")),
                "location": l.get("location"),
                "url": l.get("url"),
                "image_url": l.get("image_url"),
                "ebay_median": None, "ebay_count": 0,
            })
            out.append(row)
        return out

    # ---- 1-2. Scrape + normalize all targets (watchlist + discovery), concurrently ----
    print(f"Scraping {len(targets)} queries, {fb_workers}-way concurrent ...", flush=True)
    collected: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=fb_workers) as ex:
        collected = list(ex.map(scan_query, enumerate(targets)))

    # Global dedup by listing_id (keep the earliest query that surfaced it).
    seen_global: set[str] = set()
    for batch in sorted((b for b in collected if b), key=lambda b: b[0]["_qi"]):
        for row in batch:
            lid = row.get("listing_id")
            if lid and lid in seen_global:
                continue
            if lid:
                seen_global.add(lid)
            eff = row.pop("_eff")
            row.pop("_qi", None)
            rows.append(row)
            if row["canonical_key"]:
                eff_by_key[row["canonical_key"]] = eff

    # ---- 2b. Price-drop-to-$0 detection: was this listing priced > 0 in a prior scan? ----
    db.init_db()
    prior = db.get_prior_prices([r["listing_id"] for r in rows if r.get("listing_id")])
    dropped = 0
    for r in rows:
        lid = r.get("listing_id")
        if lid and prior.get(lid, 0) > 0 and r["price_usd"] == 0:
            r["price_dropped_to_zero"] = 1
            dropped += 1
    if dropped:
        print(f"[price] {dropped} listing(s) dropped from a prior price to $0 (likely sold/zeroed)", flush=True)

    # ---- 3. One eBay comp per canonical product (skip parts / want-ads / dealer ads) ----
    real = [r for r in rows if not r["is_part"] and not r["is_wanted_ad"]
            and not r.get("is_advertisement") and r["canonical_key"]]
    by_key: dict[str, list[dict]] = collections.defaultdict(list)
    for r in real:
        by_key[r["canonical_key"]].append(r)

    # Look up the most-listed products first; cap the rest as low-confidence.
    ranked_keys = sorted(by_key, key=lambda k: len(by_key[k]), reverse=True)
    if len(ranked_keys) > MAX_COMPS:
        print(f"[comp] {len(ranked_keys)} products; fetching comps for top {MAX_COMPS}, "
              f"{len(ranked_keys) - MAX_COMPS} left as low-confidence (MAX_COMPS).")
    comp_keys = [k for k in ranked_keys if k in set(ranked_keys[:MAX_COMPS])]

    def fetch_comp(key: str):
        group = by_key[key]
        ebay_query = collections.Counter(r["ebay_query"] for r in group).most_common(1)[0][0]
        cond_mode = collections.Counter(r["condition"] for r in group).most_common(1)[0][0]
        return key, ebay_query, run_ebay(ebay_query, cond_mode)

    comps: dict[str, dict] = {}
    print(f"[comp] fetching {len(comp_keys)} comps ({EBAY_CONCURRENCY}-way parallel) ...", flush=True)
    with ThreadPoolExecutor(max_workers=EBAY_CONCURRENCY) as ex:
        for key, ebay_query, comp in ex.map(fetch_comp, comp_keys):
            comps[key] = comp
            median, count = comp.get("median"), comp.get("count", 0)
            for r in by_key[key]:
                r["ebay_median"] = median
                r["ebay_count"] = count or 0
            print(f"  {key} -> ${median} (n={count})", flush=True)

    # ---- 4. Score ----
    for r in rows:
        eff = eff_by_key.get(r["canonical_key"], scoring.effective_thresholds({}, defaults))
        s = scoring.score_listing(
            r["price_usd"], r["ebay_median"], r["ebay_count"], eff,
            is_part=r["is_part"], is_wanted_ad=r["is_wanted_ad"],
            is_advertisement=r.get("is_advertisement", False),
        )
        r.update(s)
        for k, v in (("detail_checked", 0), ("defect_severity", None),
                     ("defect_summary", None), ("defects_json", None), ("for_parts", 0),
                     ("listing_intent", None), ("genuinely_free", 0), ("false_free", 0),
                     ("is_advertisement", int(bool(r.get("is_advertisement")))),
                     ("availability", None), ("price_in_description", None),
                     ("price_dropped_to_zero", 0), ("sold", 0)):
            r.setdefault(k, v)

    # ---- 4b. Defect / condition read via Claude (top candidates only) ----
    def _priority(r):
        order = {"deal": 0, "review": 2}.get(r["verdict"], 3)
        if r["price_usd"] == 0:          # free items are always worth a condition read
            order = min(order, 1)
        return (order, -(r.get("est_profit") or 0))

    candidates = [
        r for r in rows
        if r["verdict"] in ("deal", "review")
        or (r["price_usd"] == 0 and not r["is_part"] and not r["is_wanted_ad"])
    ]
    candidates.sort(key=_priority)
    candidates = candidates[:DEFECT_CHECK_CAP]
    print(f"[defects] reading {len(candidates)} candidates ({DETAIL_CONCURRENCY}-way parallel) ...", flush=True)

    def check_defect(r: dict):
        details = run_fb_details(r["listing_id"])
        if not details:
            return r, None, None
        a = defects.assess_defects(
            r.get("title", ""), details.get("description", ""), details.get("condition"))
        return r, a, details.get("condition")

    false_free = 0
    with ThreadPoolExecutor(max_workers=DETAIL_CONCURRENCY) as ex:
        for r, a, cond in ex.map(check_defect, candidates):
            if not a:
                continue
            r["detail_checked"] = 1
            r["condition"] = cond or r["condition"]
            r["defect_severity"] = a["severity"]
            r["defect_summary"] = a["condition_summary"]
            r["for_parts"] = int(bool(a["for_parts_or_broken"]))
            r["listing_intent"] = a["listing_intent"]
            r["genuinely_free"] = int(bool(a["genuinely_free"]))
            r["availability"] = a["availability"]
            r["price_in_description"] = a.get("price_in_description") or None
            r["defects_json"] = json.dumps({
                "defects": a["defects"], "risk_flags": a["risk_flags"],
                "refurb_needed": a["refurb_needed"], "for_parts": a["for_parts_or_broken"],
                "listing_intent": a["listing_intent"], "availability": a["availability"],
                "price_in_description": a.get("price_in_description") or None,
            })

            # SOLD / pending / unavailable (incl. misspellings) -> remove, it can't be bought.
            if a["availability"] in ("sold", "pending", "unavailable"):
                r["verdict"] = "skip"
                r["sold"] = 1
                if r["price_usd"] == 0:
                    r["false_free"] = 1
                print(f"  [{a['availability']}] {(r['canonical_name'] or '')[:30]} -> skip", flush=True)
                continue

            # DEFECTIVE / for-parts / broken -> remove (skip), not Review.
            if a["for_parts_or_broken"]:
                r["verdict"] = "skip"
                print(f"  [defective] {(r['canonical_name'] or '')[:30]} -> removed", flush=True)
                continue

            # Dealer/storefront ADVERTISEMENT (any price) -> skip (not a single buyable item).
            if a["listing_intent"] == "advertisement":
                r["verdict"] = "skip"
                if r["price_usd"] == 0:
                    r["false_free"] = 1
                    false_free += 1
                print(f"  [dealer-ad] {(r['canonical_name'] or '')[:32]} -> skip", flush=True)
                continue

            # Negate FALSE FREE: a $0 item the description reveals as a trade / sale / ISO /
            # mis-list (real price in the text) is NOT a free deal.
            if r["price_usd"] == 0 and not a["genuinely_free"] and \
                    a["listing_intent"] in ("trade_only", "want_to_buy", "mislisted", "for_sale"):
                r["verdict"] = "skip" if a["listing_intent"] in ("trade_only", "want_to_buy") else "review"
                r["false_free"] = 1
                false_free += 1
                pid = f" (real price ~${a['price_in_description']:.0f})" if a.get("price_in_description") else ""
                print(f"  [false-free:{a['listing_intent']}]{pid} {(r['canonical_name'] or '')[:26]} -> {r['verdict']}", flush=True)
                continue

            print(f"  [{a['severity']}] {(r['canonical_name'] or '')[:32]} — {a['condition_summary'][:54]}", flush=True)
            # A high-severity (but not for-parts) 'deal' is uncertain -> Review.
            if r["verdict"] == "deal" and a["severity"] == "high":
                r["verdict"] = "review"
    if false_free:
        print(f"[defects] negated {false_free} false-'free' listings", flush=True)

    # ---- 4c. Tally + rank ----
    deals = sum(1 for r in rows if r["verdict"] == "deal")
    review = sum(1 for r in rows if r["verdict"] == "review")
    rows.sort(key=lambda r: (r.get("est_profit") is None, -(r.get("est_profit") or 0)))

    # ---- 5. Persist: report JSON (+ MD) and DB ingest ----
    report = {
        "meta": {
            "ts": ts,
            "location_id": location_id,
            "watchlist_size": len(items),
            "scanned_count": len(rows),
            "deals_count": deals,
            "review_count": review,
            "note": None,
        },
        "listings": rows,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(REPORTS_DIR / f"{stamp}.md", report)

    db.init_db()
    scan_id = db.ingest_report(report)
    # Remember each listing's current price for next scan's drop-to-$0 detection.
    db.record_listing_prices(
        [(r["listing_id"], r["price_usd"]) for r in rows
         if r.get("listing_id") and r["price_usd"] is not None],
        ts,
    )

    print(f"\nScan {scan_id}: {len(rows)} listings | {deals} deals | {review} review")
    print(f"Report: reports/{stamp}.md  |  DB: data/openeye.db")


def _write_markdown(path: Path, report: dict) -> None:
    m = report["meta"]
    lines = [
        f"# OpenEye scan — {m['ts']}",
        "",
        f"- location_id: `{m['location_id']}`",
        f"- watchlist items: {m['watchlist_size']} · listings scanned: {m['scanned_count']}",
        f"- ✅ deals: {m['deals_count']} · ⚠️ review: {m['review_count']}",
        "",
        "## ✅ Deals",
        "",
        "| Est. profit | Asking | Median sold (n) | Ratio | Condition | Title | Location |",
        "|---|---|---|---|---|---|---|",
    ]

    def fmt(r):
        prof = f"${r['est_profit']:.0f}" if r.get("est_profit") is not None else "—"
        ask = f"${r['price_usd']:.0f}" if r.get("price_usd") else "—"
        med = f"${r['ebay_median']:.0f} ({r['ebay_count']})" if r.get("ebay_median") else "—"
        ratio = f"{r['ratio']:.2f}" if r.get("ratio") is not None else "—"
        title = (r.get("title") or "").replace("|", "\\|")[:60]
        link = f"[{title}]({r.get('url')})" if r.get("url") else title
        return f"| {prof} | {ask} | {med} | {ratio} | {r.get('condition','')} | {link} | {r.get('location','')} |"

    for r in report["listings"]:
        if r["verdict"] == "deal":
            lines.append(fmt(r))
    lines += ["", "## ⚠️ Review", "",
              "| Est. profit | Asking | Median sold (n) | Ratio | Condition | Title | Location |",
              "|---|---|---|---|---|---|---|"]
    for r in report["listings"]:
        if r["verdict"] == "review":
            lines.append(fmt(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
