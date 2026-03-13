"""
Stream 3 — Clearance scraper: Target + Walmart clearance via Apify (BeautifulSoup fallback).
Feeds deals into confidence_scorer.py then listing_creator.py.
Usage: python stream3_arbitrage/clearance_scraper.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"

# Target clearance categories to scan
TARGET_CATEGORIES = [
    "https://www.target.com/c/clearance/-/N-5q0e3?Ntk=All&type=products&moveTo=product-list-grid",
    "https://www.target.com/c/toys-clearance/-/N-55iqz",
    "https://www.target.com/c/sports-outdoors-clearance/-/N-4hpxk",
]

# Walmart clearance URLs
WALMART_CATEGORIES = [
    "https://www.walmart.com/cp/clearance/1101692",
    "https://www.walmart.com/shop/clearance-items/toys",
]


def get_apify_actor_id(actor_key: str) -> str:
    """Get actor ID from saved config, or use default."""
    config_path = ROOT / "shared" / "apify_actors.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                actors = json.load(f)
            return actors.get(actor_key, {}).get("id", "")
        except Exception:
            pass
    # Known good default actors
    defaults = {
        "target_clearance": "apify/target-scraper",
        "walmart_clearance": "apify/walmart-scraper",
    }
    return defaults.get(actor_key, "")


def scrape_with_apify(actor_id: str, input_data: dict, timeout_secs: int = 120) -> list:
    """Run an Apify actor and return results."""
    import requests

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ValueError("APIFY_TOKEN not set")

    headers = {"Content-Type": "application/json"}

    # Start actor run
    try:
        resp = requests.post(
            f"{APIFY_API}/acts/{actor_id}/runs",
            params={"token": token},
            json=input_data,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        run_id = resp.json().get("data", {}).get("id")
        if not run_id:
            raise RuntimeError("No run ID from Apify")
    except Exception as e:
        raise RuntimeError(f"Failed to start Apify actor {actor_id}: {e}")

    # Poll for completion
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        try:
            status_resp = requests.get(
                f"{APIFY_API}/acts/{actor_id}/runs/{run_id}",
                params={"token": token},
                timeout=15,
            )
            status = status_resp.json().get("data", {}).get("status", "")
            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run {status}: {run_id}")
        except Exception as e:
            logger.warning(f"Apify status check error: {e}")

        time.sleep(10)

    # Get results
    try:
        results_resp = requests.get(
            f"{APIFY_API}/acts/{actor_id}/runs/{run_id}/dataset/items",
            params={"token": token, "format": "json"},
            timeout=30,
        )
        results_resp.raise_for_status()
        return results_resp.json()
    except Exception as e:
        raise RuntimeError(f"Failed to get Apify results: {e}")


def scrape_target_clearance_apify() -> list:
    """Scrape Target clearance using Apify."""
    actor_id = get_apify_actor_id("target_clearance")
    if not actor_id:
        logger.warning("No Target clearance Apify actor configured")
        return []

    input_data = {
        "startUrls": [{"url": url} for url in TARGET_CATEGORIES],
        "maxRequestsPerCrawl": 200,
        "filterByPrice": {"min": 3, "max": 30},
    }

    try:
        raw = scrape_with_apify(actor_id, input_data)
        items = []
        for item in raw:
            price = item.get("price", item.get("regularPrice", 0))
            original_price = item.get("originalPrice", item.get("listPrice", 0))
            if not price:
                continue

            items.append({
                "source": "target",
                "title": item.get("name", item.get("title", ""))[:200],
                "buy_price": float(str(price).replace("$", "").replace(",", "")),
                "original_price": float(str(original_price).replace("$", "").replace(",", "")) if original_price else 0,
                "url": item.get("url", item.get("productUrl", "")),
                "category": item.get("category", ""),
                "upc": item.get("upc", item.get("productId", "")),
                "image_url": item.get("image", item.get("thumbnail", "")),
            })
        return items
    except Exception as e:
        logger.warning(f"Target Apify scrape failed: {e} — falling back to BeautifulSoup")
        return _scrape_target_bs()


def _scrape_target_bs() -> list:
    """BeautifulSoup fallback for Target clearance."""
    from bs4 import BeautifulSoup
    import requests

    items = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    for url in TARGET_CATEGORIES[:1]:
        try:
            time.sleep(10 + __import__("random").uniform(2, 8))  # Randomized delay
            resp = requests.get(url, headers=headers, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Target product cards
            cards = soup.find_all(attrs={"data-test": "product-details"}) or \
                    soup.find_all("div", class_=lambda c: c and "ProductCardWrapper" in c)

            for card in cards[:20]:
                title_el = card.find(attrs={"data-test": "product-title"}) or card.find("a", attrs={"data-test": True})
                price_el = card.find(attrs={"data-test": "product-price"}) or card.find(class_=lambda c: c and "price" in c.lower())

                if title_el and price_el:
                    import re
                    price_text = price_el.get_text(strip=True)
                    price_match = re.search(r"[\d.]+", price_text)
                    if price_match:
                        items.append({
                            "source": "target_bs",
                            "title": title_el.get_text(strip=True)[:200],
                            "buy_price": float(price_match.group()),
                            "original_price": 0,
                            "url": url,
                            "category": "",
                            "upc": "",
                            "image_url": "",
                        })

        except Exception as e:
            logger.warning(f"Target BS scrape failed: {e}")

    return items


def scrape_walmart_clearance_apify() -> list:
    """Scrape Walmart clearance using Apify."""
    actor_id = get_apify_actor_id("walmart_clearance")
    if not actor_id:
        logger.warning("No Walmart clearance Apify actor configured")
        return _scrape_walmart_bs()

    input_data = {
        "startUrls": [{"url": url} for url in WALMART_CATEGORIES],
        "maxRequestsPerCrawl": 200,
    }

    try:
        raw = scrape_with_apify(actor_id, input_data)
        items = []
        for item in raw:
            price = item.get("price", item.get("salePrice", 0))
            if not price:
                continue
            items.append({
                "source": "walmart",
                "title": item.get("name", item.get("title", ""))[:200],
                "buy_price": float(str(price).replace("$", "").replace(",", "")),
                "original_price": item.get("wasPrice", item.get("originalPrice", 0)),
                "url": item.get("url", item.get("productUrl", "")),
                "category": item.get("category", ""),
                "upc": item.get("upc", ""),
                "image_url": item.get("thumbnailImage", item.get("image", "")),
            })
        return items
    except Exception as e:
        logger.warning(f"Walmart Apify scrape failed: {e} — falling back")
        return _scrape_walmart_bs()


def _scrape_walmart_bs() -> list:
    """BeautifulSoup fallback for Walmart clearance."""
    from bs4 import BeautifulSoup
    import requests
    import re, random

    items = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for url in WALMART_CATEGORIES[:1]:
        try:
            time.sleep(10 + random.uniform(2, 8))
            resp = requests.get(url, headers=headers, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try to extract product data from JSON-LD
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts[:5]:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        for d in data:
                            if d.get("@type") == "Product":
                                offers = d.get("offers", {})
                                price = offers.get("price", 0)
                                if price:
                                    items.append({
                                        "source": "walmart_bs",
                                        "title": d.get("name", "")[:200],
                                        "buy_price": float(price),
                                        "original_price": 0,
                                        "url": d.get("url", url),
                                        "category": "",
                                        "upc": d.get("sku", ""),
                                        "image_url": d.get("image", ""),
                                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"Walmart BS scrape failed: {e}")

    return items


def run(dry_run: bool = False) -> list:
    """Main clearance scraper run."""
    logger.info("Starting clearance scraper...")

    all_items = []

    logger.info("Scraping Target clearance...")
    target_items = scrape_target_clearance_apify()
    logger.info(f"Target: {len(target_items)} items")
    all_items.extend(target_items)

    logger.info("Scraping Walmart clearance...")
    walmart_items = scrape_walmart_clearance_apify()
    logger.info(f"Walmart: {len(walmart_items)} items")
    all_items.extend(walmart_items)

    # Filter: only items between $3-$30 buy price
    all_items = [i for i in all_items if 3 <= i.get("buy_price", 0) <= 30]
    logger.info(f"Total items in price range: {len(all_items)}")

    if dry_run:
        print(f"\n[DRY RUN] Found {len(all_items)} clearance items:")
        for item in all_items[:5]:
            print(f"  [{item['source']}] {item['title'][:60]} — ${item['buy_price']}")
        print("\nDry run complete. No data published.")
        return all_items

    # Pass to confidence scorer
    if all_items:
        try:
            from stream3_arbitrage import confidence_scorer
            scored = confidence_scorer.score_batch(all_items)
            logger.info(f"Scored {len(scored)} items")
            return scored
        except Exception as e:
            logger.error(f"Confidence scorer failed: {e}")

    return all_items


def main():
    parser = argparse.ArgumentParser(description="Scrape clearance items from Target and Walmart")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without publishing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
