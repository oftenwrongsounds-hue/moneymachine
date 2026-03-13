"""
End-to-end smoke test — PASS/FAIL every integration before going live.
Usage: python setup/verify.py
"""
import os
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests

results = []


def check(name: str, fn) -> bool:
    """Run a check and record result."""
    try:
        result = fn()
        status = "PASS" if result else "FAIL"
        results.append((name, status, ""))
        print(f"  [{status}] {name}")
        return result
    except Exception as e:
        results.append((name, "FAIL", str(e)[:100]))
        print(f"  [FAIL] {name}: {str(e)[:80]}")
        return False


def header(text: str):
    print(f"\n{'─'*50}")
    print(f"  {text}")
    print(f"{'─'*50}")


# ─── AI APIs ──────────────────────────────────────────────────────────────────

def test_groq():
    from shared.groq_client import test_connection
    r = test_connection()
    return r.get("groq", False)


def test_together():
    from shared.groq_client import test_connection
    r = test_connection()
    return r.get("together", False)


# ─── Telegram ─────────────────────────────────────────────────────────────────

def test_telegram():
    from shared.telegram_bot import test_connection
    return test_connection()


# ─── Airtable ─────────────────────────────────────────────────────────────────

def test_airtable():
    from shared.airtable_logger import test_connection
    return test_connection()


def test_airtable_tables():
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base_id = os.environ.get("AIRTABLE_BASE_ID")
    if not api_key or not base_id:
        return False
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(
        f"https://api.airtable.com/v0/meta/bases/{base_id}/tables",
        headers=headers, timeout=15,
    )
    resp.raise_for_status()
    tables = {t["name"] for t in resp.json().get("tables", [])}
    required = {"Revenue_Log", "Arbitrage_Deals", "Products", "Jobs"}
    missing = required - tables
    if missing:
        raise Exception(f"Missing tables: {missing}")
    return True


# ─── Gumroad ──────────────────────────────────────────────────────────────────

def test_gumroad():
    from stream1_digital.gumroad_publisher import test_connection
    return test_connection()


# ─── Pinterest ────────────────────────────────────────────────────────────────

def test_pinterest():
    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        raise Exception("PINTEREST_ACCESS_TOKEN not set")
    resp = requests.get(
        "https://api.pinterest.com/v5/user_account",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return bool(resp.json().get("username"))


# ─── Etsy ─────────────────────────────────────────────────────────────────────

def test_etsy():
    api_key = os.environ.get("ETSY_API_KEY")
    if not api_key:
        raise Exception("ETSY_API_KEY not set")
    resp = requests.get(
        "https://openapi.etsy.com/v3/application/openapi-ping",
        headers={"x-api-key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("application_id") is not None or resp.status_code == 200


# ─── eBay ─────────────────────────────────────────────────────────────────────

def test_ebay():
    app_id = os.environ.get("EBAY_APP_ID")
    if not app_id:
        raise Exception("EBAY_APP_ID not set")
    resp = requests.get(
        f"https://svcs.ebay.com/services/search/FindingService/v1"
        f"?OPERATION-NAME=findItemsByKeywords"
        f"&SERVICE-VERSION=1.0.0"
        f"&SECURITY-APPNAME={app_id}"
        f"&RESPONSE-DATA-FORMAT=JSON"
        f"&keywords=test"
        f"&paginationInput.entriesPerPage=1",
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return "findItemsByKeywordsResponse" in data


# ─── Apify ────────────────────────────────────────────────────────────────────

def test_apify():
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise Exception("APIFY_TOKEN not set")
    resp = requests.get(
        "https://api.apify.com/v2/users/me",
        params={"token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return bool(resp.json().get("data", {}).get("id"))


# ─── Reddit ───────────────────────────────────────────────────────────────────

def test_reddit():
    import praw
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")
    if not all([client_id, client_secret]):
        raise Exception("Reddit credentials not set")
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent="ProfitPowerhouse/1.0 (verify.py)",
    )
    # Test read-only access
    posts = list(reddit.subreddit("test").hot(limit=1))
    return True


# ─── Twitter ──────────────────────────────────────────────────────────────────

def test_twitter():
    token = os.environ.get("TWITTER_BEARER_TOKEN")
    if not token:
        raise Exception("TWITTER_BEARER_TOKEN not set")
    resp = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": "test", "max_results": 10},
        timeout=15,
    )
    if resp.status_code == 429:
        raise Exception("Rate limited (but connection works)")
    resp.raise_for_status()
    return "data" in resp.json() or "meta" in resp.json()


# ─── Environment variables check ──────────────────────────────────────────────

def test_env_vars():
    required = [
        "GROQ_API_KEY", "TOGETHER_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
        "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "GUMROAD_ACCESS_TOKEN",
        "ETSY_API_KEY", "ETSY_ACCESS_TOKEN",
        "PINTEREST_APP_ID", "PINTEREST_ACCESS_TOKEN",
        "EBAY_APP_ID", "EBAY_USER_TOKEN",
        "APIFY_TOKEN",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise Exception(f"Missing: {', '.join(missing)}")
    return True


# ─── Dry run tests ────────────────────────────────────────────────────────────

def test_product_factory_dryrun():
    import subprocess
    result = subprocess.run(
        [sys.executable, "stream1_digital/product_factory.py", "--dry-run"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    return result.returncode == 0 and "Dry run complete" in result.stdout


def test_scanner_dryrun():
    import subprocess
    result = subprocess.run(
        [sys.executable, "stream3_arbitrage/scanner.py", "--dry-run"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    return result.returncode == 0 and "Dry run complete" in result.stdout


def test_job_scraper_dryrun():
    import subprocess
    result = subprocess.run(
        [sys.executable, "stream2_freelance/job_scraper.py", "--dry-run"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    return result.returncode == 0 and "Dry run complete" in result.stdout


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  $10 Profit Powerhouse — Integration Verification")
    print("=" * 60)

    header("Environment Variables")
    check("All required env vars present", test_env_vars)

    header("AI APIs")
    check("Groq API (primary AI)", test_groq)
    check("Together.ai API (fallback AI)", test_together)

    header("Messaging & Storage")
    check("Telegram bot connection", test_telegram)
    check("Airtable connection", test_airtable)
    check("Airtable all 4 tables exist", test_airtable_tables)

    header("Revenue Stream APIs")
    check("Gumroad API", test_gumroad)
    check("Etsy API", test_etsy)
    check("Pinterest API", test_pinterest)
    check("eBay Finding API", test_ebay)
    check("Apify API", test_apify)

    header("Social APIs")
    check("Reddit API", test_reddit)
    check("Twitter/X API", test_twitter)

    header("Dry Run Tests (script execution)")
    check("product_factory.py --dry-run", test_product_factory_dryrun)
    check("scanner.py --dry-run", test_scanner_dryrun)
    check("job_scraper.py --dry-run", test_job_scraper_dryrun)

    # ─── Summary ──────────────────────────────────────────────────────────────
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    total = len(results)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'='*60}")

    if failed > 0:
        print("\nFailed checks:")
        for name, status, err in results:
            if status == "FAIL":
                print(f"  ✗ {name}")
                if err:
                    print(f"    Error: {err}")
        print("\nFix these before going live.")
        sys.exit(1)
    else:
        print("\nAll checks passed! System ready to go live.")
        print("\nNext steps:")
        print("  1. Import Make.com scenarios from make_scenarios/")
        print("  2. Enable GitHub Actions (push to GitHub first)")
        print("  3. Run first live product: python stream1_digital/product_factory.py")


if __name__ == "__main__":
    main()
