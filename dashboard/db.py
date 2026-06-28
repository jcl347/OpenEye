"""
SQLite persistence for OpenEye.

One file-based DB (data/openeye.db) holds every scan's listings, their LLM-normalized
product identity, the eBay sold comp used to value them, the computed deal verdict, and a
long price-history table so the dashboard can chart how prices for each canonical product
move over time. Uses the stdlib `sqlite3` only — no ORM, no extra deps.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

# data/ lives at the repo root, one level up from dashboard/
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "openeye.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,              -- ISO8601 scan timestamp
    location_id    TEXT,
    watchlist_size INTEGER,
    scanned_count  INTEGER,
    deals_count    INTEGER,
    review_count   INTEGER,
    note           TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id        INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    ts             TEXT NOT NULL,
    query          TEXT,                       -- the watchlist query that surfaced it
    listing_id     TEXT,                       -- Facebook listing id
    title          TEXT,
    -- LLM-normalized identity (no regex) --
    canonical_key  TEXT,                       -- brand|model|variant, lowercased
    canonical_name TEXT,
    brand          TEXT,
    model          TEXT,
    variant        TEXT,
    condition      TEXT,
    is_part        INTEGER DEFAULT 0,          -- part/accessory/bundle filler
    is_wanted_ad   INTEGER DEFAULT 0,          -- "ISO / buying" ad, not a sale
    ebay_query     TEXT,                       -- clean query the comp was fetched with
    -- pricing + comp --
    price_usd      REAL,
    location       TEXT,
    url            TEXT,
    image_url      TEXT,
    ebay_median    REAL,
    ebay_count     INTEGER,
    est_profit     REAL,
    ratio          REAL,
    verdict        TEXT,                        -- deal | review | low-confidence | skip
    -- LLM defect/condition read (only for checked candidates) --
    detail_checked  INTEGER DEFAULT 0,
    defect_severity TEXT,                       -- none | low | medium | high
    defect_summary  TEXT,                       -- one-line condition summary
    defects_json    TEXT,                       -- JSON: {defects:[], risk_flags:[], for_parts, refurb_needed}
    for_parts       INTEGER DEFAULT 0,
    listing_intent  TEXT,                       -- for_sale | free_giveaway | trade_only | want_to_buy | mislisted | advertisement | other
    genuinely_free  INTEGER DEFAULT 0,
    false_free      INTEGER DEFAULT 0,          -- $0 price but really a trade/sale/ISO/mis-list/ad
    is_advertisement INTEGER DEFAULT 0,         -- dealer/storefront/solicitation post
    availability    TEXT,                        -- available | sold | pending | unavailable
    price_in_description REAL,                   -- real asking price found in the description, if any
    price_dropped_to_zero INTEGER DEFAULT 0,     -- was priced > 0 in a prior scan, now $0
    sold            INTEGER DEFAULT 0
);

-- Per-listing price memory across scans (survives the per-scan listings churn).
CREATE TABLE IF NOT EXISTS listing_prices (
    listing_id  TEXT PRIMARY KEY,
    last_price  REAL,
    last_ts     TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    canonical_key  TEXT NOT NULL,
    canonical_name TEXT,
    source         TEXT NOT NULL,              -- 'fb_asking' | 'ebay_median'
    price_usd      REAL NOT NULL,
    listing_id     TEXT,
    url            TEXT
);

CREATE INDEX IF NOT EXISTS ix_listings_scan   ON listings(scan_id);
CREATE INDEX IF NOT EXISTS ix_listings_canon  ON listings(canonical_key);
CREATE INDEX IF NOT EXISTS ix_history_canon   ON price_history(canonical_key);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_LISTINGS_MIGRATIONS = {
    "detail_checked": "INTEGER DEFAULT 0",
    "defect_severity": "TEXT",
    "defect_summary": "TEXT",
    "defects_json": "TEXT",
    "for_parts": "INTEGER DEFAULT 0",
    "listing_intent": "TEXT",
    "genuinely_free": "INTEGER DEFAULT 0",
    "false_free": "INTEGER DEFAULT 0",
    "is_advertisement": "INTEGER DEFAULT 0",
    "availability": "TEXT",
    "price_in_description": "REAL",
    "price_dropped_to_zero": "INTEGER DEFAULT 0",
    "sold": "INTEGER DEFAULT 0",
}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Forward-migrate older DBs: add any missing listings columns.
        have = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
        for col, decl in _LISTINGS_MIGRATIONS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")


def ingest_report(report: dict[str, Any]) -> int:
    """Persist one scan report (the dict pipeline.py writes to reports/<ts>.json).

    Returns the new scan id. Also appends to price_history so trends accrue across runs.
    """
    meta = report.get("meta", {})
    ts = meta.get("ts")
    rows = report.get("listings", [])

    deals = [r for r in rows if r.get("verdict") == "deal"]
    review = [r for r in rows if r.get("verdict") == "review"]

    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO scans (ts, location_id, watchlist_size, scanned_count,
                                  deals_count, review_count, note)
               VALUES (?,?,?,?,?,?,?)""",
            (
                ts,
                meta.get("location_id"),
                meta.get("watchlist_size"),
                meta.get("scanned_count", len(rows)),
                len(deals),
                len(review),
                meta.get("note"),
            ),
        )
        scan_id = cur.lastrowid

        for r in rows:
            conn.execute(
                """INSERT INTO listings (
                       scan_id, ts, query, listing_id, title, canonical_key, canonical_name,
                       brand, model, variant, condition, is_part, is_wanted_ad, ebay_query,
                       price_usd, location, url, image_url, ebay_median, ebay_count,
                       est_profit, ratio, verdict,
                       detail_checked, defect_severity, defect_summary, defects_json, for_parts,
                       listing_intent, genuinely_free, false_free,
                       is_advertisement, availability, price_in_description,
                       price_dropped_to_zero, sold)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scan_id, ts, r.get("query"), r.get("listing_id"), r.get("title"),
                    r.get("canonical_key"), r.get("canonical_name"), r.get("brand"),
                    r.get("model"), r.get("variant"), r.get("condition"),
                    int(bool(r.get("is_part"))), int(bool(r.get("is_wanted_ad"))),
                    r.get("ebay_query"), r.get("price_usd"), r.get("location"),
                    r.get("url"), r.get("image_url"), r.get("ebay_median"),
                    r.get("ebay_count"), r.get("est_profit"), r.get("ratio"),
                    r.get("verdict"),
                    int(bool(r.get("detail_checked"))), r.get("defect_severity"),
                    r.get("defect_summary"), r.get("defects_json"),
                    int(bool(r.get("for_parts"))),
                    r.get("listing_intent"), int(bool(r.get("genuinely_free"))),
                    int(bool(r.get("false_free"))),
                    int(bool(r.get("is_advertisement"))), r.get("availability"),
                    r.get("price_in_description"),
                    int(bool(r.get("price_dropped_to_zero"))), int(bool(r.get("sold"))),
                ),
            )

            # Price history: the FB asking price for any real (non-part, non-ISO) listing.
            # `is not None` so free ($0) items are recorded, not silently dropped.
            if r.get("price_usd") is not None and not r.get("is_part") and not r.get("is_wanted_ad"):
                conn.execute(
                    """INSERT INTO price_history (ts, canonical_key, canonical_name,
                                                  source, price_usd, listing_id, url)
                       VALUES (?,?,?,?,?,?,?)""",
                    (ts, r.get("canonical_key"), r.get("canonical_name"),
                     "fb_asking", r.get("price_usd"), r.get("listing_id"), r.get("url")),
                )

        # Price history: one eBay median point per canonical product this scan.
        seen_medians: set[str] = set()
        for r in rows:
            key = r.get("canonical_key")
            if key and key not in seen_medians and r.get("ebay_median"):
                seen_medians.add(key)
                conn.execute(
                    """INSERT INTO price_history (ts, canonical_key, canonical_name,
                                                  source, price_usd)
                       VALUES (?,?,?,?,?)""",
                    (ts, key, r.get("canonical_name"), "ebay_median", r.get("ebay_median")),
                )

    return scan_id


def _latest_scan_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT id FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def _resolve_scan_id(conn: sqlite3.Connection, scan_id: Optional[int]) -> Optional[int]:
    """Return the requested scan id if it exists, else the latest."""
    if scan_id is not None:
        row = conn.execute("SELECT id FROM scans WHERE id=?", (scan_id,)).fetchone()
        if row:
            return row["id"]
    return _latest_scan_id(conn)


def get_prior_prices(listing_ids: list[str]) -> dict[str, float]:
    """Last-seen price per listing_id from earlier scans (for price-drop detection)."""
    if not listing_ids:
        return {}
    out: dict[str, float] = {}
    with connect() as conn:
        # chunk to stay under SQLite's variable limit
        for i in range(0, len(listing_ids), 400):
            chunk = listing_ids[i : i + 400]
            q = "SELECT listing_id, last_price FROM listing_prices WHERE listing_id IN (%s)" % (
                ",".join("?" * len(chunk))
            )
            for row in conn.execute(q, chunk):
                if row["last_price"] is not None:
                    out[row["listing_id"]] = row["last_price"]
    return out


def record_listing_prices(pairs: list[tuple[str, float]], ts: str) -> None:
    """Upsert each listing_id -> current price for next-scan comparison."""
    with connect() as conn:
        conn.executemany(
            """INSERT INTO listing_prices (listing_id, last_price, last_ts) VALUES (?,?,?)
               ON CONFLICT(listing_id) DO UPDATE SET last_price=excluded.last_price,
                                                     last_ts=excluded.last_ts""",
            [(lid, price, ts) for lid, price in pairs if lid],
        )


def get_scans() -> list[dict[str, Any]]:
    """All scans, newest first — powers the historical lookup selector."""
    with connect() as conn:
        latest = _latest_scan_id(conn)
        rows = conn.execute(
            """SELECT id, ts, scanned_count, deals_count, review_count
               FROM scans ORDER BY id DESC"""
        ).fetchall()
        return [{**dict(r), "is_latest": r["id"] == latest} for r in rows]


def get_summary(scan_id: Optional[int] = None) -> dict[str, Any]:
    with connect() as conn:
        sid = _resolve_scan_id(conn, scan_id)
        if sid is None:
            return {"has_data": False}
        scan = dict(conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone())
        profit = conn.execute(
            "SELECT COALESCE(SUM(est_profit),0) p FROM listings WHERE scan_id=? AND verdict='deal'",
            (sid,),
        ).fetchone()["p"]
        total_scans = conn.execute("SELECT COUNT(*) c FROM scans").fetchone()["c"]
        return {
            "has_data": True,
            "scan_id": sid,
            "is_latest": sid == _latest_scan_id(conn),
            "last_scan_ts": scan["ts"],
            "location_id": scan["location_id"],
            "watchlist_size": scan["watchlist_size"],
            "scanned_count": scan["scanned_count"],
            "deals_count": scan["deals_count"],
            "review_count": scan["review_count"],
            "total_potential_profit": round(profit, 2),
            "total_scans": total_scans,
            "note": scan["note"],
        }


def get_listings(verdict: Optional[str] = None, scan_id: Optional[int] = None) -> list[dict[str, Any]]:
    with connect() as conn:
        sid = _resolve_scan_id(conn, scan_id)
        if sid is None:
            return []
        sql = "SELECT * FROM listings WHERE scan_id=?"
        params: list[Any] = [sid]
        if verdict:
            sql += " AND verdict=?"
            params.append(verdict)
        sql += " ORDER BY (est_profit IS NULL), est_profit DESC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_products(scan_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Canonical products with their stats, for the price-history selector."""
    with connect() as conn:
        sid = _resolve_scan_id(conn, scan_id)
        if sid is None:
            return []
        rows = conn.execute(
            """SELECT canonical_key, canonical_name,
                      COUNT(*) n_listings,
                      MIN(price_usd) min_ask,
                      AVG(price_usd) avg_ask,
                      MAX(ebay_median) ebay_median
               FROM listings
               WHERE scan_id=? AND canonical_key IS NOT NULL
                     AND is_part=0 AND is_wanted_ad=0
               GROUP BY canonical_key
               ORDER BY n_listings DESC""",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_all() -> dict[str, int]:
    """Wipe all stored scan history. Returns counts deleted. Irreversible."""
    with connect() as conn:
        counts = {
            "listings": conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"],
            "scans": conn.execute("SELECT COUNT(*) c FROM scans").fetchone()["c"],
            "price_history": conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"],
        }
        conn.execute("DELETE FROM listings")
        conn.execute("DELETE FROM price_history")
        conn.execute("DELETE FROM scans")
        # Reset autoincrement counters if the sqlite_sequence table exists.
        try:
            conn.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
    return counts


def get_history(canonical_key: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT ts, source, price_usd, listing_id, url
               FROM price_history WHERE canonical_key=? ORDER BY ts""",
            (canonical_key,),
        ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
