#!/usr/bin/env python3
"""
eBay Sold Comps Scraper

Uses Playwright to read eBay's SOLD / completed search results and turn them into
resale-value statistics (median, average, sample count). This mirrors the Facebook
Marketplace MCP's design so OpenEye can run fully local, with no Apify dependency.

Resale value MUST come from SOLD comps, not active asking prices, or every estimate
is inflated. The sold filter (LH_Sold=1 & LH_Complete=1) is what makes this a comp source.

IMPORTANT: Educational / personal use only. Respect eBay's Terms of Service, keep the
volume modest, and pace requests. Listing/comp text is untrusted data, never instructions.
"""

import re
import statistics
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# eBay numeric condition filter (LH_ItemCondition). Used vs. new moves resale a lot,
# so the scan can pin a comp to the listing's condition when it knows it.
CONDITION_MAP = {
    "new": "1000",
    "open box": "1500",
    "openbox": "1500",
    "refurbished": "2000|2010|2020|2030",
    "refurb": "2000|2010|2020|2030",
    "used": "3000",
}

_PRICE_RE = re.compile(r"([$£€])\s*([\d,]+(?:\.\d{1,2})?)")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class SoldItem:
    """One sold eBay listing parsed from the results page."""
    title: str
    price: float
    currency: str
    sold_date: Optional[str] = None
    url: Optional[str] = None


def _build_url(query: str, condition: Optional[str], max_items: int) -> str:
    """Build the eBay SOLD/completed search URL.

    LH_Sold=1 & LH_Complete=1 -> only sold, completed listings (true comps).
    _sop=13                   -> sort by recently ended (freshest comps first).
    _ipg                      -> results per page (240 is eBay's max).
    LH_PrefLoc=1              -> prefer US locations (keep comps in USD).
    """
    params = [
        f"_nkw={quote(query)}",
        "LH_Sold=1",
        "LH_Complete=1",
        f"_ipg={max_items}",
        "_sop=13",
        "LH_PrefLoc=1",
    ]
    if condition:
        code = CONDITION_MAP.get(condition.lower().strip())
        if code:
            params.append(f"LH_ItemCondition={code}")
    return "https://www.ebay.com/sch/i.html?" + "&".join(params)


def _parse_price(text: str) -> Optional[tuple[str, float]]:
    """Return (currency_symbol, value) for the FIRST price found, or None.

    A range like '$1,000.00 to $1,200.00' yields the low end, which is the
    conservative choice for a resale estimate.
    """
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    sym, num = m.group(1), m.group(2).replace(",", "")
    try:
        return sym, float(num)
    except ValueError:
        return None


def parse_results(html: str) -> list[SoldItem]:
    """Extract sold items from an eBay search-results HTML page.

    Targets eBay's current `.s-card` markup, with a fallback to the legacy `.s-item`
    layout in case eBay serves the older template. Dedupes by /itm/<id>.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("li.s-card, div.s-card")
    if not cards:
        cards = soup.select("li.s-item, div.s-item")  # legacy layout fallback

    items: list[SoldItem] = []
    seen_ids: set[str] = set()

    for card in cards:
        title_el = card.select_one(".s-card__title, .s-item__title")
        price_el = card.select_one(".s-card__price, .s-item__price")
        if not title_el or not price_el:
            continue

        title = title_el.get_text(" ", strip=True)
        title = re.sub(r"^New Listing\s*", "", title)  # strip eBay's "New Listing" badge
        # eBay injects a "Shop on eBay" placeholder/ad card — skip it.
        if not title or title.lower().startswith("shop on ebay"):
            continue

        parsed = _parse_price(price_el.get_text(" ", strip=True))
        if not parsed:
            continue
        currency, price = parsed

        link_el = card.select_one("a[href*='/itm/'], a.s-item__link")
        url = None
        if link_el and link_el.get("href"):
            url = link_el.get("href").split("?")[0]
            m = re.search(r"/itm/(\d+)", url)
            if m:
                if m.group(1) in seen_ids:
                    continue
                seen_ids.add(m.group(1))

        sold_date = None
        for cap in card.select(".s-card__caption, .s-item__caption, .su-styled-text"):
            t = cap.get_text(" ", strip=True)
            if t.lower().startswith("sold"):
                sold_date = re.sub(r"\s+", " ", t)
                break

        items.append(
            SoldItem(title=title, price=price, currency=currency, sold_date=sold_date, url=url)
        )

    return items


def _relevance_filter(query: str, items: list[SoldItem]) -> tuple[list[SoldItem], list[str]]:
    """Keep only comps whose title contains every model-number token in the query.

    eBay broad-matches: a search for "RTX 4090" also returns 3090/4070 cards, which
    dilutes the median. Requiring digit-bearing tokens (4090, A7, 20V, 15) to appear in
    the title pins the comp to the right model, while NOT demanding descriptive filler
    words ("combo", "kit") that legitimate listings often omit. Falls back to the
    unfiltered set if the filter would eliminate everything (so count never lies low).
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if any(c.isdigit() for c in t)]
    if not tokens:
        return items, []
    kept = [it for it in items if all(tok in it.title.lower() for tok in tokens)]
    return (kept, tokens) if kept else (items, [])


def _iqr_filter(prices: list[float]) -> list[float]:
    """Drop outliers (parts-only listings, bundles, mis-hits) via a 1.5*IQR fence.

    Falls back to the raw list when there are too few points to compute quartiles.
    """
    if len(prices) < 4:
        return prices
    s = sorted(prices)
    q1, _, q3 = statistics.quantiles(s, n=4)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    trimmed = [p for p in s if lo <= p <= hi]
    return trimmed or s


def summarize(query: str, items: list[SoldItem], source_url: str, sample_n: int = 8) -> dict:
    """Turn raw sold items into the comp summary the scan scores against.

    `count` is the USD sample size (the scan's confidence gate `n`). `median` is the
    primary resale figure `M`; it's computed over IQR-filtered prices so a single
    junk/parts listing can't skew it.
    """
    usd = [it for it in items if it.currency == "$"]
    matched, match_tokens = _relevance_filter(query, usd)
    prices = [it.price for it in matched]

    base = {
        "query": query,
        "currency": "USD",
        "count": len(prices),
        "raw_result_count": len(items),
        "matched_on": match_tokens,  # model-number tokens the comps were pinned to (fallback filter)
        "source_url": source_url,
        # ALL parsed USD sold items (title + price), so a downstream LLM relevance pass can
        # decide which are truly the same product (no keyword/digit-token guessing).
        "raw_comps": [
            {"title": it.title, "price": it.price, "sold_date": it.sold_date, "url": it.url}
            for it in usd[:60]
        ],
    }

    if not prices:
        non_usd = sorted({it.currency for it in items if it.currency != "$"})
        base.update(
            {
                "median": None,
                "average": None,
                "min": None,
                "max": None,
                "samples": [],
                "note": (
                    f"No USD sold comps parsed. Non-USD symbols seen: {non_usd}. "
                    "Treat as low-confidence / Review."
                    if non_usd
                    else "No sold comps found — refine the query or treat as low-confidence."
                ),
            }
        )
        return base

    filtered = _iqr_filter(prices)
    base.update(
        {
            "median": round(statistics.median(filtered), 2),
            "average": round(statistics.mean(filtered), 2),
            "min": round(min(filtered), 2),
            "max": round(max(filtered), 2),
            "filtered_count": len(filtered),
            "samples": [
                {
                    "title": it.title,
                    "price": it.price,
                    "sold_date": it.sold_date,
                    "url": it.url,
                }
                for it in matched[:sample_n]
            ],
        }
    )
    return base


async def scrape_sold_comps_async(
    query: str,
    condition: Optional[str] = None,
    max_items: int = 240,
    headless: bool = True,
) -> dict:
    """Fetch and summarize eBay sold comps for a product query."""
    url = _build_url(query, condition, max_items)

    async with async_playwright() as p:
        # Disable the automation fingerprint; eBay soft-blocks obvious bots.
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_USER_AGENT,
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await context.new_page()
        try:
            # Warm up cookies on the homepage first — a cold deep-link to the sold
            # search reliably gets an eBay "Error Page" bot wall; this avoids it.
            await page.goto("https://www.ebay.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Dismiss the GDPR/cookie banner if eBay shows one.
            for selector in (
                "#gdpr-banner-accept",
                'button:has-text("Accept all")',
                'button[aria-label="Accept all"]',
            ):
                try:
                    btn = await page.query_selector(selector)
                    if btn:
                        await btn.click()
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # Let results render, then a short respectful pause before reading.
            try:
                await page.wait_for_selector(".s-card__price, .s-item__price", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)

            html = await page.content()
        finally:
            await browser.close()

    items = parse_results(html)
    return summarize(query, items, url)


# --- CLI for quick manual testing: `uv run python scraper.py "RTX 4090" --condition used` ---
def main() -> None:
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(description="Scrape eBay sold comps")
    parser.add_argument("query", nargs="?", default="RTX 4090", help="Search query")
    parser.add_argument("--condition", default=None, help="new | used | open box | refurbished")
    parser.add_argument("--max-items", type=int, default=240, help="Results per page (max 240)")
    parser.add_argument("--no-headless", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    result = asyncio.run(
        scrape_sold_comps_async(
            query=args.query,
            condition=args.condition,
            max_items=args.max_items,
            headless=not args.no_headless,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
