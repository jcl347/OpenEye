# OpenEye Dashboard (local server + executive UI)

A local FastAPI server that **persists** every scan into SQLite and serves an
**executive-facing dashboard** to see scraped item prices, deal rankings, and price
history over time. Items are matched to eBay comps via **LLM extraction** (no regex).

```
dashboard/
  pipeline.py   FB scrape → LLM normalize → eBay sold comps → score → reports/ + DB
  normalize.py  Claude structured extraction: title → {brand, model, variant, condition,
                is_part, is_wanted_ad, ebay_query}     ← the no-regex matching layer
  scoring.py    deal scoring (faithful port of CLAUDE.md) + non-regex price parsing
  db.py         SQLite schema + ingest/query (stdlib sqlite3, no ORM)
  app.py        FastAPI: JSON API + serves static/ + POST /api/scan
  static/       index.html + app.js (Tailwind + ECharts via CDN, zero build)
```

Data lands in `data/openeye.db` and `reports/<ts>.{json,md}` (both gitignored).

## Setup

```bash
cd dashboard
uv sync --native-tls        # your proxy needs --native-tls; harmless otherwise
```

Requires the repo `.env` (for `ANTHROPIC_API_KEY`, `FB_MCP_DIR`, `FB_LOCATION_ID`) and the
two scrapers already synced (`mcp/ebay-sold-comps` and the FB MCP clone).

## Run

```bash
# 1. populate via one scan (uses the live scrapers + Claude normalizer)
uv run python pipeline.py

# 2. serve the dashboard
uv run python app.py          # http://127.0.0.1:8500
```

Or skip step 1 and click **Run scan** in the UI — it triggers the same pipeline in the
background and refreshes when done.

## Why LLM normalization (not regex)

Matching a messy title to the right comp is the project's accuracy bottleneck. Regex only
matches surface patterns, so it can't tell a "Herman Miller Aeron **replacement caster**"
from the chair, or know "A7 IV" == "Alpha 7 IV". The normalizer asks Claude for a
structured identity per listing; `is_part` / `is_wanted_ad` drop the noise that was
collapsing comp medians (the Aeron-$36 problem), and `ebay_query` becomes the clean comp
query. See the approach comparison in the chat / commit history.

## Notes

- **Read-only except `/api/scan`.** The API never contacts sellers; it only reads the DB
  and (on demand) launches the scan pipeline.
- Listing/comp text is untrusted — the normalizer is instructed to treat titles as data,
  never instructions.
- Reports and the DB can contain seller names/locations — keep `data/` and `reports/`
  gitignored (they are).
