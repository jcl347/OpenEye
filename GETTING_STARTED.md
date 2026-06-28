# OpenEye — Getting Started

OpenEye finds underpriced **Facebook Marketplace** listings by valuing each against **eBay
sold comps**, then shows them on a local **executive dashboard**. Items are matched, condition-
read, and intent-classified by Claude (no brittle regex). This guide covers setup and daily use.

> **Responsible use.** Scraping Facebook/eBay is against their Terms of Service and can get an
> account limited. Keep this at personal scale. You are responsible for complying with all terms
> and laws. See the security/legal notes in [README.md](README.md).

---

## 1. Prerequisites

- **Windows** with **PowerShell**
- **[uv](https://docs.astral.sh/uv/)** (Python package/runtime manager)
- **Claude Code** (for the agent-driven scan) and/or an **`ANTHROPIC_API_KEY`** (the dashboard
  pipeline calls Claude directly for normalization, defects, and intent)
- A local clone of the **Facebook Marketplace MCP** (patched by OpenEye for free sweeps,
  details, scrolling, radius, and persistent login)

> **Behind a corporate proxy?** Every `uv` command below needs `--native-tls`, and the dashboard
> is pinned to **Python 3.12** (3.14's bundled OpenSSL crashes on some Windows setups). Both are
> already handled in the project config.

---

## 2. One-time setup

### 2a. Clone the Facebook MCP and apply OpenEye's patch
```powershell
git clone https://github.com/fisheyes/mcp-facebook-market-place C:\path\to\mcp-facebook-market-place
cd C:\path\to\mcp-facebook-market-place
git checkout 81a9f46                                    # base the patch was made against
git apply C:\path\to\OpenEye\patches\fb-mcp-openeye.patch
```
The patch adds the free sweep, listing details, scrolling, radius, and persistent login that
OpenEye depends on. See [patches/README.md](patches/README.md) for details and conflict handling.

### 2b. Configure `.env`
```powershell
Copy-Item .env.example .env
```
Edit `.env` and set:
- `ANTHROPIC_API_KEY` — your Claude API key
- `FB_MCP_DIR` — the path you cloned the FB MCP to
- `FB_LOCATION_ID` — `seattle` works (a city slug is fine; numeric IDs also work)

`.env`, `.mcp.json`, `data/`, and `reports/` are **gitignored** — secrets never get committed.

### 2c. Activate MCP config
```powershell
Copy-Item mcp.example.json .mcp.json
```

### 2d. Install dependencies (three projects)
```powershell
# Facebook MCP
cd C:\path\to\mcp-facebook-market-place
uv sync --native-tls
uv run playwright install chromium

# eBay sold-comps MCP (bundled, local — replaces Apify)
cd C:\Users\jcl34\OneDrive\Documents\GitHub\OpenEye\mcp\ebay-sold-comps
uv sync --native-tls

# Dashboard (pinned to Python 3.12)
cd C:\Users\jcl34\OneDrive\Documents\GitHub\OpenEye\dashboard
uv sync --native-tls
```

---

## 3. Run a scan + open the dashboard

```powershell
cd C:\Users\jcl34\OneDrive\Documents\GitHub\OpenEye\dashboard

# 1) Run one scan (scrapes FB + eBay, normalizes, scores, persists to SQLite)
uv run python pipeline.py

# 2) Start the dashboard
uv run python app.py
```
Open **http://127.0.0.1:8500**. Or skip step 1 and click **Run scan** in the dashboard — it
runs the same pipeline in the background and refreshes when done.

---

## 4. Using the dashboard

- **KPI cards** — deals found, total potential profit, free finds, listings scanned, scans on record.
- **Ranked opportunities** table, with filter tabs:
  - **✅ Deals** — asking ≤ threshold of resale AND profit ≥ minimum.
  - **⚠️ Review** — too-good-to-be-true, or a deal the description revealed as defective/false-free.
  - **🎁 Free** — every $0 listing surfaced by the free sweep.
  - **All** — everything (incl. **Fair price** = scanned but no margin).
- **Condition / defects column** — Claude's read of the description:
  - severity chip (Clean / Minor / Wear / High risk) + risk flags (e.g. *ex-mining*, *iCloud locked*).
  - **⚑ not really free (intent)** — a $0 listing the description exposed as a trade / sale / ISO /
    mis-list. These are auto-demoted out of Deals.
  - **✓ genuine free** — confirmed real giveaway.
- **Price history chart** — FB asking points vs. eBay median sold line, per canonical product, over time.
- **Clear all scan history** (footer) — wipes the database. **Double verification**: confirm, then
  type `ERASE`.

---

## 5. Tune what it hunts (`config/watchlist.yaml`)

- **`items:`** — the curated watchlist (currently electronics-led). Add/remove queries freely;
  per-item keys override the defaults (e.g. `min_comp_samples`, `resale_ship_usd`).
- **`discovery:`** — broad "look for any items" queries (run with a free sweep each). Trim for
  speed, expand for coverage.
- **`defaults:`** — the deal thresholds (tuned for electronics):
  | Key | Meaning |
  |---|---|
  | `max_asking_ratio` | flag when asking ≤ this fraction of median sold (0.70) |
  | `min_profit_usd` | require ≥ this net profit ($100) |
  | `min_comp_samples` | require ≥ this many eBay sold comps (8) |
  | `resale_fee_rate` / `resale_ship_usd` | fees + shipping subtracted from resale (0.13 / $12) |

---

## 6. Advanced knobs

- **Persistent login (deeper results).** Logged-out scraping only sees ~page 1. To go deeper:
  ```powershell
  # set FB_PROFILE_DIR=C:\...\.fb-profile in .env, then:
  cd C:\path\to\mcp-facebook-market-place
  uv run python scraper.py --login   # log in once in the window that opens
  ```
  Future scans reuse the session. **Trade-off:** logged-in scraping more clearly violates FB's ToS
  and risks *your account*. Leave `FB_PROFILE_DIR` unset for safer logged-out scraping.
- **Scan radius.** `FB_RADIUS_KM` / `FB_LAT` / `FB_LNG` in `.env` widen the geographic area
  (best-effort; FB honors it most reliably with lat/long).
- **Speed vs. detection.** `pipeline.py` constants: `FB_CONCURRENCY` (parallel FB queries),
  `EBAY_CONCURRENCY`, `MAX_COMPS`, `DEFECT_CHECK_CAP`, and `PACE_FB` (jittered delay). More
  concurrency / less pacing = faster but higher bot-detection risk.
- **Schedule it.** `scripts/register-task.ps1` registers a Windows task to run scans on a cadence.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `uv` TLS / `UnknownIssuer` errors | add `--native-tls` to the `uv` command |
| `OPENSSL_Applink` crash | the dashboard is pinned to Python 3.12 (`uv python pin 3.12`); re-`uv sync` |
| eBay returns an "Error Page" | the scraper warms up cookies + parses the current `.s-card` markup; retry |
| FB results empty / login wall | logged-out FB caps results; set up persistent login (§6) |
| Prices show `£`/`€` | `FB_LOCATION_ID` is wrong (defaults to UK) — set it to `seattle` |
| Dashboard blank | run a scan first (`pipeline.py`) or click **Run scan** |
