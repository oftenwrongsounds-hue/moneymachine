"""
Free tier monitor — watches usage across Groq, Make.com, Apify, Airtable.
Auto-throttles or switches fallbacks when limits approach.
"""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests
from shared import telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FREE_TIER_LIMITS = {
    "groq_daily": 14400,
    "make_monthly": 1000,
    "apify_credit": 5.00,
    "airtable_records": 1000,
}


def check_groq_usage() -> dict:
    """Check Groq API usage against daily limit."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return {"status": "not_configured", "used": 0, "limit": 14400}

    try:
        # Groq doesn't have a usage endpoint — track locally
        usage_file = ROOT / "shared" / "groq_usage.json"
        today = datetime.now().strftime("%Y-%m-%d")

        if usage_file.exists():
            with open(usage_file) as f:
                data = json.load(f)
        else:
            data = {}

        used = data.get(today, 0)
        limit = FREE_TIER_LIMITS["groq_daily"]
        pct = (used / limit) * 100

        status = "ok"
        if pct >= 90:
            status = "critical"
            # Auto-switch to Together.ai
            _activate_together_fallback()
        elif pct >= 75:
            status = "warning"

        return {"status": status, "used": used, "limit": limit, "pct": pct}

    except Exception as e:
        logger.error(f"Groq usage check failed: {e}")
        return {"status": "error", "error": str(e)}


def check_apify_credit() -> dict:
    """Check remaining Apify credit."""
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return {"status": "not_configured", "remaining": 0}

    try:
        resp = requests.get(
            "https://api.apify.com/v2/users/me",
            params={"token": token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        plan = data.get("plan", {})
        usage = data.get("usage", {})

        # Get credit balance
        credit = float(str(data.get("credits", 0)).replace("$", ""))

        status = "ok"
        if credit < 1.0:
            status = "critical"
            _pause_etsy_scraper()
        elif credit < 2.0:
            status = "warning"

        return {"status": status, "remaining_credit": credit, "limit": FREE_TIER_LIMITS["apify_credit"]}

    except Exception as e:
        logger.error(f"Apify credit check failed: {e}")
        return {"status": "error", "error": str(e)}


def check_airtable_records() -> dict:
    """Check Airtable record count."""
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base_id = os.environ.get("AIRTABLE_BASE_ID")

    if not api_key or not base_id:
        return {"status": "not_configured", "count": 0}

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        total = 0

        for table_name in ["Revenue_Log", "Arbitrage_Deals", "Products", "Jobs"]:
            try:
                resp = requests.get(
                    f"https://api.airtable.com/v0/{base_id}/{table_name}",
                    headers=headers,
                    params={"fields[]": "CreatedAt"},
                    timeout=15,
                )
                if resp.ok:
                    total += len(resp.json().get("records", []))
            except Exception:
                pass

        limit = FREE_TIER_LIMITS["airtable_records"]
        pct = (total / limit) * 100

        status = "ok"
        if pct >= 80:
            status = "warning"
            _archive_old_airtable_records()

        return {"status": status, "count": total, "limit": limit, "pct": pct}

    except Exception as e:
        logger.error(f"Airtable check failed: {e}")
        return {"status": "error", "error": str(e)}


def check_make_usage() -> dict:
    """
    Check Make.com operations usage.
    Make.com doesn't have a public API for this — we estimate from our own run logs.
    """
    usage_file = ROOT / "shared" / "make_usage.json"
    month = datetime.now().strftime("%Y-%m")

    try:
        if usage_file.exists():
            with open(usage_file) as f:
                data = json.load(f)
            used = data.get(month, 0)
        else:
            used = 0

        limit = FREE_TIER_LIMITS["make_monthly"]
        pct = (used / limit) * 100

        status = "ok"
        if pct >= 80:
            status = "warning"
            _throttle_proposal_engine()

        return {"status": status, "used": used, "limit": limit, "pct": pct}

    except Exception as e:
        logger.error(f"Make.com usage check failed: {e}")
        return {"status": "error"}


def _activate_together_fallback():
    """Tell groq_client.py to use Together.ai for the rest of the day."""
    logger.warning("Groq quota >90% — activating Together.ai fallback")
    from shared.groq_client import _groq_quota_exhausted
    # Set the module-level flag
    import shared.groq_client as gc
    gc._groq_quota_exhausted = True
    import time
    gc._quota_reset_time = time.time() + 3600
    logger.info("Together.ai fallback activated")


def _pause_etsy_scraper():
    """Write a flag file to pause Etsy trend scraper to save Apify credit."""
    flag_file = ROOT / "shared" / "pause_etsy_scraper.flag"
    flag_file.write_text(datetime.now().isoformat())
    logger.warning("Apify credit < $1 — Etsy trend scraper paused")


def _throttle_proposal_engine():
    """Reduce proposal engine frequency via GitHub Actions API."""
    token = os.environ.get("GITHUB_DISPATCH_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return

    logger.warning("Make.com ops >80% — throttling proposal engine")
    # Write throttle flag
    flag_file = ROOT / "shared" / "throttle_proposals.flag"
    flag_file.write_text("2")  # Reduce to 2x/day
    logger.info("Proposal engine throttled to 2x/day")


def _archive_old_airtable_records():
    """Archive records older than 90 days to free up Airtable space."""
    logger.info("Airtable >800 records — archiving old records...")
    try:
        from shared.airtable_logger import archive_old_records
        archived = archive_old_records(days=90)
        logger.info(f"Archived {archived} records")
    except Exception as e:
        logger.error(f"Archive failed: {e}")


def run() -> dict:
    """Check all free tiers and take automated action where needed."""
    logger.info("Starting tier monitor...")

    results = {
        "groq": check_groq_usage(),
        "apify": check_apify_credit(),
        "airtable": check_airtable_records(),
        "make": check_make_usage(),
    }

    # Build Telegram summary
    lines = ["*Free Tier Monitor*\n"]
    any_critical = False

    for service, data in results.items():
        status = data.get("status", "unknown")
        icon = "✅" if status == "ok" else ("⚠️" if status == "warning" else "🚨")
        if status == "critical":
            any_critical = True

        if service == "groq":
            detail = f"{data.get('used', 0)}/{data.get('limit', 14400)} req/day"
        elif service == "apify":
            detail = f"${data.get('remaining_credit', 0):.2f}/$5 credit"
        elif service == "airtable":
            detail = f"{data.get('count', 0)}/{data.get('limit', 1000)} records"
        elif service == "make":
            detail = f"{data.get('used', 0)}/{data.get('limit', 1000)} ops/mo"
        else:
            detail = status

        lines.append(f"{icon} {service.upper()}: {detail}")

    if any_critical:
        lines.append("\n⚠️ Critical thresholds hit — fallbacks activated automatically")

    try:
        telegram_bot.send("\n".join(lines))
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")

    return results


if __name__ == "__main__":
    run()
