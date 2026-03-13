"""
OAuth token auto-refresh — runs weekly via GitHub Actions.
Checks eBay, Etsy, Pinterest token expiry and silently refreshes if within 30 days.
"""
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests
from shared import telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO = os.environ.get("GITHUB_REPO", "")

ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def save_secret(name: str, value: str) -> bool:
    """Save a secret to GitHub via gh CLI."""
    if not REPO:
        logger.warning("GITHUB_REPO not set — cannot save to GitHub secrets")
        return False
    try:
        subprocess.run(
            ["gh", "secret", "set", name, "--body", value, "--repo", REPO],
            check=True, capture_output=True,
        )
        logger.info(f"Updated GitHub secret: {name}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Failed to update GitHub secret {name}: {e}")
        return False


def get_token_expiry(token_str: str) -> datetime:
    """Decode JWT expiry or return None if not a JWT."""
    try:
        import base64
        parts = token_str.split(".")
        if len(parts) == 3:
            payload = parts[1]
            # Add padding
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.b64decode(payload).decode())
            exp = data.get("exp")
            if exp:
                return datetime.fromtimestamp(exp)
    except Exception:
        pass
    return None


def refresh_etsy_token() -> bool:
    """Refresh Etsy OAuth token using refresh token."""
    api_key = os.environ.get("ETSY_API_KEY")
    refresh_token = os.environ.get("ETSY_REFRESH_TOKEN")

    if not refresh_token:
        logger.warning("ETSY_REFRESH_TOKEN not set — cannot auto-refresh")
        return False

    try:
        data = {
            "grant_type": "refresh_token",
            "client_id": api_key,
            "refresh_token": refresh_token,
        }
        resp = requests.post(ETSY_TOKEN_URL, data=data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()

        new_access = tokens.get("access_token")
        new_refresh = tokens.get("refresh_token", refresh_token)

        if not new_access:
            raise ValueError("No access token in refresh response")

        save_secret("ETSY_ACCESS_TOKEN", new_access)
        if new_refresh != refresh_token:
            save_secret("ETSY_REFRESH_TOKEN", new_refresh)

        # Update local .env
        env_path = ROOT / ".env"
        if env_path.exists():
            from dotenv import set_key
            set_key(str(env_path), "ETSY_ACCESS_TOKEN", new_access)

        logger.info("Etsy token refreshed successfully")
        return True

    except Exception as e:
        logger.error(f"Etsy token refresh failed: {e}")
        return False


def refresh_pinterest_token() -> bool:
    """Refresh Pinterest OAuth token."""
    app_id = os.environ.get("PINTEREST_APP_ID")
    app_secret = os.environ.get("PINTEREST_APP_SECRET")
    refresh_token = os.environ.get("PINTEREST_REFRESH_TOKEN")

    if not refresh_token:
        logger.warning("PINTEREST_REFRESH_TOKEN not set — cannot auto-refresh")
        return False

    try:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "boards:read,boards:write,pins:read,pins:write",
        }
        resp = requests.post(
            PINTEREST_TOKEN_URL,
            data=data,
            auth=(app_id, app_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        tokens = resp.json()

        new_access = tokens.get("access_token")
        new_refresh = tokens.get("refresh_token", refresh_token)

        if not new_access:
            raise ValueError("No access token in refresh response")

        save_secret("PINTEREST_ACCESS_TOKEN", new_access)
        if new_refresh != refresh_token:
            save_secret("PINTEREST_REFRESH_TOKEN", new_refresh)

        env_path = ROOT / ".env"
        if env_path.exists():
            from dotenv import set_key
            set_key(str(env_path), "PINTEREST_ACCESS_TOKEN", new_access)

        logger.info("Pinterest token refreshed successfully")
        return True

    except Exception as e:
        logger.error(f"Pinterest token refresh failed: {e}")
        return False


def refresh_ebay_token() -> bool:
    """Refresh eBay OAuth token."""
    import base64

    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    refresh_token = os.environ.get("EBAY_REFRESH_TOKEN")

    if not refresh_token:
        logger.warning("EBAY_REFRESH_TOKEN not set — cannot auto-refresh")
        return False

    try:
        credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://api.ebay.com/oauth/api_scope/sell.inventory",
        }

        resp = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()

        new_access = tokens.get("access_token")
        expires_in = tokens.get("expires_in", 0)

        if not new_access:
            raise ValueError("No access token in eBay refresh response")

        save_secret("EBAY_USER_TOKEN", new_access)

        env_path = ROOT / ".env"
        if env_path.exists():
            from dotenv import set_key
            set_key(str(env_path), "EBAY_USER_TOKEN", new_access)

        expiry_date = (datetime.now() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d")
        logger.info(f"eBay token refreshed. Next expiry: {expiry_date}")
        return True, expiry_date

    except Exception as e:
        logger.error(f"eBay token refresh failed: {e}")
        return False


def check_and_refresh_all() -> dict:
    """Check all token expiries and refresh if within 30 days."""
    results = {}
    alerts = []

    # eBay
    ebay_token = os.environ.get("EBAY_USER_TOKEN", "")
    if ebay_token:
        expiry = get_token_expiry(ebay_token)
        if expiry:
            days_left = (expiry - datetime.now()).days
            if days_left < 30:
                logger.info(f"eBay token expires in {days_left} days — refreshing...")
                result = refresh_ebay_token()
                results["ebay"] = "refreshed" if result else "refresh_failed"
                if not result:
                    alerts.append("eBay token refresh FAILED — run: python setup/ebay_auth.py")
            else:
                logger.info(f"eBay token OK — expires in {days_left} days")
                results["ebay"] = f"ok_{days_left}_days"
        else:
            results["ebay"] = "no_expiry_detected"
    else:
        results["ebay"] = "not_set"

    # Etsy
    etsy_token = os.environ.get("ETSY_ACCESS_TOKEN", "")
    if etsy_token:
        expiry = get_token_expiry(etsy_token)
        if expiry:
            days_left = (expiry - datetime.now()).days
            if days_left < 30:
                success = refresh_etsy_token()
                results["etsy"] = "refreshed" if success else "refresh_failed"
                if not success:
                    alerts.append("Etsy token refresh FAILED — run: python setup/etsy_auth.py")
            else:
                results["etsy"] = f"ok_{days_left}_days"
        else:
            results["etsy"] = "no_expiry_detected"
    else:
        results["etsy"] = "not_set"

    # Pinterest
    pinterest_token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
    if pinterest_token:
        expiry = get_token_expiry(pinterest_token)
        if expiry:
            days_left = (expiry - datetime.now()).days
            if days_left < 30:
                success = refresh_pinterest_token()
                results["pinterest"] = "refreshed" if success else "refresh_failed"
                if not success:
                    alerts.append("Pinterest token refresh FAILED — run: python setup/pinterest_auth.py")
            else:
                results["pinterest"] = f"ok_{days_left}_days"
        else:
            results["pinterest"] = "no_expiry_detected"
    else:
        results["pinterest"] = "not_set"

    # Send summary to Telegram
    try:
        lines = ["*Token Manager Report*\n"]
        for platform, status in results.items():
            icon = "✅" if "ok" in status or "refreshed" in status else "⚠️"
            lines.append(f"{icon} {platform.title()}: {status.replace('_', ' ')}")
        if alerts:
            lines.append("\n*Action Required:*")
            lines.extend([f"⛔ {a}" for a in alerts])

        telegram_bot.send("\n".join(lines))
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

    return results


if __name__ == "__main__":
    check_and_refresh_all()
