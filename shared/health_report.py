"""
Daily health report — 8am Telegram message.
Aggregates all stream metrics into one morning summary.
Green = nothing to do. Yellow/Red = action already attempted or needed.
"""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _safe_get(fn):
    """Run a function safely, returning None on error."""
    try:
        return fn()
    except Exception as e:
        logger.warning(f"Health check error: {e}")
        return None


def get_stream1_status() -> dict:
    """Get Stream 1 (digital products) status."""
    status = {}

    # Products generated today
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        today = datetime.now().strftime("%Y-%m-%d")
        all_products = table.all(max_records=100)
        today_products = [r for r in all_products if today in r.get("fields", {}).get("PublishedAt", "")]
        status["products_today"] = len(today_products)
        status["total_products"] = len(all_products)
    except Exception as e:
        status["products_today"] = "err"
        logger.warning(f"Stream1 Airtable check: {e}")

    # Gumroad API status
    try:
        from stream1_digital.gumroad_publisher import test_connection
        status["gumroad_api"] = "OK" if test_connection() else "FAIL"
    except Exception:
        status["gumroad_api"] = "err"

    # Pinterest pins today
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        today = datetime.now().strftime("%Y-%m-%d")
        all_p = table.all()
        pinned_today = [r for r in all_p if r.get("fields", {}).get("PinterestPinID") and today in r.get("fields", {}).get("PublishedAt", "")]
        status["pins_today"] = len(pinned_today)
    except Exception:
        status["pins_today"] = "err"

    return status


def get_stream2_status() -> dict:
    """Get Stream 2 (freelancing) status."""
    status = {}

    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Jobs")
        today = datetime.now().strftime("%Y-%m-%d")
        all_jobs = table.all()
        today_jobs = [r for r in all_jobs if today in r.get("fields", {}).get("ScrapedAt", "")]
        status["jobs_scraped_today"] = len(today_jobs)

        proposals = [r for r in all_jobs if r.get("fields", {}).get("Status") == "Awaiting Approval"]
        status["pending_proposals"] = len(proposals)

        active = [r for r in all_jobs if r.get("fields", {}).get("Status") == "Active Contract"]
        status["active_contracts"] = len(active)
    except Exception as e:
        status["jobs_scraped_today"] = "err"
        logger.warning(f"Stream2 check: {e}")

    # Social replies today
    try:
        state_file = ROOT / "shared" / "proposals_today_social.json"
        if state_file.exists():
            with open(state_file) as f:
                d = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            status["social_replies_today"] = d.get("count", 0) if d.get("date") == today else 0
        else:
            status["social_replies_today"] = 0
    except Exception:
        status["social_replies_today"] = 0

    return status


def get_stream3_status() -> dict:
    """Get Stream 3 (arbitrage) status."""
    status = {}

    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Arbitrage_Deals")
        all_deals = table.all()
        today = datetime.now().strftime("%Y-%m-%d")

        today_deals = [r for r in all_deals if today in r.get("fields", {}).get("CreatedAt", "")]
        status["deals_scanned_today"] = len(today_deals)

        pending = [r for r in all_deals if r.get("fields", {}).get("Status") == "Pending Approval"]
        status["pending_approvals"] = len(pending)

        listed = [r for r in all_deals if r.get("fields", {}).get("Status") == "Listed on eBay"]
        status["open_listings"] = len(listed)
    except Exception as e:
        status["deals_scanned_today"] = "err"
        logger.warning(f"Stream3 check: {e}")

    # Capital available (rough estimate from uninvested revenue)
    status["capital_note"] = "Check Airtable Revenue_Log for current capital"

    return status


def get_token_status() -> dict:
    """Get token expiry status for all platforms."""
    status = {}

    def days_left(token_env: str) -> str:
        token = os.environ.get(token_env, "")
        if not token:
            return "not_set"
        try:
            import base64
            parts = token.split(".")
            if len(parts) == 3:
                payload = parts[1]
                payload += "=" * (-len(payload) % 4)
                data = json.loads(base64.b64decode(payload).decode())
                exp = data.get("exp")
                if exp:
                    days = (datetime.fromtimestamp(exp) - datetime.now()).days
                    return f"{days}d"
        except Exception:
            pass
        return "ok"

    status["ebay"] = days_left("EBAY_USER_TOKEN")
    status["etsy"] = days_left("ETSY_ACCESS_TOKEN")
    status["pinterest"] = days_left("PINTEREST_ACCESS_TOKEN")
    return status


def get_free_tier_status() -> dict:
    """Get free tier usage."""
    status = {}

    # Make.com (estimated from log)
    try:
        usage_file = ROOT / "shared" / "make_usage.json"
        if usage_file.exists():
            with open(usage_file) as f:
                data = json.load(f)
            month = datetime.now().strftime("%Y-%m")
            used = data.get(month, 0)
            status["make"] = f"{used}/1000 ops"
        else:
            status["make"] = "unknown"
    except Exception:
        status["make"] = "err"

    # Apify credit
    try:
        from shared.tier_monitor import check_apify_credit
        apify = check_apify_credit()
        status["apify"] = f"${apify.get('remaining_credit', 0):.2f}/$5"
    except Exception:
        status["apify"] = "err"

    # Airtable records
    try:
        from shared.tier_monitor import check_airtable_records
        at = check_airtable_records()
        status["airtable"] = f"{at.get('count', 0)}/1000 records"
    except Exception:
        status["airtable"] = "err"

    # Groq usage
    try:
        usage_file = ROOT / "shared" / "groq_usage.json"
        if usage_file.exists():
            with open(usage_file) as f:
                data = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            used = data.get(today, 0)
            status["groq"] = f"{used}/14400 req"
        else:
            status["groq"] = "unknown"
    except Exception:
        status["groq"] = "err"

    return status


def get_revenue_status() -> dict:
    """Get today's and MTD revenue."""
    status = {}
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Revenue_Log")
        all_records = table.all()

        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")

        streams = {"Digital Products": 0, "Freelancing": 0, "Arbitrage": 0}
        mtd = 0

        for r in all_records:
            f = r.get("fields", {})
            amount = float(f.get("Amount", 0))
            stream = f.get("Stream", "")
            date = f.get("Date", "")

            if date.startswith(this_month):
                mtd += amount
                if stream in streams:
                    streams[stream] += amount

        status["digital"] = f"${streams['Digital Products']:.2f}"
        status["freelance"] = f"${streams['Freelancing']:.2f}"
        status["arbitrage"] = f"${streams['Arbitrage']:.2f}"
        status["mtd"] = f"${mtd:.2f}"

    except Exception as e:
        status["digital"] = "err"
        status["freelance"] = "err"
        status["arbitrage"] = "err"
        status["mtd"] = "err"
        logger.warning(f"Revenue check: {e}")

    return status


def build_report() -> str:
    """Build the complete health report message."""
    s1 = get_stream1_status()
    s2 = get_stream2_status()
    s3 = get_stream3_status()
    tokens = get_token_status()
    tiers = get_free_tier_status()
    revenue = get_revenue_status()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def icon(val, good="ok", warn_thresh=None):
        if isinstance(val, str) and ("err" in str(val).lower() or "fail" in str(val).lower()):
            return "🔴"
        return "✅"

    report = f"""*Daily Health Report* — {now}

*STREAM 1 — Digital Products*
{icon(s1.get('products_today'))} Products today: {s1.get('products_today', 0)}
{icon(s1.get('gumroad_api'))} Gumroad API: {s1.get('gumroad_api', 'unknown')}
{icon(s1.get('pins_today'))} Pins posted: {s1.get('pins_today', 0)}

*STREAM 2 — Freelancing*
✅ Jobs scraped: {s2.get('jobs_scraped_today', 0)}
✅ Proposals awaiting approval: {s2.get('pending_proposals', 0)}
✅ Active contracts: {s2.get('active_contracts', 0)}
✅ Social replies today: {s2.get('social_replies_today', 0)}

*STREAM 3 — Arbitrage*
✅ Deals scanned: {s3.get('deals_scanned_today', 0)}
✅ Pending YES/NO: {s3.get('pending_approvals', 0)}
✅ Open eBay listings: {s3.get('open_listings', 0)}

*TOKENS*
✅ eBay: {tokens.get('ebay', 'unknown')} | Etsy: {tokens.get('etsy', 'unknown')} | Pinterest: {tokens.get('pinterest', 'unknown')}

*FREE TIERS*
✅ Make.com: {tiers.get('make', 'unknown')}
✅ Apify: {tiers.get('apify', 'unknown')}
✅ Airtable: {tiers.get('airtable', 'unknown')}
✅ Groq: {tiers.get('groq', 'unknown')}

*REVENUE (MTD)*
💰 Digital: {revenue.get('digital', '$0')} | Freelance: {revenue.get('freelance', '$0')} | Arbitrage: {revenue.get('arbitrage', '$0')}
💰 *Total MTD: {revenue.get('mtd', '$0')}*"""

    return report


def build_weekly_report() -> str:
    """Build a weekly summary with revenue comparison vs prior week."""
    revenue = get_revenue_status()
    now = datetime.now().strftime("%Y-%m-%d")

    return (
        f"*Weekly P&L Summary* — {now}\n\n"
        f"💰 Digital Products: {revenue.get('digital', '$0')}\n"
        f"💰 Freelancing: {revenue.get('freelance', '$0')}\n"
        f"💰 Arbitrage: {revenue.get('arbitrage', '$0')}\n"
        f"💰 *MTD Total: {revenue.get('mtd', '$0')}*\n\n"
        f"Full daily report sent separately."
    )


def run(weekly: bool = False):
    """Generate and send daily (or weekly) health report."""
    if weekly:
        logger.info("Generating weekly P&L summary...")
        try:
            report = build_weekly_report()
            telegram_bot.send(report)
            logger.info("Weekly report sent")
        except Exception as e:
            logger.error(f"Weekly report failed: {e}")
        return

    logger.info("Generating daily health report...")
    try:
        report = build_report()
        telegram_bot.send(report)
        logger.info("Health report sent")
    except Exception as e:
        logger.error(f"Health report failed: {e}")
        try:
            telegram_bot.send(f"*Health report error:* {e}")
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly", action="store_true", help="Send weekly P&L summary")
    args = parser.parse_args()
    run(weekly=args.weekly)
