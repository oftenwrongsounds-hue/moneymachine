"""
Stream 3 — 7-dimension confidence scoring engine.
Scores each arbitrage opportunity 0-100. Hard vetoes prevent bad buys.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def score_item(item: dict) -> dict:
    """
    Score an item across 7 dimensions. Total max = 100 points.

    Dimensions:
    1. Net profit ($)        — 25 pts
    2. ROI %                 — 20 pts
    3. Sell-through rate     — 20 pts (from eBay sold data)
    4. Data sample size      — 15 pts
    5. Price trend           — 10 pts
    6. Competition           — 5 pts
    7. Shipping weight       — 5 pts

    Hard vetoes (score = 0 regardless of other dimensions):
    - Net profit < $4
    - Sell-through rate < 30%
    """
    buy_price = item.get("buy_price", 0)
    sold_data = item.get("sold_data", {})
    competition = item.get("competition", {})
    weight_lbs = item.get("weight_lbs")

    from stream3_arbitrage.ebay_researcher import (
        calculate_sell_price, calculate_net_profit, estimate_shipping_cost
    )

    sell_price = calculate_sell_price(buy_price, sold_data)
    net_profit = calculate_net_profit(buy_price, sell_price, weight_lbs)
    sample_count = sold_data.get("sample_count", 0)

    # Calculate sell-through rate from sold vs active listings
    sold_count = sold_data.get("sample_count", 0)
    active_count = competition.get("count", 1)
    if sold_count + active_count > 0:
        sell_through = sold_count / (sold_count + active_count)
    else:
        sell_through = 0

    if sell_price > 0 and buy_price > 0:
        roi_pct = ((sell_price - buy_price) / buy_price) * 100
    else:
        roi_pct = 0

    # ─── Hard Vetoes ───────────────────────────────────────────────────────────
    if net_profit < 4:
        return {
            **item,
            "score": 0,
            "sell_price": sell_price,
            "net_profit": net_profit,
            "roi_pct": roi_pct,
            "veto_reason": f"Net profit too low: ${net_profit:.2f} (minimum $4)",
            "dimensions": {},
        }

    if sell_through < 0.30 and sample_count >= 5:
        return {
            **item,
            "score": 0,
            "sell_price": sell_price,
            "net_profit": net_profit,
            "roi_pct": roi_pct,
            "veto_reason": f"Sell-through rate too low: {sell_through:.0%} (minimum 30%)",
            "dimensions": {},
        }

    # ─── Dimension 1: Net profit (25 pts) ──────────────────────────────────────
    if net_profit >= 15:
        d1 = 25
    elif net_profit >= 10:
        d1 = 20
    elif net_profit >= 7:
        d1 = 15
    elif net_profit >= 5:
        d1 = 10
    elif net_profit >= 4:
        d1 = 5
    else:
        d1 = 0

    # ─── Dimension 2: ROI % (20 pts) ───────────────────────────────────────────
    if roi_pct >= 150:
        d2 = 20
    elif roi_pct >= 100:
        d2 = 15
    elif roi_pct >= 50:
        d2 = 10
    elif roi_pct >= 25:
        d2 = 5
    elif roi_pct > 0:
        d2 = 2
    else:
        d2 = 0

    # ─── Dimension 3: Sell-through rate (20 pts) ───────────────────────────────
    if sell_through >= 0.85:
        d3 = 20
    elif sell_through >= 0.70:
        d3 = 15
    elif sell_through >= 0.55:
        d3 = 10
    elif sell_through >= 0.40:
        d3 = 7
    elif sell_through >= 0.30:
        d3 = 4
    else:
        d3 = 0

    # ─── Dimension 4: Data sample size (15 pts) ────────────────────────────────
    if sample_count >= 40:
        d4 = 15
    elif sample_count >= 20:
        d4 = 12
    elif sample_count >= 10:
        d4 = 8
    elif sample_count >= 5:
        d4 = 5
    elif sample_count >= 3:
        d4 = 3
    elif sample_count > 0:
        d4 = 1
    else:
        d4 = 0

    # ─── Dimension 5: Price trend (10 pts) ─────────────────────────────────────
    prices = sold_data.get("prices", [])
    if len(prices) >= 5:
        # Compare first half avg vs second half avg to detect trend
        mid = len(prices) // 2
        first_half_avg = sum(prices[:mid]) / mid
        second_half_avg = sum(prices[mid:]) / (len(prices) - mid)
        trend = (second_half_avg - first_half_avg) / max(first_half_avg, 0.01)
        if trend > 0.05:
            d5 = 10  # Rising
        elif trend > -0.05:
            d5 = 6   # Stable
        elif trend > -0.15:
            d5 = 3   # Slightly falling
        else:
            d5 = 0   # Falling
    else:
        d5 = 5  # Not enough data — neutral

    # ─── Dimension 6: Competition (5 pts) ──────────────────────────────────────
    comp_count = competition.get("count", 0)
    lowest_price = competition.get("lowest_price", 0)

    if comp_count == 0:
        d6 = 5  # No competition
    elif comp_count <= 3:
        d6 = 4
    elif comp_count <= 10:
        d6 = 3
    elif comp_count <= 25:
        d6 = 1
        # Also check if we'd be undercut
        if lowest_price > 0 and sell_price > lowest_price:
            d6 = 0
    else:
        d6 = 0

    # ─── Dimension 7: Shipping weight (5 pts) ──────────────────────────────────
    if weight_lbs is None:
        d7 = 3  # Unknown — assume OK
    elif weight_lbs <= 1:
        d7 = 5
    elif weight_lbs <= 2:
        d7 = 4
    elif weight_lbs <= 5:
        d7 = 2
    elif weight_lbs <= 10:
        d7 = 1
    else:
        d7 = 0

    total_score = d1 + d2 + d3 + d4 + d5 + d6 + d7

    dimensions = {
        "net_profit_pts": d1,
        "roi_pts": d2,
        "sell_through_pts": d3,
        "sample_size_pts": d4,
        "price_trend_pts": d5,
        "competition_pts": d6,
        "shipping_weight_pts": d7,
    }

    return {
        **item,
        "score": total_score,
        "sell_price": sell_price,
        "net_profit": net_profit,
        "roi_pct": round(roi_pct, 1),
        "sell_through": round(sell_through, 2),
        "veto_reason": None,
        "dimensions": dimensions,
    }


def score_batch(items: list, lookup_ebay: bool = True) -> list:
    """
    Score a batch of items.
    If lookup_ebay=True, fetches eBay sold data first.
    Returns items sorted by score descending, filtered to score >= 50.
    """
    if lookup_ebay:
        from stream3_arbitrage.ebay_researcher import get_sold_prices, get_active_competition
        import time

        for item in items:
            try:
                title = item.get("title", "")
                sold_data = get_sold_prices(title)
                competition = get_active_competition(title)
                item["sold_data"] = sold_data
                item["competition"] = competition
                time.sleep(1)  # Rate limiting
            except Exception as e:
                logger.warning(f"eBay lookup failed for '{item.get('title', '')}': {e}")
                item["sold_data"] = {}
                item["competition"] = {}

    scored = []
    for item in items:
        try:
            result = score_item(item)
            scored.append(result)
        except Exception as e:
            logger.warning(f"Score failed for '{item.get('title', '')}': {e}")

    # Sort by score, highest first
    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Return all scored items (filter applied in scanner.py)
    return scored
