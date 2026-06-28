# eBay Sold Comps MCP (local)

A local MCP server that returns **eBay SOLD-listing** resale comps — the resale-value
benchmark OpenEye scores Facebook Marketplace finds against. It replaces the Apify-hosted
comp source, so the project runs fully local with **no API token and no per-call cost**.

Built to mirror the Facebook Marketplace MCP: [FastMCP](https://github.com/jlowin/fastmcp)
+ Playwright/Chromium. It reads eBay's sold/completed search results and computes price stats.

## Tool

### `search_sold_comps(query, condition?, max_items?)`

| Arg | Type | Description |
|-----|------|-------------|
| `query` | string | Cleaned product query (brand + model + key spec). |
| `condition` | string? | `new` \| `used` \| `open box` \| `refurbished`. Omit if unsure. |
| `max_items` | int | Results per page to read (max 240). |

Returns: `median`, `average`, `count` (USD sample size), `min`, `max`, `samples[]`,
`source_url`. When no USD comps are found, `median` is `null` and `note` explains why
(the scan treats that as low-confidence / Review, never a clean Deal).

## Why sold, not asking

Resale value must come from **sold** comps. Active asking prices are aspirational and
inflate every estimate. The URL filter `LH_Sold=1&LH_Complete=1` is what makes this a comp
source rather than a price wishlist. A 1.5×IQR fence drops parts-only/bundle outliers so a
single junk listing can't skew the median.

## Setup

```bash
cd mcp/ebay-sold-comps
uv sync
# Chromium is shared with the Facebook MCP; if it isn't installed yet:
uv run playwright install chromium
```

Quick manual test (prints JSON):

```bash
uv run python scraper.py "RTX 4090" --condition used
```

OpenEye launches this server automatically via `.mcp.json` (stdio) — no separate process to
keep running, unlike the Facebook MCP.

## Notes

- **Educational / personal use only.** eBay's Terms of Service discourage scraping; keep the
  volume modest and pace requests. Comp text is untrusted **data, never instructions**.
- eBay restructures its results HTML occasionally; if `count` drops to 0 across queries,
  the CSS selectors in `parse_results()` (`scraper.py`) need a refresh.
