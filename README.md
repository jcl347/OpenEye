# OpenEye — Marketplace deal scanner

OpenEye finds **Facebook Marketplace** listings whose asking price is well below their **resale
value** — where resale value comes from **eBay _sold_ comps** — and surfaces them on a local
**executive dashboard**. It's discovery-only: it never messages a seller or buys anything; you
review the ranked deals and decide.

The distinguishing idea: **Claude does the judgment work.** Matching a messy title to the right
product, deciding which eBay sales are valid comps, reading a description for defects/scams,
vetting "free" items — all done by the LLM with structured output, not brittle keyword rules.

> **Responsible use.** Scraping Facebook/eBay is contrary to their Terms of Service and can get an
> account limited. Keep this at personal scale. You are solely responsible for complying with all
> terms and applicable laws. See [Notes & responsible use](#notes--responsible-use).

---

## How it works

```
  config/watchlist.yaml ──┐
   (what to hunt for)      │
                           ▼
   Facebook scrape ──▶ LLM normalize ──▶ eBay sold comps ──▶ score ──▶ LLM read details ──▶ dashboard
   (priced + free)     (title→product)   (LLM-filtered to     (profit,    (defects, intent,   (KPIs, ranked
                                          the exact product)   confidence)  sold, lots)         deals, charts)
                                                  │
                                                  ▼
                                  data/openeye.db  +  reports/<ts>.{md,json}
```

Two scrapers (Facebook + eBay) feed a Python pipeline that normalizes, values, scores, and persists
every candidate to SQLite, then a small web app serves the dashboard at **http://127.0.0.1:8500**.

---

## MCP servers

OpenEye composes **two local [MCP](https://modelcontextprotocol.io) servers** — one per data
source — each wrapping a headless-browser scraper. They're deliberately built differently to match
what each needs:

| | **facebook-marketplace** | **ebay-sold-comps** |
|---|---|---|
| Role | Find listings to evaluate | Value them via sold comps |
| Transport | **HTTP** service (`127.0.0.1:8000`) | **stdio** (launched on demand) |
| Lifecycle | Long-lived; owns a persistent browser session | Spawned per scan, then exits |
| Origin | Third-party clone, **patched** (see `patches/`) | **Bundled** (`mcp/ebay-sold-comps`) |
| Cost / secret | None (your own session) | None — **no API token, no per-call fee** |

- **Transport matched to lifecycle.** Facebook holds a stateful browser/login → a persistent HTTP
  service. An eBay comp lookup is a stateless one-shot → an on-demand stdio process (no port,
  nothing to keep running).
- **Local & self-contained.** The eBay server replaced a paid, token-based cloud comp source, so
  nothing proprietary leaves your machine and there's no per-call cost.
- **Two ways to reach the scrapers.** The dashboard pipeline drives the scrapers directly via their
  command-line interface (deterministic, isolated dependencies); the same scrapers are also exposed
  as MCP **tools** (`search_marketplace`, `get_listing_details`, `search_sold_comps`) so the
  agent-driven path can call them conversationally.

The Facebook MCP is a clone of [`fisheyes/mcp-facebook-market-place`](https://github.com/fisheyes/mcp-facebook-market-place)
with OpenEye's additions applied as a reproducible patch — see [patches/](patches/).

---

## Quick start (deploy)

Full step-by-step (with the exact commands and troubleshooting) is in
**[GETTING_STARTED.md](GETTING_STARTED.md)**. The short version:

**1. Prerequisites**
- **Windows + PowerShell**, **[uv](https://docs.astral.sh/uv/)**, and an **`ANTHROPIC_API_KEY`**
  (the pipeline calls Claude directly).
- A clone of the Facebook MCP (patched by OpenEye).

**2. Clone the Facebook MCP and apply OpenEye's patch**
```powershell
git clone https://github.com/fisheyes/mcp-facebook-market-place C:\path\to\mcp-facebook-market-place
cd C:\path\to\mcp-facebook-market-place
git apply C:\path\to\OpenEye\patches\fb-mcp-openeye.patch
```

**3. Configure `.env`** (gitignored — never committed)
```powershell
Copy-Item .env.example .env      # then edit:
#   ANTHROPIC_API_KEY = your Claude API key
#   FB_MCP_DIR        = path to the FB MCP clone above
#   FB_LOCATION_ID    = your search area (a city slug like "seattle" works; numeric IDs work too)
```

**4. Install dependencies** (corporate proxy? add `--native-tls`)
```powershell
cd C:\path\to\mcp-facebook-market-place ; uv sync ; uv run playwright install chromium
cd C:\path\to\OpenEye\mcp\ebay-sold-comps ; uv sync
cd C:\path\to\OpenEye\dashboard ; uv sync          # pinned to Python 3.12
```

**5. Start the Facebook MCP and log in once**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-fb-mcp.ps1
```
A Chromium window opens — log in to Facebook, dismiss warnings, and leave it running.

**6. Run a scan + open the dashboard**
```powershell
cd C:\path\to\OpenEye\dashboard
uv run python pipeline.py        # one scan -> SQLite + reports/
uv run python app.py             # serve the dashboard
```
Open **http://127.0.0.1:8500**. Or skip the first command and click **Run scan** in the dashboard —
it runs the same pipeline and refreshes when done.

> **Set your area** in `FB_LOCATION_ID`. It defaults to nothing useful out of the box; a city slug
> (e.g. `seattle`, `austin`, `chicago`) works because the scraper drops it straight into the
> Marketplace URL. Facebook's default radius for an area covers the surrounding metro.

---

## Two ways to run

- **Dashboard pipeline (recommended)** — `dashboard/pipeline.py`: a deterministic Python pipeline
  with the LLM in the loop only for judgment (normalization, comp curation, description reading).
  Persists to SQLite and powers the dashboard. This is the primary interface.
- **Agent-driven** — [CLAUDE.md](CLAUDE.md) is a scan playbook an LLM can execute directly via the
  MCP tools (`claude "Run the Facebook Marketplace deal scan per CLAUDE.md."`). Good for ad-hoc runs;
  the scripts in `scripts/` wrap this for headless/scheduled use.

---

## Configuration

- **`config/watchlist.yaml`** — the curated watchlist (electronics-led) + a broad **discovery**
  sweep ("find any items"), plus the deal **thresholds** (`max_asking_ratio`, `min_profit_usd`,
  `min_comp_samples`, fees/shipping, resale haircut). Per-item keys override the defaults.
- **`.env`** — `ANTHROPIC_API_KEY`, `FB_MCP_DIR`, `FB_LOCATION_ID`, and optional knobs
  (`FB_PROFILE_DIR` for a persistent login, `FB_RADIUS_KM`/`FB_LAT`/`FB_LNG` for a wider area).
- **`.mcp.json`** — copy from [mcp.example.json](mcp.example.json); declares the two MCP servers and
  contains no secrets (it interpolates `${VAR}` from your environment).

---

## Using the dashboard

- **KPI band + FB→eBay comparison** at the top; **ranked opportunities** table (Deals / Review /
  Free / All); an **Expected-profit** chart with clickable bubbles.
- Each deal shows the asking price vs. the eBay sold median (and comp count), a condition/defect
  read, and badges (GPU, lot, bundle, genuine-free, sold, etc.).
- **Multi-item lots** are decomposed — each item in the listing is valued against eBay individually.
- **Run scan** triggers a fresh scan in the background and auto-refreshes; **Clear history** wipes
  the database behind a typed confirmation.

---

## Notes & responsible use

- **Legal / account risk.** Marketplace scraping violates Facebook's ToS and can get an account
  warned or banned; eBay scraping is likewise against its terms. Personal/educational scale only —
  don't bulk-harvest, and you accept the risk.
- **Anti-bot is best-effort.** The eBay scraper warms up cookies, hides the automation fingerprint,
  and paces requests with jitter; FB concurrency is kept gentle by default. This clears *soft*
  heuristics, not serious anti-bot — expect occasional empty results when a site shows a wall.
- **Logged-out coverage is shallow.** Anonymous Facebook returns ~one page per search; OpenEye
  compensates with breadth (many discovery queries). A persistent-login mode is built but **opt-in**
  (`FB_PROFILE_DIR` + `scraper.py --login`) because logged-in scraping raises account risk.
- **Sold ≠ asking.** Resale value comes only from **sold** comps; an LLM pass keeps only the exact
  same product so the median isn't polluted by parts or wrong models. Pricing is conservative
  (fees + shipping + a quick-sale haircut). Treat every number as a screen, not a guarantee.
- **Untrusted content.** Listing/comp text is attacker-controllable; it's treated as data, never
  instructions. Secrets are never printed; `reports/`, `data/`, `.env`, and `.mcp.json` are gitignored.

---

## Repository layout

| Path | Purpose |
|------|---------|
| [dashboard/](dashboard/) | The pipeline + FastAPI server + dashboard (the primary interface). |
| [dashboard/pipeline.py](dashboard/pipeline.py) | Orchestrator: scrape → normalize → comp → score → read → persist. |
| [dashboard/normalize.py](dashboard/normalize.py) · [comps.py](dashboard/comps.py) · [defects.py](dashboard/defects.py) | The LLM judgment steps (normalization, comp relevance, description reading). |
| [mcp/ebay-sold-comps/](mcp/ebay-sold-comps/) | The bundled local eBay sold-comps MCP (no token, no cost). |
| [patches/](patches/) | OpenEye's patch for the third-party Facebook MCP. |
| [config/watchlist.yaml](config/watchlist.yaml) | What to search for + deal thresholds + discovery sweep. |
| [CLAUDE.md](CLAUDE.md) | The agent-driven scan playbook (alternate run path). |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Full setup, usage, and troubleshooting. |
| `scripts/` | Start the FB MCP, run a headless scan, register a scheduled task. |
| `reports/`, `data/` | Runtime output + SQLite (gitignored). |

## Disclaimer

Provided for educational and personal use, with no warranty. You are responsible for complying with
the Terms of Service of Facebook, eBay, and any other service, and with all applicable laws.
