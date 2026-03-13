"""
API watchdog — monitors all API calls for deprecation/404/changes.
Auto-detects and patches changed endpoints using Groq.
"""
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# API endpoints to monitor
ENDPOINTS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/models",
        "auth_header": lambda: f"Bearer {os.environ.get('GROQ_API_KEY', '')}",
        "changelog_url": "https://console.groq.com/docs/changelog",
    },
    "pinterest": {
        "url": "https://api.pinterest.com/v5/user_account",
        "auth_header": lambda: f"Bearer {os.environ.get('PINTEREST_ACCESS_TOKEN', '')}",
        "changelog_url": "https://developers.pinterest.com/docs/api/v5/",
    },
    "etsy": {
        "url": "https://openapi.etsy.com/v3/application/openapi-ping",
        "auth_header": lambda: None,
        "extra_headers": {"x-api-key": os.environ.get("ETSY_API_KEY", "")},
        "changelog_url": "https://developer.etsy.com/documentation/",
    },
    "gumroad": {
        "url": "https://api.gumroad.com/v2/products",
        "auth_header": lambda: f"Bearer {os.environ.get('GUMROAD_ACCESS_TOKEN', '')}",
    },
    "airtable": {
        "url": "https://api.airtable.com/v0/meta/whoami",
        "auth_header": lambda: f"Bearer {os.environ.get('AIRTABLE_API_KEY', '')}",
    },
}

API_STATUS_FILE = ROOT / "shared" / "api_status.json"


def load_status() -> dict:
    if API_STATUS_FILE.exists():
        try:
            with open(API_STATUS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_status(status: dict):
    with open(API_STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def check_endpoint(name: str, config: dict) -> dict:
    """Check an API endpoint for availability and deprecation signals."""
    import requests

    url = config["url"]
    auth_fn = config.get("auth_header", lambda: None)
    extra = config.get("extra_headers", {})

    headers = {}
    auth = auth_fn()
    if auth:
        headers["Authorization"] = auth
    headers.update(extra)

    result = {
        "name": name,
        "url": url,
        "checked_at": datetime.now().isoformat(),
        "status_code": None,
        "deprecated": False,
        "error": None,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        result["status_code"] = resp.status_code

        # Check for deprecation headers
        depr_headers = ["Deprecation", "Sunset", "X-Api-Deprecation-Date", "X-Deprecated"]
        for h in depr_headers:
            if h.lower() in [k.lower() for k in resp.headers]:
                result["deprecated"] = True
                result["deprecation_info"] = resp.headers.get(h, "")
                logger.warning(f"API {name} has deprecation header: {h}")

        if resp.status_code in (404, 410):
            result["error"] = f"Endpoint gone: {resp.status_code}"
        elif resp.status_code == 401:
            result["error"] = "Auth failed (token may be expired)"
        elif resp.status_code >= 500:
            result["error"] = f"Server error: {resp.status_code}"

    except Exception as e:
        result["error"] = str(e)[:100]

    return result


def attempt_endpoint_fix(api_name: str, old_url: str, error: str) -> str:
    """Use Groq to find the updated API endpoint."""
    prompt = f"""The following API endpoint returned an error:

API: {api_name}
Old URL: {old_url}
Error: {error}

Search for the current correct API endpoint for {api_name}.
Known changelogs:
- Groq: https://console.groq.com/docs/
- Pinterest: https://developers.pinterest.com/docs/api/v5/
- Etsy: https://developer.etsy.com/documentation/
- Gumroad: https://help.gumroad.com/article/280-gumroad-api
- Airtable: https://airtable.com/developers/web/api/introduction

Based on your knowledge of these APIs (current as of early 2026),
what is the most likely correct endpoint URL to replace:
{old_url}

Return only the corrected URL, nothing else."""

    try:
        return groq_client.complete(
            prompt,
            system="You are an API integration expert. Return only the corrected endpoint URL.",
            max_tokens=100,
            temperature=0.2,
        ).strip()
    except Exception as e:
        logger.error(f"Could not get fix from Groq: {e}")
        return ""


def apply_endpoint_fix(api_name: str, old_url: str, new_url: str) -> bool:
    """Search Python files for old URL and replace with new URL."""
    if not new_url or new_url == old_url:
        return False

    changed_files = []
    for py_file in ROOT.rglob("*.py"):
        if ".git" in str(py_file) or "test_" in py_file.name:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            if old_url in content:
                new_content = content.replace(old_url, new_url)
                py_file.write_text(new_content, encoding="utf-8")
                changed_files.append(str(py_file.relative_to(ROOT)))
        except Exception as e:
            logger.warning(f"Could not update {py_file}: {e}")

    if changed_files:
        logger.info(f"Updated {api_name} URL in: {', '.join(changed_files)}")
        try:
            subprocess.run(["git", "add"] + changed_files, cwd=str(ROOT), capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"Auto-fix: update {api_name} endpoint URL"],
                cwd=str(ROOT), capture_output=True,
            )
        except Exception:
            pass
        return True

    return False


def run():
    """Check all watched API endpoints and auto-fix any issues."""
    logger.info("Starting API watchdog...")
    prev_status = load_status()
    new_status = {}
    alerts = []

    for api_name, config in ENDPOINTS.items():
        result = check_endpoint(api_name, config)
        new_status[api_name] = result

        # Compare with previous
        prev = prev_status.get(api_name, {})
        prev_ok = prev.get("status_code") in (200, 201, 204) or not prev.get("error")
        curr_ok = result.get("status_code") in (200, 201, 204) and not result.get("error")

        if result.get("deprecated"):
            alerts.append(f"⚠️ {api_name}: Deprecation detected")

        if not curr_ok and prev_ok:
            # Newly broken — try to fix
            error = result.get("error", "")
            status_code = result.get("status_code")

            if status_code in (404, 410):
                logger.warning(f"{api_name} endpoint gone ({status_code}) — attempting fix")
                new_url = attempt_endpoint_fix(api_name, config["url"], error)
                if new_url:
                    fixed = apply_endpoint_fix(api_name, config["url"], new_url)
                    if fixed:
                        alerts.append(f"✅ {api_name}: Endpoint auto-updated to {new_url}")
                    else:
                        alerts.append(f"⚠️ {api_name}: Endpoint broken. Suggested fix: {new_url}")
                else:
                    alerts.append(f"🚨 {api_name}: Endpoint broken, could not auto-fix. Manual check needed.")
            elif status_code == 401:
                alerts.append(f"⚠️ {api_name}: Auth failed — token may be expired. Run token_manager.py")

        elif not curr_ok:
            # Ongoing issue
            logger.warning(f"{api_name}: still broken ({result.get('error', '')})")

    save_status(new_status)

    if alerts:
        try:
            telegram_bot.send("*API Watchdog Alert*\n\n" + "\n".join(alerts))
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    logger.info(f"API watchdog complete. {len(alerts)} alerts.")
    return new_status


if __name__ == "__main__":
    run()
