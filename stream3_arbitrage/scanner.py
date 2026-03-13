"""
Stream 3 — Main arbitrage scanner.
Orchestrates: clearance_scraper → confidence_scorer → Telegram alerts.
scanner.py is declared "already built" in the spec — this is the canonical version.
Usage: python stream3_arbitrage/scanner.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from shared import telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Minimum score threshold for sending Telegram alert
MIN_SCORE = 50

# Max eBay listings per day (suspension prevention)
MAX_LISTINGS_PER_DAY = 15


def get_listings_today() -> int:
    """Check how many eBay listings were created today."""
    state_file = ROOT / "shared" / "ebay_listings_today.json"
    if not state_file.exists():
        return 0
    try:
        with open(state_file) as f:
            data = json.load(f)
        if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return data.get("count", 0)
        return 0
    except Exception:
        return 0


def send_deal_alert(item: dict, dry_run: bool = False) -> str:
    """Send a Telegram approval request for a high-confidence deal."""
    deal_id = str(uuid.uuid4())[:8]
    title = item.get("title", "")
    buy_price = item.get("buy_price", 0)
    sell_price = item.get("sell_price", 0)
    net_profit = item.get("net_profit", 0)
    roi_pct = item.get("roi_pct", 0)
    score = item.get("score", 0)
    source_url = item.get("url", "")
    dims = item.get("dimensions", {})

    msg = (
        f"*Arbitrage Deal Alert — Score: {score}/100*\n\n"
        f"*Item:* {title[:80]}\n"
        f"*Buy:* ${buy_price:.2f}  |  *Sell:* ${sell_price:.2f}\n"
        f"*Net Profit:* ${net_profit:.2f}  |  *ROI:* {roi_pct:.0f}%\n"
        f"*Source:* {source_url[:80]}\n\n"
        f"📊 Breakdown: Profit {dims.get('net_profit_pts',0)}pt | "
        f"ROI {dims.get('roi_pts',0)}pt | "
        f"STR {dims.get('sell_through_pts',0)}pt | "
        f"Data {dims.get('sample_size_pts',0)}pt\n\n"
        f"Tap YES to list on eBay, NO to skip."
    )

    if dry_run:
        print(f"\n[DRY RUN] Deal alert for: {title[:60]}")
        print(f"  Score: {score}/100 | Profit: ${net_profit:.2f} | ROI: {roi_pct:.0f}%")
        return deal_id

    try:
        # Save to Airtable first
        airtable_record = airtable_logger.log_arbitrage_deal(
            title=title,
            buy_price=buy_price,
            sell_price=sell_price,
            confidence_score=score,
            source_url=source_url,
            status="Pending Approval",
            deal_id=deal_id,
            extra_fields={
                "NetProfit": net_profit,
                "ROIPct": roi_pct,
            },
        )

        # Send Telegram approval request
        telegram_bot.send_approval_request(
            message=msg,
            approval_id=f"deal:{deal_id}",
            approve_label="YES — LIST IT",
            skip_label="NO — SKIP",
        )
        logger.info(f"Sent deal alert: {title[:40]} (score {score}, profit ${net_profit:.2f})")
    except Exception as e:
        logger.error(f"Failed to send deal alert: {e}")

    return deal_id


def run(dry_run: bool = False) -> dict:
    """Main scanner run — full pipeline."""
    logger.info("Starting arbitrage scanner...")

    listings_today = get_listings_today()
    if listings_today >= MAX_LISTINGS_PER_DAY:
        logger.warning(f"Daily listing limit reached ({listings_today}/{MAX_LISTINGS_PER_DAY})")
        return {"skipped": "daily_limit_reached", "count": 0}

    # Step 1: Scrape clearance items
    from stream3_arbitrage import clearance_scraper
    logger.info("Scraping clearance items...")
    try:
        items = clearance_scraper.run(dry_run=False)  # Always fetch real data
    except Exception as e:
        logger.error(f"Clearance scraper failed: {e}")
        return {"error": str(e)}

    if not items:
        logger.info("No clearance items found")
        if dry_run:
            print("\nDry run complete. No data published.")
        return {"items": 0, "alerts": 0}

    logger.info(f"Scraped {len(items)} items")

    # Step 2: Score items (clearance_scraper already calls confidence_scorer if not dry_run)
    # Filter high-confidence deals
    qualified = [i for i in items if i.get("score", 0) >= MIN_SCORE and not i.get("veto_reason")]
    logger.info(f"{len(qualified)} items qualify (score >= {MIN_SCORE})")

    if not qualified:
        logger.info("No qualifying deals found today")
        if dry_run:
            # Show best items anyway
            best = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:3]
            print(f"\n[DRY RUN] Best items (all below threshold {MIN_SCORE}):")
            for item in best:
                print(f"  {item.get('title', '')[:60]} — Score: {item.get('score', 0)}, Profit: ${item.get('net_profit', 0):.2f}")
            print("\nDry run complete. No data published.")
        return {"items": len(items), "alerts": 0}

    # Cap alerts per run to avoid Telegram spam
    max_alerts = min(len(qualified), MAX_LISTINGS_PER_DAY - listings_today, 5)
    to_alert = qualified[:max_alerts]

    if dry_run:
        print(f"\n[DRY RUN] Would send {len(to_alert)} deal alerts:")
        for item in to_alert:
            print(f"  {item.get('title', '')[:60]}")
            print(f"    Score: {item.get('score', 0)} | Profit: ${item.get('net_profit', 0):.2f} | ROI: {item.get('roi_pct', 0):.0f}%")
        print("\nDry run complete. No data published.")
        return {"items": len(items), "alerts": len(to_alert)}

    # Step 3: Send alerts
    alerts_sent = 0
    for item in to_alert:
        try:
            send_deal_alert(item, dry_run=dry_run)
            alerts_sent += 1
        except Exception as e:
            logger.error(f"Alert failed: {e}")

    logger.info(f"Scanner complete: {len(items)} items, {len(qualified)} qualified, {alerts_sent} alerts sent")
    return {"items": len(items), "qualified": len(qualified), "alerts": alerts_sent}


def main():
    parser = argparse.ArgumentParser(description="Arbitrage scanner — clearance to eBay pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending alerts")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
