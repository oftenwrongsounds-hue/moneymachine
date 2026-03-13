"""
Master setup wizard — runs once after repo is built.
Handles: Airtable schema, GitHub secrets, Groq/Together.ai verification,
Apify actor discovery, Fiverr gig copy generation, Telegram test.
"""
import os
import sys
import json
import subprocess
import argparse
import time
from pathlib import Path

# Allow running from setup/ or root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

import requests

AIRTABLE_TABLES = {
    "Revenue_Log": {
        "fields": [
            {"name": "Stream", "type": "singleLineText"},
            {"name": "Amount", "type": "number", "options": {"precision": 2}},
            {"name": "Source", "type": "singleLineText"},
            {"name": "Description", "type": "multilineText"},
            {"name": "Date", "type": "date", "options": {"dateFormat": {"name": "iso"}}},
            {"name": "CreatedAt", "type": "singleLineText"},
        ]
    },
    "Arbitrage_Deals": {
        "fields": [
            {"name": "Title", "type": "singleLineText"},
            {"name": "BuyPrice", "type": "number", "options": {"precision": 2}},
            {"name": "SellPrice", "type": "number", "options": {"precision": 2}},
            {"name": "EstimatedProfit", "type": "number", "options": {"precision": 2}},
            {"name": "ConfidenceScore", "type": "number", "options": {"precision": 0}},
            {"name": "SourceURL", "type": "url"},
            {"name": "Status", "type": "singleLineText"},
            {"name": "DealID", "type": "singleLineText"},
            {"name": "CreatedAt", "type": "singleLineText"},
        ]
    },
    "Products": {
        "fields": [
            {"name": "Title", "type": "singleLineText"},
            {"name": "Niche", "type": "singleLineText"},
            {"name": "GumroadURL", "type": "url"},
            {"name": "EtsyListingID", "type": "singleLineText"},
            {"name": "PinterestPinID", "type": "singleLineText"},
            {"name": "Price", "type": "number", "options": {"precision": 2}},
            {"name": "Status", "type": "singleLineText"},
            {"name": "PublishedAt", "type": "singleLineText"},
        ]
    },
    "Jobs": {
        "fields": [
            {"name": "Platform", "type": "singleLineText"},
            {"name": "Title", "type": "singleLineText"},
            {"name": "Budget", "type": "singleLineText"},
            {"name": "URL", "type": "url"},
            {"name": "Status", "type": "singleLineText"},
            {"name": "ProposalText", "type": "multilineText"},
            {"name": "ScrapedAt", "type": "singleLineText"},
        ]
    },
}

GITHUB_SECRETS = [
    "GROQ_API_KEY", "TOGETHER_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
    "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "GUMROAD_ACCESS_TOKEN",
    "ETSY_API_KEY", "ETSY_SHOP_ID", "ETSY_ACCESS_TOKEN", "ETSY_REFRESH_TOKEN",
    "PINTEREST_APP_ID", "PINTEREST_APP_SECRET", "PINTEREST_ACCESS_TOKEN",
    "EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_DEV_ID", "EBAY_USER_TOKEN",
    "APIFY_TOKEN", "GMAIL_CREDENTIALS",
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD",
    "TWITTER_BEARER_TOKEN", "MAKE_WEBHOOK_URL", "GITHUB_DISPATCH_TOKEN",
]


def header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def step(text: str):
    print(f"\n[*] {text}")


def ok(text: str):
    print(f"    [OK] {text}")


def warn(text: str):
    print(f"    [WARN] {text}")


def fail(text: str):
    print(f"    [FAIL] {text}")


# ─── Step 1: Check gh CLI ─────────────────────────────────────────────────────

def check_gh_cli() -> bool:
    step("Checking GitHub CLI (gh)...")
    try:
        result = subprocess.run(["gh", "--version"], capture_output=True, text=True)
        ok(f"gh found: {result.stdout.strip().splitlines()[0]}")
        return True
    except FileNotFoundError:
        warn("gh CLI not found.")
        print("    Install it from: https://cli.github.com/")
        print("    Windows: winget install --id GitHub.cli")
        print("    Then run: gh auth login")
        return False


# ─── Step 2: Create Airtable schema ──────────────────────────────────────────

def create_airtable_schema(tables_only: bool = False) -> bool:
    step("Creating Airtable schema...")
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base_id = os.environ.get("AIRTABLE_BASE_ID")

    if not api_key or not base_id:
        warn("AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set — skipping schema creation")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Get existing tables
    try:
        resp = requests.get(
            f"https://api.airtable.com/v0/meta/bases/{base_id}/tables",
            headers=headers, timeout=15,
        )
        resp.raise_for_status()
        existing = {t["name"] for t in resp.json().get("tables", [])}
    except Exception as e:
        warn(f"Could not list Airtable tables: {e}")
        existing = set()

    created = 0
    for table_name, config in AIRTABLE_TABLES.items():
        if table_name in existing:
            ok(f"Table '{table_name}' already exists")
            continue
        try:
            payload = {"name": table_name, "fields": config["fields"]}
            resp = requests.post(
                f"https://api.airtable.com/v0/meta/bases/{base_id}/tables",
                headers=headers, json=payload, timeout=15,
            )
            resp.raise_for_status()
            ok(f"Created table: {table_name}")
            created += 1
            time.sleep(0.5)  # Rate limit
        except Exception as e:
            warn(f"Could not create table '{table_name}': {e}")

    if created > 0:
        ok(f"Created {created} new Airtable tables")
    return True


# ─── Step 3: Set GitHub secrets ──────────────────────────────────────────────

def set_github_secrets() -> int:
    step("Setting GitHub secrets...")
    repo = os.environ.get("GITHUB_REPO")
    if not repo:
        warn("GITHUB_REPO not set — skipping GitHub secrets")
        return 0

    set_count = 0
    for secret_name in GITHUB_SECRETS:
        value = os.environ.get(secret_name)
        if not value:
            warn(f"  {secret_name} not in .env — skipping")
            continue
        try:
            subprocess.run(
                ["gh", "secret", "set", secret_name, "--body", value, "--repo", repo],
                check=True, capture_output=True,
            )
            ok(f"Set secret: {secret_name}")
            set_count += 1
        except subprocess.CalledProcessError as e:
            warn(f"Failed to set {secret_name}: {e.stderr.decode()}")
        except FileNotFoundError:
            warn("gh CLI not found — cannot set GitHub secrets")
            break

    return set_count


# ─── Step 4: Verify AI APIs ───────────────────────────────────────────────────

def verify_ai_apis() -> dict:
    step("Verifying AI API connections...")
    results = {}

    # Groq
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 5},
                timeout=15,
            )
            resp.raise_for_status()
            ok("Groq API: connected")
            results["groq"] = True
        except Exception as e:
            fail(f"Groq API: {e}")
            results["groq"] = False
    else:
        warn("GROQ_API_KEY not set")
        results["groq"] = False

    # Together.ai
    together_key = os.environ.get("TOGETHER_API_KEY")
    if together_key:
        try:
            resp = requests.post(
                "https://api.together.xyz/v1/chat/completions",
                headers={"Authorization": f"Bearer {together_key}", "Content-Type": "application/json"},
                json={"model": "meta-llama/Llama-3-70b-chat-hf", "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 5},
                timeout=15,
            )
            resp.raise_for_status()
            ok("Together.ai API: connected")
            results["together"] = True
        except Exception as e:
            fail(f"Together.ai API: {e}")
            results["together"] = False
    else:
        warn("TOGETHER_API_KEY not set")
        results["together"] = False

    return results


# ─── Step 5: Discover Apify actors ────────────────────────────────────────────

def discover_apify_actors() -> dict:
    step("Discovering best Apify actors...")
    apify_token = os.environ.get("APIFY_TOKEN")
    if not apify_token:
        warn("APIFY_TOKEN not set — skipping actor discovery")
        return {}

    searches = {
        "walmart_clearance": "walmart clearance scraper",
        "target_clearance": "target clearance scraper",
        "ebay_scraper": "ebay product scraper",
        "etsy_scraper": "etsy search scraper",
    }

    actors = {}
    for key, query in searches.items():
        try:
            resp = requests.get(
                "https://api.apify.com/v2/store",
                params={"search": query, "limit": 5, "token": apify_token},
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("items", [])
            if items:
                # Pick highest run count
                best = max(items, key=lambda x: x.get("stats", {}).get("totalRuns", 0))
                actors[key] = {
                    "id": best.get("id", ""),
                    "name": best.get("name", ""),
                    "runs": best.get("stats", {}).get("totalRuns", 0),
                }
                ok(f"Found actor for {key}: {actors[key]['name']} ({actors[key]['runs']} runs)")
        except Exception as e:
            warn(f"Could not find actor for {key}: {e}")

    # Save actor config
    config_path = ROOT / "shared" / "apify_actors.json"
    with open(config_path, "w") as f:
        json.dump(actors, f, indent=2)
    ok(f"Saved actor config to {config_path}")
    return actors


# ─── Step 6: Generate Fiverr gig copy ─────────────────────────────────────────

def generate_fiverr_copy() -> bool:
    step("Generating Fiverr gig copy...")
    groq_key = os.environ.get("GROQ_API_KEY")
    together_key = os.environ.get("TOGETHER_API_KEY")

    if not (groq_key or together_key):
        warn("No AI API key available — skipping Fiverr copy generation")
        return False

    try:
        sys.path.insert(0, str(ROOT))
        from shared import groq_client

        gigs = []

        prompts = [
            {
                "title": "Blog Post Writing Gig",
                "prompt": (
                    "Write a complete Fiverr gig listing for a blog post writing service. "
                    "Include: Title (max 80 chars), Description (max 1200 chars, highlight fast delivery, "
                    "SEO optimization, unlimited revisions), 3 FAQ items with answers, and 3 packages "
                    "(Basic: 500-word post $8, Standard: 1000-word post $15, Premium: 2000-word post $25). "
                    "Format as JSON with keys: title, description, faqs (list of {q,a}), packages (list of {name,description,price,delivery_days})."
                )
            },
            {
                "title": "Resume Rewriting Gig",
                "prompt": (
                    "Write a complete Fiverr gig listing for a professional resume rewriting service. "
                    "Include: Title (max 80 chars), Description (max 1200 chars, highlight ATS optimization, "
                    "professional formatting, 24-hour delivery), 3 FAQ items with answers, and 3 packages "
                    "(Basic: 1-page resume $15, Standard: 2-page + cover letter $25, Premium: Full package $45). "
                    "Format as JSON with keys: title, description, faqs (list of {q,a}), packages (list of {name,description,price,delivery_days})."
                )
            },
            {
                "title": "Social Media Caption Pack",
                "prompt": (
                    "Write a complete Fiverr gig listing for a social media caption writing service (packs of 10). "
                    "Include: Title (max 80 chars), Description (max 1200 chars, highlight engagement-focused copy, "
                    "hashtag research, brand voice matching), 3 FAQ items with answers, and 3 packages "
                    "(Basic: 10 captions $12, Standard: 20 captions $20, Premium: 30 captions + strategy $35). "
                    "Format as JSON with keys: title, description, faqs (list of {q,a}), packages (list of {name,description,price,delivery_days})."
                )
            },
        ]

        for gig_info in prompts:
            try:
                response = groq_client.complete(
                    gig_info["prompt"],
                    system="You are a top-performing Fiverr seller. Write high-converting gig copy. Return valid JSON only.",
                    max_tokens=1500,
                    temperature=0.7,
                )
                # Try to parse JSON
                try:
                    # Strip markdown code fences if present
                    clean = response.strip()
                    if clean.startswith("```"):
                        clean = clean.split("```")[1]
                        if clean.startswith("json"):
                            clean = clean[4:]
                    gig_data = json.loads(clean)
                except json.JSONDecodeError:
                    gig_data = {"title": gig_info["title"], "raw": response}

                gigs.append({"gig_name": gig_info["title"], **gig_data})
                ok(f"Generated: {gig_info['title']}")
            except Exception as e:
                warn(f"Failed to generate {gig_info['title']}: {e}")

        output_path = ROOT / "setup" / "fiverr_gig_copy.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("FIVERR GIG COPY — Generated by setup.py\n")
            f.write("=" * 60 + "\n\n")
            for gig in gigs:
                f.write(f"GIG: {gig.get('gig_name', 'Unknown')}\n")
                f.write("-" * 40 + "\n")
                f.write(json.dumps(gig, indent=2, ensure_ascii=False))
                f.write("\n\n")

        ok(f"Saved Fiverr copy to {output_path}")
        return True

    except Exception as e:
        warn(f"Fiverr copy generation failed: {e}")
        return False


# ─── Step 7: Test Telegram ─────────────────────────────────────────────────────

def test_telegram() -> bool:
    step("Testing Telegram bot...")
    try:
        from shared import telegram_bot
        result = telegram_bot.test_connection()
        if result:
            ok("Telegram bot connected")
            telegram_bot.send("Setup wizard running... System is being configured.")
            ok("Test message sent")
            return True
        else:
            fail("Telegram bot connection failed")
            return False
    except Exception as e:
        fail(f"Telegram test failed: {e}")
        return False


# ─── Print final checklist ─────────────────────────────────────────────────────

def print_checklist(results: dict):
    header("SETUP COMPLETE — Manual Steps Remaining")

    automated = [
        ("Airtable schema created", results.get("airtable", False)),
        ("GitHub secrets set", results.get("secrets_count", 0) > 0),
        ("Groq API verified", results.get("groq", False)),
        ("Together.ai verified", results.get("together", False)),
        ("Apify actors discovered", results.get("apify", False)),
        ("Fiverr copy generated", results.get("fiverr", False)),
        ("Telegram tested", results.get("telegram", False)),
    ]

    print("\nAutomated steps:")
    for label, done in automated:
        status = "DONE" if done else "SKIPPED"
        print(f"  [{status}] {label}")

    print("\nRemaining manual steps (total ~22 min):")
    steps = [
        ("Create 8 free accounts", "~15 min",
         "groq.com, make.com, gumroad.com, airtable.com, apify.com,\n"
         "         developer.ebay.com, etsy.com/developers, porkbun.com"),
        ("Buy domain on Porkbun ($9.73)", "~2 min",
         "The only required spend. Go to porkbun.com"),
        ("Create Telegram bot", "~2 min",
         "Open Telegram → message @BotFather → /newbot → follow prompts"),
        ("Run OAuth flows", "~5 min",
         "python setup/ebay_auth.py\n"
         "         python setup/etsy_auth.py\n"
         "         python setup/pinterest_auth.py"),
        ("Import Make.com scenarios", "~3 min",
         "Go to make.com → Import → upload each JSON from make_scenarios/"),
        ("Run verify.py", "~2 min",
         "python setup/verify.py  (fix any FAILs before going live)"),
    ]

    for i, (title, time_est, detail) in enumerate(steps, 1):
        print(f"\n  {i}. {title} ({time_est})")
        print(f"     {detail}")

    print("\n" + "=" * 60)
    print("FIRST COMMAND TO RUN NEXT:")
    print("  python setup/ebay_auth.py")
    print("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="$10 Profit Powerhouse — Setup Wizard")
    parser.add_argument("--tables-only", action="store_true", help="Only recreate Airtable tables")
    parser.add_argument("--skip-fiverr", action="store_true", help="Skip Fiverr copy generation")
    parser.add_argument("--skip-secrets", action="store_true", help="Skip GitHub secrets upload")
    args = parser.parse_args()

    header("$10 Profit Powerhouse — Master Setup Wizard v2.0")
    print("This runs once. It will configure Airtable, GitHub secrets,")
    print("verify APIs, discover Apify actors, and generate Fiverr copy.")

    results = {}

    check_gh_cli()

    results["airtable"] = create_airtable_schema(tables_only=args.tables_only)

    if args.tables_only:
        print("\nTables-only mode complete.")
        return

    if not args.skip_secrets:
        results["secrets_count"] = set_github_secrets()

    ai_results = verify_ai_apis()
    results.update(ai_results)

    results["apify"] = bool(discover_apify_actors())

    if not args.skip_fiverr:
        results["fiverr"] = generate_fiverr_copy()

    results["telegram"] = test_telegram()

    print_checklist(results)


if __name__ == "__main__":
    main()
