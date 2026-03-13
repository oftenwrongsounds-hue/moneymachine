"""
Stream 3 — eBay sold price researcher.
Looks up recently sold prices for items to calculate ROI.
"""
import os
import sys
import logging
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests

logger = logging.getLogger(__name__)

EBAY_FINDING_API = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_BROWSE_API = "https://api.ebay.com/buy/browse/v1"


def get_sold_prices(title: str, max_results: int = 40) -> dict:
    """
    Look up recently sold eBay prices for an item.
    Returns dict with avg_price, median_price, sample_count, min_price, max_price.
    """
    app_id = os.environ.get("EBAY_APP_ID")
    if not app_id:
        raise ValueError("EBAY_APP_ID not set")

    # Clean title for search
    search_terms = _clean_search_terms(title)

    try:
        # Use Finding API for completed/sold listings
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "keywords": search_terms,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "itemFilter(1).name": "ListingType",
            "itemFilter(1).value": "FixedPrice",
            "sortOrder": "EndTimeSoonest",
            "paginationInput.entriesPerPage": str(min(max_results, 100)),
        }

        resp = requests.get(EBAY_FINDING_API, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        search_result = (
            data.get("findCompletedItemsResponse", [{}])[0]
            .get("searchResult", [{}])[0]
        )
        items = search_result.get("item", [])

        prices = []
        for item in items:
            try:
                selling_status = item.get("sellingStatus", [{}])[0]
                price = selling_status.get("currentPrice", [{}])[0].get("__value__", "0")
                prices.append(float(price))
            except (KeyError, IndexError, ValueError):
                continue

        if not prices:
            return {"avg_price": 0, "median_price": 0, "sample_count": 0, "min_price": 0, "max_price": 0}

        prices.sort()
        avg = sum(prices) / len(prices)
        median = prices[len(prices) // 2]

        return {
            "avg_price": round(avg, 2),
            "median_price": round(median, 2),
            "sample_count": len(prices),
            "min_price": round(min(prices), 2),
            "max_price": round(max(prices), 2),
            "prices": prices[:20],  # Sample for trend analysis
        }

    except Exception as e:
        logger.error(f"eBay sold price lookup failed for '{title}': {e}")
        return {"avg_price": 0, "median_price": 0, "sample_count": 0, "min_price": 0, "max_price": 0}


def get_active_competition(title: str) -> dict:
    """Check how many active eBay listings exist and at what prices."""
    app_id = os.environ.get("EBAY_APP_ID")
    if not app_id:
        return {"count": 0, "lowest_price": 0}

    search_terms = _clean_search_terms(title)

    try:
        params = {
            "OPERATION-NAME": "findItemsByKeywords",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "keywords": search_terms,
            "itemFilter(0).name": "ListingType",
            "itemFilter(0).value": "FixedPrice",
            "sortOrder": "PricePlusShippingLowest",
            "paginationInput.entriesPerPage": "10",
        }

        resp = requests.get(EBAY_FINDING_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        search_result = (
            data.get("findItemsByKeywordsResponse", [{}])[0]
            .get("searchResult", [{}])[0]
        )
        items = search_result.get("item", [])
        count = int(search_result.get("@count", 0))

        prices = []
        for item in items[:5]:
            try:
                price = (
                    item.get("sellingStatus", [{}])[0]
                    .get("currentPrice", [{}])[0]
                    .get("__value__", "0")
                )
                prices.append(float(price))
            except Exception:
                continue

        return {
            "count": count,
            "lowest_price": round(min(prices), 2) if prices else 0,
            "prices": prices,
        }

    except Exception as e:
        logger.warning(f"eBay competition check failed: {e}")
        return {"count": 0, "lowest_price": 0}


def calculate_sell_price(buy_price: float, sold_data: dict) -> float:
    """
    Calculate optimal sell price.
    Uses median sold price minus 5% to undercut slightly and ensure quick sale.
    """
    median = sold_data.get("median_price", 0)
    avg = sold_data.get("avg_price", 0)

    if not median and not avg:
        return 0

    # Use median if available, otherwise avg
    base_price = median if median > 0 else avg

    # Target just below median for faster sales
    return round(base_price * 0.95, 2)


def _clean_search_terms(title: str) -> str:
    """Clean product title for eBay search — remove store-specific text."""
    import re
    # Remove common clearance-specific terms
    stopwords = [
        "clearance", "sale", "new with tags", "nwt", "brand new", "lot of",
        "set of", "pack of", "bundle", "assorted", "various", "mixed",
    ]
    clean = title.lower()
    for word in stopwords:
        clean = clean.replace(word, "")

    # Remove special characters except spaces and hyphens
    clean = re.sub(r"[^a-z0-9\s\-]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Cap at 3-4 key words for better eBay search results
    words = clean.split()[:5]
    return " ".join(words)


def estimate_shipping_cost(weight_lbs: float = None) -> float:
    """Estimate shipping cost based on weight."""
    if weight_lbs is None:
        return 4.50  # Default estimate
    if weight_lbs < 1:
        return 3.50
    elif weight_lbs < 2:
        return 4.50
    elif weight_lbs < 5:
        return 7.00
    elif weight_lbs < 10:
        return 12.00
    else:
        return 18.00


def calculate_net_profit(buy_price: float, sell_price: float, weight_lbs: float = None) -> float:
    """Calculate net profit after eBay fees and shipping."""
    if sell_price <= 0:
        return 0
    ebay_fee = sell_price * 0.1325  # ~13.25% total fees
    shipping = estimate_shipping_cost(weight_lbs)
    net = sell_price - buy_price - ebay_fee - shipping
    return round(net, 2)
