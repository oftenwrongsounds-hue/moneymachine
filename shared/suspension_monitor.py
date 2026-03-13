"""
Suspension prevention + monitor.
Prevention: rate limiting, warm-up schedules, user-agent rotation.
Detection: checks account status every 12 hours, pauses stream on suspension.
Response: generates appeal letter, sends to Telegram for copy-paste.
"""
import os
import sys
import json
import logging
import time
import random
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUSPENSION_STATUS_FILE = ROOT / "shared" / "suspension_status.json"

# Platform daily limits (suspension prevention)
PLATFORM_LIMITS = {
    "upwork": {"proposals": 5, "logins": 3},
    "gumroad": {"products": 10},
    "ebay": {"listings": 15},
    "etsy": {"listings": 10},
    "reddit": {"posts": 2, "comments": 5},
}

# User agent pool for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
]


def get_random_user_agent() -> str:
    """Get a random user agent for request rotation."""
    return random.choice(USER_AGENTS)


def get_random_delay(min_sec: float = 8, max_sec: float = 25) -> float:
    """Get a randomized delay to avoid detection patterns."""
    return random.uniform(min_sec, max_sec)


def load_suspension_status() -> dict:
    if SUSPENSION_STATUS_FILE.exists():
        try:
            with open(SUSPENSION_STATUS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_suspension_status(status: dict):
    with open(SUSPENSION_STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def check_gumroad_status() -> str:
    """Check if Gumroad account is in good standing."""
    import requests
    token = os.environ.get("GUMROAD_ACCESS_TOKEN")
    if not token:
        return "not_configured"
    try:
        resp = requests.get(
            "https://api.gumroad.com/v2/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            user = data.get("user", {})
            if user.get("is_banned") or user.get("suspended"):
                return "suspended"
            return "active"
        elif resp.status_code == 401:
            return "auth_error"
    except Exception as e:
        logger.warning(f"Gumroad status check failed: {e}")
    return "unknown"


def check_ebay_status() -> str:
    """Check eBay account status via Trading API."""
    import requests
    import xml.etree.ElementTree as ET

    token = os.environ.get("EBAY_USER_TOKEN")
    app_id = os.environ.get("EBAY_APP_ID")
    if not token or not app_id:
        return "not_configured"

    xml_request = f"""<?xml version="1.0" encoding="utf-8"?>
<GetUserRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{token}</eBayAuthToken>
    </RequesterCredentials>
</GetUserRequest>"""

    try:
        resp = requests.post(
            "https://api.ebay.com/ws/api.dll",
            headers={
                "X-EBAY-API-CALL-NAME": "GetUser",
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-APP-NAME": app_id,
                "Content-Type": "text/xml",
                "X-EBAY-API-IAF-TOKEN": token,
            },
            data=xml_request.encode("utf-8"),
            timeout=15,
        )
        root = ET.fromstring(resp.content)
        ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.findtext("ns:Ack", "", namespaces=ns)
        if ack == "Failure":
            return "suspended"
        return "active"
    except Exception as e:
        logger.warning(f"eBay status check failed: {e}")
    return "unknown"


def check_etsy_status() -> str:
    """Check Etsy shop status."""
    import requests
    token = os.environ.get("ETSY_ACCESS_TOKEN")
    shop_id = os.environ.get("ETSY_SHOP_ID")
    if not token or not shop_id:
        return "not_configured"
    try:
        resp = requests.get(
            f"https://openapi.etsy.com/v3/application/shops/{shop_id}",
            headers={
                "x-api-key": os.environ.get("ETSY_API_KEY", ""),
                "Authorization": f"Bearer {token}",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            shop = resp.json()
            if shop.get("is_banned") or shop.get("state") == "suspended":
                return "suspended"
            return "active"
        elif resp.status_code in (403, 401):
            return "auth_error"
    except Exception as e:
        logger.warning(f"Etsy status check failed: {e}")
    return "unknown"


def pause_platform(platform: str):
    """Write a pause flag file to stop automated activity on a platform."""
    flag_file = ROOT / "shared" / f"paused_{platform}.flag"
    flag_file.write_text(f"Paused at {datetime.now().isoformat()} — suspension detected")
    logger.warning(f"Platform {platform} PAUSED — suspension detected")


def resume_platform_at_half_rate(platform: str):
    """Remove pause flag and set half-rate throttle for 2-week recovery."""
    pause_flag = ROOT / "shared" / f"paused_{platform}.flag"
    throttle_file = ROOT / "shared" / f"throttle_{platform}.json"

    pause_flag.unlink(missing_ok=True)
    with open(throttle_file, "w") as f:
        json.dump({
            "rate": 0.5,
            "full_rate_after": (datetime.now().timestamp() + 14 * 86400),
            "reason": "Post-suspension recovery",
        }, f)
    logger.info(f"Platform {platform} resumed at 50% rate")


def generate_appeal_letter(platform: str) -> str:
    """Generate a platform-specific appeal letter using Groq."""
    appeals = {
        "ebay": "I'm writing to appeal the suspension of my eBay seller account. I am a small seller listing retail clearance items. I follow all eBay policies and am committed to maintaining excellent buyer experiences. I would appreciate the opportunity to resolve any issues with my account.",
        "etsy": "I'm writing to appeal the suspension of my Etsy shop. I sell original digital templates that I create. I follow all Etsy marketplace policies and am committed to the handmade/digital creator community. I would like to understand what policy was violated and how I can resolve this.",
        "gumroad": "I'm writing to appeal the suspension of my Gumroad account. I sell original digital products (templates and planners) that I create. I follow all Gumroad terms of service.",
        "upwork": "I'm writing to appeal the suspension of my Upwork freelancer account. I am a professional writer offering legitimate content writing services. I follow all Upwork terms of service and am committed to delivering excellent work.",
    }

    template = appeals.get(platform, f"I'm writing to appeal the suspension of my {platform} account.")

    prompt = f"""Write a professional account appeal letter for {platform}.

Base message: {template}

Make it:
1. Professional and polite
2. Specific to {platform}'s platform
3. 150-200 words
4. Include a clear request for reinstatement
5. Offer to provide any additional information needed

Return only the letter text."""

    try:
        return groq_client.complete(
            prompt,
            system="You write professional business correspondence. Tone is polite, factual, and professional.",
            max_tokens=400,
            temperature=0.5,
        )
    except Exception as e:
        logger.error(f"Could not generate appeal: {e}")
        return template


def check_all_platforms() -> dict:
    """Check all platform account statuses."""
    logger.info("Checking all platform account statuses...")

    checkers = {
        "gumroad": check_gumroad_status,
        "ebay": check_ebay_status,
        "etsy": check_etsy_status,
    }

    results = {}
    prev_status = load_suspension_status()

    for platform, checker in checkers.items():
        try:
            status = checker()
            results[platform] = status

            # Detect new suspension
            prev = prev_status.get(platform, "unknown")
            if status == "suspended" and prev != "suspended":
                logger.error(f"{platform.upper()} SUSPENDED — taking action")
                pause_platform(platform)

                # Generate and send appeal
                appeal = generate_appeal_letter(platform)
                try:
                    telegram_bot.send(
                        f"🚨 *{platform.upper()} ACCOUNT SUSPENDED*\n\n"
                        f"Automated activity paused for {platform}.\n"
                        f"Other streams continue normally.\n\n"
                        f"*Appeal letter (copy-paste to {platform} support):*\n\n"
                        f"```\n{appeal[:800]}\n```\n\n"
                        f"Reply RESUME when account is reinstated."
                    )
                except Exception as e:
                    logger.warning(f"Telegram alert failed: {e}")

            elif status == "active" and prev == "suspended":
                # Reinstated — resume at half rate
                logger.info(f"{platform} reinstated — resuming at 50% rate")
                resume_platform_at_half_rate(platform)
                try:
                    telegram_bot.send(
                        f"✅ *{platform.upper()} REINSTATED*\n"
                        f"Resuming at 50% rate for 2 weeks."
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Status check failed for {platform}: {e}")
            results[platform] = "check_failed"

    save_suspension_status(results)
    return results


def is_platform_paused(platform: str) -> bool:
    """Check if a platform is currently paused due to suspension."""
    flag_file = ROOT / "shared" / f"paused_{platform}.flag"
    return flag_file.exists()


def get_rate_multiplier(platform: str) -> float:
    """Get the rate multiplier for a platform (1.0 = full rate, 0.5 = half rate)."""
    throttle_file = ROOT / "shared" / f"throttle_{platform}.json"
    if throttle_file.exists():
        try:
            with open(throttle_file) as f:
                data = json.load(f)
            if datetime.now().timestamp() > data.get("full_rate_after", 0):
                throttle_file.unlink()
                return 1.0
            return data.get("rate", 1.0)
        except Exception:
            pass
    return 1.0


if __name__ == "__main__":
    results = check_all_platforms()
    print(json.dumps(results, indent=2))
