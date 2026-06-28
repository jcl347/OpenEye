"""
Deal scoring — a faithful port of the rules in CLAUDE.md.

    R      = M * (1 - resale_fee_rate) - resale_ship_usd     # net resale proceeds
    profit = R - P
    ratio  = P / M

Gates (effective thresholds = watchlist defaults + per-item overrides):
  - n < min_comp_samples            -> 'low-confidence' (excluded from Deals)
  - ratio <= max_asking_ratio AND profit >= min_profit_usd AND n ok -> 'deal'
  - otherwise                       -> 'skip'

Parts / want-ads / dealer-ads / sold / defective (flagged by the LLM) are never Deals.
"""

from __future__ import annotations

from typing import Any, Optional

DEFAULTS = {
    "days": 7,
    "min_comp_samples": 8,
    "max_asking_ratio": 0.70,
    "min_profit_usd": 100,
    "resale_fee_rate": 0.13,
    "resale_ship_usd": 12,
}


def parse_price_usd(price: Any) -> Optional[float]:
    """Parse a free-text price like '$1,200', 'Free', '$2,350 OBO' into USD float.

    Deliberately not regex: we keep digits and the decimal point and read the first
    number. Returns None for non-USD symbols (£/€/C$) or unparseable/zero values, which
    the caller treats as 'exclude from ranking' per CLAUDE.md.
    """
    if price is None:
        return None
    if isinstance(price, (int, float)):
        return float(price)  # preserve 0.0 (free) — do NOT collapse to None

    s = str(price).strip()
    low = s.lower()
    if low in ("free", "$0", "0", "$0.00"):
        return 0.0
    # Currency sanity check (CLAUDE.md): a correct US location returns '$'.
    if any(sym in s for sym in ("£", "€")) or "c$" in low or "cad" in low:
        return None

    # Read the first run of digits / commas / dot, stop at the next space or word.
    digits: list[str] = []
    started = False
    for ch in s:
        if ch.isdigit() or (ch == "." and started):
            digits.append(ch)
            started = True
        elif ch == ",":
            continue
        elif started:
            break
    if not digits:
        return None
    try:
        return float("".join(digits))  # 0.0 preserved (free)
    except ValueError:
        return None


def effective_thresholds(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    eff = dict(DEFAULTS)
    eff.update(defaults or {})
    for k in DEFAULTS:
        if k in item and item[k] is not None:
            eff[k] = item[k]
    return eff


def score_listing(
    price_usd: Optional[float],
    median: Optional[float],
    n: int,
    eff: dict[str, Any],
    *,
    is_part: bool = False,
    is_wanted_ad: bool = False,
    is_advertisement: bool = False,
) -> dict[str, Any]:
    """Return {verdict, est_profit, ratio, net_resale} for one listing."""
    out: dict[str, Any] = {"est_profit": None, "ratio": None, "net_resale": None}

    if is_part or is_wanted_ad or is_advertisement:
        out["verdict"] = "skip"
        return out
    if price_usd is None or price_usd < 0:   # unparseable / non-USD — exclude (free $0 is kept)
        out["verdict"] = "skip"
        return out
    if not median or median <= 0:
        out["verdict"] = "low-confidence"
        return out

    is_free = price_usd == 0
    net_resale = median * (1 - eff["resale_fee_rate"]) - eff["resale_ship_usd"]
    profit = net_resale - price_usd
    ratio = price_usd / median  # 0.0 for free items
    out.update(
        {"est_profit": round(profit, 2), "ratio": round(ratio, 4), "net_resale": round(net_resale, 2)}
    )

    if n < eff["min_comp_samples"]:
        out["verdict"] = "low-confidence"
    elif is_free:
        # A genuinely free item with solid comps is the best possible deal; the LLM
        # already routes scammy "free" want-ads/ads/sold to skip. Require profit.
        out["verdict"] = "deal" if profit >= eff["min_profit_usd"] else "low-confidence"
    elif ratio <= eff["max_asking_ratio"] and profit >= eff["min_profit_usd"]:
        out["verdict"] = "deal"
    else:
        out["verdict"] = "skip"
    return out
