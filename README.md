# OpenEye — Marketplace deal scanner (Seattle / Edmonds, WA)

Finds **Facebook Marketplace** listings near Edmonds/Seattle whose asking price is well below their
**resale value**, where resale value comes from **eBay _sold_ comps**. Claude Code drives two MCP
servers, scores each candidate, and writes a ranked report. You review the report and decide whether
to act — the agent never contacts sellers or buys anything.

> **Responsible-use note.** Scraping Facebook Marketplace is contrary to Facebook's Terms of Service
> and may get an account restricted; using eBay/Apify data is subject to their terms too. The bundled
> FB MCP is an educational project. Keep usage at personal scale, and you are responsible for
> complying with all applicable terms and laws. See **Design considerations → Legal & account risk**.

## How it works

```
                 config/watchlist.yaml         .env  (FB_LOCATION_ID, tokens, paths)
                          │                       │
                          ▼                       ▼
  Windows Task Scheduler ─▶ scripts/scan.ps1 ─▶ claude -p  ──reads──▶ CLAUDE.md (scan logic)
  (or run on demand)                              │  │
                          ┌─────────────────────-─┘  └────────────────────┐
                          ▼                                               ▼
        mcp__facebook-marketplace  (local, Playwright,           mcp__ebay-sold-comps
         logged-in Chromium @ :8000)                              (Apify-hosted, sold prices)
                          │                                               │
                          └───────────────► score & rank ◄───────────────┘
                                                  │
                                                  ▼
                                  reports/<ts>.md + .json   (+ state/seen_listings.json)
```

The scan logic lives in [CLAUDE.md](CLAUDE.md). The scripts only start the MCP and invoke Claude with that playbook.

## Prerequisites

- **Claude Code** CLI, authenticated (subscription login, or `ANTHROPIC_API_KEY` in `.env`).
- **Python + [uv](https://docs.astral.sh/uv/)** to run the Facebook MCP.
- A local clone of the FB MCP: `git clone https://github.com/fisheyes/mcp-facebook-market-place`.
  Its first run installs a headless Chromium; you must **log in to Facebook once** in that profile.
- An **Apify account + API token** for the eBay sold-comps MCP (per-call cost), _or_ swap in another
  comp source (see Design considerations).

## Setup

1. **Clone the FB MCP** somewhere and note the path.
2. **Copy env + fill it in:** `Copy-Item .env.example .env`, then set `FB_MCP_DIR`, `FB_LOCATION_ID`,
   `APIFY_TOKEN`, and `EBAY_MCP_ACTOR`. (`.env` is gitignored.)
3. **Activate MCP config:** review [mcp.example.json](mcp.example.json), then copy it to `.mcp.json`.
   It pulls secrets from your environment via `${VAR}` — it stores no secrets itself. Confirm that
   the `ebay-sold-comps` entry (which sends a bearer token to `apify.com`) is acceptable to you, or
   replace it with your chosen comp source.
4. **Export env vars for MCP interpolation.** Claude Code expands `${VAR}` in `.mcp.json` from the
   **process environment**, not from `.env` automatically. Either set them as Windows user env vars,
   or let `scripts/scan.ps1` load `.env` for you (it does this before invoking Claude).
5. **Start the FB MCP and log in:** `powershell -ExecutionPolicy Bypass -File scripts\start-fb-mcp.ps1`.
   On first run, complete the Facebook login / dismiss warnings in the Chromium window the MCP opens,
   so the session persists.
6. **Approve the MCP servers once (interactive).** Run `claude` in this folder and let it run a scan;
   the first time, Claude asks you to approve the two project MCP servers — approve them. This records
   your approval in `.claude/settings.local.json`, after which **headless scans run without prompting**.
   (Headless runs cannot approve MCP servers, so this one-time interactive step is required.)

### Finding your Facebook `location_id`

The MCP takes a **numeric** Facebook area ID, not a city name or coordinates, and its default
(`108339199186201`) is the **UK** — you must override it.

1. In a logged-in browser, open `https://www.facebook.com/marketplace/seattle/` and use the location
   picker to set your area (e.g., Seattle) and the search radius.
2. Look at the resulting URL — `…/marketplace/<NUMERIC_ID>/…` — and copy `<NUMERIC_ID>` into
   `FB_LOCATION_ID`.

**Radius:** the MCP's `search_marketplace` has **no radius parameter**, so radius is whatever default
the chosen area uses (Facebook's city default of ~40 mi from Seattle already covers Edmonds, Everett,
Bellevue, and Tacoma). To set an explicit wide radius, patch the MCP's `scraper.py` to append
`&radius=<miles>` (and lat/long) to the marketplace search URL it builds — see Design considerations.

## Running

**On demand** (interactive):
```
claude "Run the Facebook Marketplace deal scan per CLAUDE.md."
```

**Headless / scheduled** — `scripts/scan.ps1` loads `.env`, then runs Claude non-interactively with
only the tools the scan needs allow-listed:
```
powershell -ExecutionPolicy Bypass -File scripts\scan.ps1
```
Make sure the FB MCP from step 5 is already running (it must stay up, with a valid login), and that
you completed the one-time MCP approval in setup step 6.

**Schedule it** (a few times a day — deals move fast). Register a Windows Scheduled Task that runs
the scan at 8am / 12pm / 6pm:
```
powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
# add -WithMcpAutostart to also launch the FB MCP at logon:
powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -WithMcpAutostart
```
The task runs as you, **only while you're logged on** — required, because the FB MCP needs your
desktop browser session. A Claude cloud routine can't do this (it has no access to that browser),
which is why this is a local Windows deployment — see Design considerations.

## Output

- `reports/<YYYY-MM-DD-HHMM>.md` — `✅ Deals` and `⚠️ Review` tables, ranked by estimated profit.
- `reports/<YYYY-MM-DD-HHMM>.json` — same data, machine-readable.
- `state/seen_listings.json` — dedup memory so you aren't re-alerted on the same listing.

`reports/` and `state/` are gitignored (they can contain seller names/locations).

## Design considerations

**Legal & account risk.** Marketplace scraping violates Facebook's ToS; an account used for it can be
warned, throttled, or banned. Public-data scraping legality is unsettled and jurisdiction-dependent —
this tool is for personal, educational use, and you accept that risk. Don't use a stranger's account,
and don't scale this into bulk/commercial harvesting.

**Why it's a *local* deployment (not a Claude cloud routine).** The FB MCP drives a **persistent,
logged-in Chromium** profile on one machine. A cloud-scheduled agent has no access to that browser
session, so scans must run on the machine holding the login. Hence Windows Task Scheduler + a
long-running local MCP, rather than a hosted routine.

**Search radius is approximate.** The MCP exposes only `location_id`, not a radius. We anchor on a
Seattle-area ID and lean on Facebook's ~40 mi city default to cover Edmonds. Precise radius/centering
requires patching the scraper's URL builder. Document any change so results stay interpretable.

**Prices are free-text strings.** The MCP returns `price` like `"$1,200"`, `"Free"`, or possibly a
non-USD symbol if the location is misconfigured. Normalization (and a currency sanity check) is part
of the scan; `$0`/non-numeric items are excluded rather than treated as infinite margin.

**Resale comps: sold ≠ asking.** Resale value must come from **sold** comps, not active asking prices,
or every estimate is inflated. The accuracy bottleneck is **matching** a messy listing title to the
right comp (model variant, condition, bundle vs. single). We require a minimum sample size and demote
thin/uncertain matches to *Review*.

**Fees, shipping & true margin.** Median sold price isn't take-home. The score subtracts assumed
platform/payment fees (~13%) and shipping, per category. Real margin also includes repair/refurb, your
time, and risk — treat the number as a screen, not a guarantee. Local-resale categories set shipping to 0.

**Scams & false positives.** A too-good-to-be-true ratio usually means a scam, stolen goods, a wrong
comp match, or a parts-only item — not free money. Such listings are routed to **Review** and never
auto-ranked as Deals. Always eyeball the listing before acting.

**Rate limiting & detection.** Headless scraping that hammers the site invites blocks and CAPTCHAs.
Keep the watchlist modest, pace requests, and rely on the persistent profile to avoid frequent
re-logins. Expect occasional empty results when Facebook shows a wall — the agent stops and asks you
to re-login rather than spinning.

**Alert fatigue & dedup.** `state/seen_listings.json` suppresses repeat alerts unless price drops
> 10%. Tune thresholds per category in `config/watchlist.yaml` so the report stays signal-dense.

**Cadence & freshness.** Good deals sell within minutes-to-hours. More frequent scans catch more but
raise detection risk and cost. A few runs/day with `days: 7` recency is a sane default; tighten to
`days: 1` if you scan often.

**Cost.** Apify comp calls and Claude tokens both cost money per run; the watchlist size bounds both.
Start small.

**Privacy.** Reports can contain seller names, neighborhoods, and listing photos' URLs. They're
gitignored; don't commit or share them, and prune old ones.

**Prompt-injection.** Listing text is attacker-controllable input to an LLM. CLAUDE.md instructs the
agent to treat all scraped text as data, never as instructions — keep that rule if you edit the playbook.

**Extensibility.** The comp source is pluggable: swap the Apify actor for the official **eBay Browse /
Marketplace Insights API** (ToS-cleaner, but access is gated), or another MCP. Add notifications
(email via a Gmail MCP, or a Discord webhook) at the end of the scan. If you ever want Claude out of
the hot loop, the pure price-parse + scoring logic in CLAUDE.md can be reimplemented as a script.

## Repository layout

| Path | Purpose |
|------|---------|
| [CLAUDE.md](CLAUDE.md) | The scan playbook the agent follows (source of truth for logic). |
| [config/watchlist.yaml](config/watchlist.yaml) | What to search for + per-category deal thresholds. |
| [mcp.example.json](mcp.example.json) | MCP server template — review, copy to `.mcp.json` to activate. |
| [.env.example](.env.example) | Env template — copy to `.env` and fill in. |
| `scripts/start-fb-mcp.ps1` | Start the local Facebook MCP (first run: log in once). |
| `scripts/scan.ps1` | Load `.env`, run one headless scan via Claude. |
| `scripts/register-task.ps1` | Register the recurring Windows Scheduled Task. |
| `reports/`, `state/` | Runtime output + dedup memory (gitignored). |

## Disclaimer

Provided for educational and personal use. No warranty. You are responsible for complying with the
Terms of Service of Facebook, eBay, Apify, and any other service, and with all applicable laws.
