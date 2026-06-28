#!/usr/bin/env python3
"""
eBay Sold Comps MCP Server

Exposes eBay SOLD-listing resale comps via MCP, as a local drop-in replacement for the
Apify-hosted comp source. Mirrors the Facebook Marketplace MCP's FastMCP design, but runs
over stdio (Claude Code launches it on demand) since it needs no persistent login.

The scan (CLAUDE.md) reads back `median` (resale value M) and `count` (sample size n).
"""

from typing import Optional

from fastmcp import FastMCP

from scraper import scrape_sold_comps_async

mcp = FastMCP("eBay Sold Comps")


@mcp.tool()
async def search_sold_comps(
    query: str,
    condition: Optional[str] = None,
    max_items: int = 240,
) -> dict:
    """
    Get eBay SOLD-listing resale comps for a product, to value a Marketplace find.

    Build `query` from brand + model + key spec (drop filler like "like new", emojis,
    neighborhood names). Pass `condition` when known so the comp matches the listing.

    Args:
        query: Cleaned product query, e.g. "Sony A7 IV body" or "RTX 4090 Founders Edition".
        condition: Optional — one of: new, used, open box, refurbished. Omit if unsure.
        max_items: Results per page to read (max 240).

    Returns:
        dict with:
          median  - median sold price in USD (the resale value M to score against)
          average - mean of outlier-filtered sold prices
          count   - number of USD sold comps found (the confidence sample size n)
          min/max - bounds of the filtered sold prices
          samples - a few example sold listings (title, price, sold_date, url)
          source_url - the eBay sold-search URL used (for manual verification)
        When no USD comps are found, median is null and `note` explains why
        (treat as low-confidence / Review, never a clean Deal).
    """
    return await scrape_sold_comps_async(
        query=query,
        condition=condition,
        max_items=max_items,
    )


if __name__ == "__main__":
    # stdio transport: launched on demand by Claude Code via .mcp.json (no port, no
    # long-running process). Each call drives a short-lived headless Chromium.
    mcp.run()
