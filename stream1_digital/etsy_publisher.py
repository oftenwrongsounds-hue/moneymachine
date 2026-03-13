"""
Stream 1 — Etsy publisher: weekly cross-listing of top Gumroad sellers.
Usage: python stream1_digital/etsy_publisher.py [--dry-run]
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
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client, airtable_logger, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ETSY_API = "https://openapi.etsy.com/v3/application"


def _etsy_auth_headers() -> dict:
    token = os.environ.get("ETSY_ACCESS_TOKEN")
    if not token:
        raise ValueError("ETSY_ACCESS_TOKEN not set — run setup/etsy_auth.py first")
    return {
        "x-api-key": os.environ.get("ETSY_API_KEY", ""),
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def get_shop_id() -> str:
    shop_id = os.environ.get("ETSY_SHOP_ID")
    if not shop_id:
        raise ValueError("ETSY_SHOP_ID not set")
    return shop_id


def get_top_gumroad_products(limit: int = 5) -> list:
    """Get top-selling Gumroad products from Airtable."""
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        records = table.all(
            formula="AND(Status='Published', EtsyListingID='')",
            max_records=limit,
            sort=[{"field": "PublishedAt", "direction": "desc"}],
        )
        return [{"airtable_id": r["id"], **r["fields"]} for r in records]
    except Exception as e:
        logger.warning(f"Could not fetch products: {e}")
        return []


def generate_etsy_listing(product: dict) -> dict:
    """Use Groq to optimize listing copy for Etsy SEO."""
    title = product.get("Title", "")
    niche = product.get("Niche", "")
    description = product.get("Description", "")[:400]

    prompt = f"""Create an Etsy listing for this digital product:
Title: {title}
Niche: {niche}
Description: {description}

Return JSON with:
- title: Etsy listing title (max 140 chars, include primary keywords at start)
- description: Full listing description (400-600 chars, include features, who it's for, what they get)
- tags: List of exactly 13 tags (Etsy maximum, 20 chars each max, no phrases longer than 20 chars)
- materials: List of 3-5 materials/tools (e.g. "Notion", "PDF", "Digital Download")
- price: price in cents (e.g. 1200 for $12)
- quantity: 999

Return only valid JSON."""

    response = groq_client.complete(
        prompt,
        system="You are an Etsy SEO expert. Maximize discoverability through keyword-rich titles and tags.",
        max_tokens=800,
        temperature=0.6,
    )

    clean = response.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(clean)


def create_etsy_listing(product: dict, listing_data: dict) -> str:
    """Create an Etsy listing and return the listing ID."""
    import requests

    shop_id = get_shop_id()
    headers = _etsy_auth_headers()

    payload = {
        "quantity": listing_data.get("quantity", 999),
        "title": listing_data.get("title", product.get("Title", ""))[:140],
        "description": listing_data.get("description", ""),
        "price": listing_data.get("price", 1200),  # cents
        "who_made": "i_did",
        "when_made": "made_to_order",
        "taxonomy_id": 2078,  # Digital downloads category
        "type": "download",
        "is_digital": True,
        "tags": listing_data.get("tags", [])[:13],
        "materials": listing_data.get("materials", ["Notion"]),
        "state": "draft",  # Create as draft first — review before activating
    }

    try:
        resp = requests.post(
            f"{ETSY_API}/shops/{shop_id}/listings",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        listing_id = str(resp.json().get("listing_id", ""))
        logger.info(f"Created Etsy listing: {listing_id}")
        return listing_id
    except requests.exceptions.RequestException as e:
        logger.error(f"Etsy listing creation failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.text[:300]}")
        raise


def run(dry_run: bool = False) -> int:
    """Cross-list top Gumroad products to Etsy."""
    logger.info("Starting Etsy publisher...")

    products = get_top_gumroad_products(limit=3)
    if not products:
        logger.info("No products to cross-list (all already on Etsy or none found)")
        if dry_run:
            print("\nDry run complete. No data published.")
        return 0

    listed = 0
    for product in products:
        try:
            logger.info(f"Generating Etsy listing for: {product.get('Title', '')}")
            listing_data = generate_etsy_listing(product)

            if dry_run:
                print(f"\n[DRY RUN] Would create Etsy listing:")
                print(f"  Title: {listing_data.get('title', '')}")
                print(f"  Tags: {', '.join(listing_data.get('tags', [])[:5])}")
                print(f"  Price: ${listing_data.get('price', 0) / 100:.2f}")
                listed += 1
                continue

            listing_id = create_etsy_listing(product, listing_data)

            # Update Airtable
            try:
                airtable_logger.update_product(
                    product["airtable_id"],
                    {"EtsyListingID": listing_id, "Status": "Cross-listed"},
                )
            except Exception as e:
                logger.warning(f"Could not update Airtable: {e}")

            listed += 1
            time.sleep(3)  # Etsy rate limiting

        except Exception as e:
            logger.error(f"Failed to list '{product.get('Title', '')}': {e}")

    if not dry_run and listed > 0:
        try:
            telegram_bot.send(f"*Etsy publisher:* {listed} products cross-listed (as drafts — activate in Etsy dashboard)")
        except Exception:
            pass
    elif dry_run:
        print(f"\nDry run complete. No data published. ({listed} would be listed)")

    logger.info(f"Etsy publisher: {listed} products listed")
    return listed


def main():
    parser = argparse.ArgumentParser(description="Cross-list top Gumroad products to Etsy")
    parser.add_argument("--dry-run", action="store_true", help="Generate without publishing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
