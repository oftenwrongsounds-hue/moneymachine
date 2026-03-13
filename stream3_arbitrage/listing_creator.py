"""
Stream 3 — eBay listing creator.
Creates draft eBay listings for approved arbitrage deals.
Triggered by Telegram YES approval via Make.com webhook or GitHub Actions dispatch.
Usage: python stream3_arbitrage/listing_creator.py --deal-id DEAL_ID [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client, telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EBAY_TRADING_API = "https://api.ebay.com/ws/api.dll"

# eBay category IDs for common clearance items
CATEGORY_MAP = {
    "toys": "220",
    "electronics": "58058",
    "sports": "888",
    "clothing": "11450",
    "home": "11700",
    "books": "267",
    "default": "99",  # Everything else
}


def _ebay_headers(call_name: str) -> dict:
    token = os.environ.get("EBAY_USER_TOKEN")
    app_id = os.environ.get("EBAY_APP_ID")
    dev_id = os.environ.get("EBAY_DEV_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")

    if not all([token, app_id, dev_id, cert_id]):
        raise ValueError("Missing eBay credentials. Run setup/ebay_auth.py first.")

    return {
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-APP-NAME": app_id,
        "X-EBAY-API-DEV-NAME": dev_id,
        "X-EBAY-API-CERT-NAME": cert_id,
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }


def generate_listing_content(item: dict) -> dict:
    """Use Groq to write compelling eBay listing title and description."""
    title = item.get("title", "")
    buy_price = item.get("buy_price", 0)
    sell_price = item.get("sell_price", buy_price * 2)
    category = item.get("category", "")

    prompt = f"""Write an eBay listing for this item:

Item: {title}
Category: {category}
Our sell price: ${sell_price}

Create:
1. eBay title (max 80 chars, keyword-rich, no ALL CAPS, no special chars except hyphens)
2. Description (150-250 words: condition, features, what's included, shipping info)

Return JSON with keys: title, description"""

    try:
        response = groq_client.complete(
            prompt,
            system="You are an expert eBay seller. Write listings that rank well in eBay search and convert to sales.",
            max_tokens=600,
            temperature=0.6,
        )
        clean = response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(clean)
    except Exception as e:
        logger.warning(f"AI listing generation failed: {e} — using defaults")
        return {
            "title": title[:80],
            "description": f"{title}\n\nCondition: New (clearance item)\nFast shipping. Contact with any questions.",
        }


def detect_category(title: str) -> str:
    """Detect eBay category from item title."""
    lower = title.lower()
    if any(w in lower for w in ["toy", "game", "lego", "puzzle", "doll", "action figure"]):
        return "220"
    if any(w in lower for w in ["electronic", "headphone", "speaker", "tablet", "phone"]):
        return "58058"
    if any(w in lower for w in ["sport", "fitness", "exercise", "outdoor", "camping"]):
        return "888"
    if any(w in lower for w in ["shirt", "pants", "dress", "shoe", "clothing", "apparel"]):
        return "11450"
    if any(w in lower for w in ["kitchen", "home", "decor", "furniture", "bedding"]):
        return "11700"
    if any(w in lower for w in ["book", "novel", "textbook"]):
        return "267"
    return "99"


def create_ebay_listing_xml(item: dict, listing_content: dict) -> str:
    """Build eBay AddItem XML request."""
    title = listing_content.get("title", item.get("title", ""))[:80]
    description = listing_content.get("description", "")
    sell_price = item.get("sell_price", item.get("buy_price", 10) * 2)
    category_id = detect_category(item.get("title", ""))
    image_url = item.get("image_url", "")

    picture_xml = ""
    if image_url:
        picture_xml = f"""
        <PictureDetails>
            <PictureURL>{image_url}</PictureURL>
        </PictureDetails>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{os.environ.get('EBAY_USER_TOKEN', '')}</eBayAuthToken>
    </RequesterCredentials>
    <Item>
        <Title>{title}</Title>
        <Description><![CDATA[{description}]]></Description>
        <PrimaryCategory>
            <CategoryID>{category_id}</CategoryID>
        </PrimaryCategory>
        <StartPrice>{sell_price}</StartPrice>
        <ConditionID>1000</ConditionID>
        <Country>US</Country>
        <Currency>USD</Currency>
        <DispatchTimeMax>3</DispatchTimeMax>
        <ListingDuration>GTC</ListingDuration>
        <ListingType>FixedPriceItem</ListingType>
        <PaymentMethods>PayPal</PaymentMethods>
        <PayPalEmailAddress>{os.environ.get('PAYPAL_EMAIL', 'your@email.com')}</PayPalEmailAddress>
        <Quantity>1</Quantity>
        <ShipToLocations>US</ShipToLocations>
        <ShippingDetails>
            <ShippingType>Flat</ShippingType>
            <ShippingServiceOptions>
                <ShippingServicePriority>1</ShippingServicePriority>
                <ShippingService>USPSFirstClass</ShippingService>
                <ShippingServiceCost>0</ShippingServiceCost>
                <FreeShipping>true</FreeShipping>
            </ShippingServiceOptions>
        </ShippingDetails>
        <ReturnPolicy>
            <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
            <RefundOption>MoneyBack</RefundOption>
            <ReturnsWithinOption>Days_30</ReturnsWithinOption>
            <ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>
        </ReturnPolicy>
        {picture_xml}
    </Item>
</AddItemRequest>"""


def create_listing(item: dict, dry_run: bool = False) -> dict:
    """
    Create an eBay listing for an approved deal.
    Returns listing URL and item ID.
    """
    import requests

    listing_content = generate_listing_content(item)
    xml_request = create_ebay_listing_xml(item, listing_content)

    if dry_run:
        return {
            "dry_run": True,
            "title": listing_content.get("title", ""),
            "sell_price": item.get("sell_price", 0),
            "xml_preview": xml_request[:500],
        }

    try:
        headers = _ebay_headers("AddItem")
        resp = requests.post(
            EBAY_TRADING_API,
            headers=headers,
            data=xml_request.encode("utf-8"),
            timeout=30,
        )
        resp.raise_for_status()

        # Parse response XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.content)
        ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}

        ack = root.findtext("ns:Ack", "", namespaces=ns)
        item_id = root.findtext("ns:ItemID", "", namespaces=ns)
        fees = root.find("ns:Fees", namespaces=ns)

        if ack not in ("Success", "Warning"):
            errors = root.findall("ns:Errors", namespaces=ns)
            error_msgs = [e.findtext("ns:LongMessage", "", namespaces=ns) for e in errors]
            raise RuntimeError(f"eBay AddItem failed: {'; '.join(error_msgs)}")

        listing_url = f"https://www.ebay.com/itm/{item_id}"
        logger.info(f"Created eBay listing: {listing_url}")

        return {
            "item_id": item_id,
            "listing_url": listing_url,
            "title": listing_content.get("title", ""),
            "sell_price": item.get("sell_price", 0),
        }

    except Exception as e:
        logger.error(f"eBay listing creation failed: {e}")
        raise


def run(deal_id: str = None, dry_run: bool = False) -> dict:
    """
    Create a listing for a deal.
    If deal_id provided, fetches from Airtable. Otherwise uses last approved deal.
    """
    logger.info(f"Starting listing creator (deal_id={deal_id})...")

    item = None

    if deal_id:
        # Fetch deal from Airtable
        try:
            from pyairtable import Api
            api = Api(os.environ.get("AIRTABLE_API_KEY"))
            table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Arbitrage_Deals")
            records = table.all(formula=f"DealID='{deal_id}'", max_records=1)
            if records:
                fields = records[0]["fields"]
                item = {
                    "title": fields.get("Title", ""),
                    "buy_price": fields.get("BuyPrice", 0),
                    "sell_price": fields.get("SellPrice", 0),
                    "url": fields.get("SourceURL", ""),
                    "airtable_id": records[0]["id"],
                }
        except Exception as e:
            logger.error(f"Could not fetch deal from Airtable: {e}")

    if not item:
        logger.warning(f"No deal found for id {deal_id}")
        return {"error": "Deal not found"}

    try:
        result = create_listing(item, dry_run=dry_run)

        if not dry_run:
            # Update Airtable status
            if item.get("airtable_id"):
                airtable_logger.update_arbitrage_deal_status(
                    item["airtable_id"], "Listed on eBay"
                )

            # Send Telegram confirmation
            telegram_bot.send(
                f"*eBay Listing Created!*\n\n"
                f"*Title:* {result.get('title', '')[:60]}\n"
                f"*Sell Price:* ${result.get('sell_price', 0)}\n"
                f"*URL:* {result.get('listing_url', '')}"
            )

        if dry_run:
            print(f"\n[DRY RUN] Would create eBay listing:")
            print(f"  Title: {result.get('title', '')}")
            print(f"  Sell Price: ${result.get('sell_price', 0)}")
            print("\nDry run complete. No data published.")

        return result

    except Exception as e:
        logger.error(f"Listing creation failed: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Create eBay listing for approved deal")
    parser.add_argument("--deal-id", type=str, help="Deal ID from Airtable")
    parser.add_argument("--dry-run", action="store_true", help="Generate without publishing")
    args = parser.parse_args()

    run(deal_id=args.deal_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
