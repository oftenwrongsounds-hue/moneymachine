"""
Stream 1 — Reddit seeder: free lite version + Reddit post draft for Telegram approval.
Semi-automated — you tap APPROVE in Telegram before it posts.
Usage: python stream1_digital/reddit_seeder.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_newest_product() -> dict:
    """Get the most recently published product from Airtable."""
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        records = table.all(
            formula="Status='Published'",
            max_records=1,
            sort=[{"field": "PublishedAt", "direction": "desc"}],
        )
        if records:
            return records[0]["fields"]
    except Exception as e:
        logger.warning(f"Could not fetch product from Airtable: {e}")
    return {}


def generate_free_lite_version(product: dict) -> str:
    """Generate a stripped-down free version of the product."""
    title = product.get("Title", "")
    niche = product.get("Niche", "")

    prompt = f"""Create a FREE lite version of this digital product:
Title: {title}
Niche: {niche}

The free version should:
1. Be genuinely useful (not just a teaser)
2. Include 1-2 of the most useful features from the full version
3. Be formatted as a clear, ready-to-use template or guide
4. Be short enough to share as text (200-400 words max)

Include a note at the end that a full version with [X additional features] is available.

Return the free template content as plain text."""

    return groq_client.complete(
        prompt,
        system="You create genuinely helpful free templates. They must be useful standalone, not just teasers.",
        max_tokens=800,
        temperature=0.7,
    )


def generate_reddit_post(product: dict, free_content: str, subreddit: str) -> dict:
    """Generate a genuine Reddit post with the free template."""
    title = product.get("Title", "")
    niche = product.get("Niche", "")
    gumroad_url = product.get("GumroadURL", "")

    prompt = f"""Write a genuine Reddit post for r/{subreddit} sharing a free {niche} template.

Template name: {title}
Free content to share: {free_content[:300]}
Full version URL: {gumroad_url}

Requirements:
1. Post title: Compelling, community-focused (not promotional). Max 300 chars.
2. Post body:
   - Start by addressing a common pain point for this community
   - Share the free template naturally
   - Keep any mention of the paid version brief and at the end (1 sentence max)
   - Sound like a genuine community member sharing something useful
   - 200-350 words
3. This MUST read as genuinely helpful, not as advertising
4. Reddit will remove promotional posts — this needs to be authentically useful

Return JSON with keys: title, body"""

    response = groq_client.complete(
        prompt,
        system="You write Reddit posts that are genuinely helpful and don't feel like advertising. Reddit is extremely sensitive to spam.",
        max_tokens=600,
        temperature=0.8,
    )

    clean = response.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(clean)


def get_best_subreddit(niche: str) -> str:
    """Find the best subreddit for a given niche."""
    niche_subreddits = {
        "ceramic": "pottery",
        "pottery": "pottery",
        "game dev": "gamedev",
        "indie game": "gamedev",
        "dog trainer": "dogs",
        "tattoo": "tattoo",
        "beekeeper": "beekeeping",
        "food truck": "foodtrucks",
        "podcast": "podcasting",
        "etsy seller": "Etsy",
        "notion": "Notion",
        "productivity": "productivity",
        "freelance": "freelance",
        "small business": "smallbusiness",
    }

    niche_lower = niche.lower()
    for key, sub in niche_subreddits.items():
        if key in niche_lower:
            return sub

    # Default to Notion if it's a template
    return "Notion"


def post_to_reddit(subreddit: str, title: str, body: str) -> bool:
    """Post to Reddit using PRAW."""
    try:
        import praw
        reddit = praw.Reddit(
            client_id=os.environ.get("REDDIT_CLIENT_ID"),
            client_secret=os.environ.get("REDDIT_CLIENT_SECRET"),
            username=os.environ.get("REDDIT_USERNAME"),
            password=os.environ.get("REDDIT_PASSWORD"),
            user_agent="ProfitPowerhouse/1.0",
        )
        sub = reddit.subreddit(subreddit)
        sub.submit(title=title, selftext=body)
        logger.info(f"Posted to r/{subreddit}: {title[:50]}")
        return True
    except Exception as e:
        logger.error(f"Reddit post failed: {e}")
        return False


def run(dry_run: bool = False) -> dict:
    """Main Reddit seeder run."""
    logger.info("Starting Reddit seeder...")

    product = get_newest_product()
    if not product:
        logger.warning("No products found in Airtable")
        if dry_run:
            print("\nDry run complete. No data published.")
        return {"posted": False}

    niche = product.get("Niche", "productivity")
    subreddit = get_best_subreddit(niche)

    logger.info(f"Generating free lite version for: {product.get('Title', '')}")
    try:
        free_content = generate_free_lite_version(product)
    except Exception as e:
        logger.error(f"Could not generate free content: {e}")
        return {"posted": False}

    logger.info(f"Generating Reddit post for r/{subreddit}...")
    try:
        post_data = generate_reddit_post(product, free_content, subreddit)
    except Exception as e:
        logger.error(f"Could not generate Reddit post: {e}")
        return {"posted": False}

    post_id = str(uuid.uuid4())[:8]

    if dry_run:
        print(f"\n[DRY RUN] Reddit post draft:")
        print(f"  Subreddit: r/{subreddit}")
        print(f"  Title: {post_data.get('title', '')}")
        print(f"  Body preview: {post_data.get('body', '')[:200]}...")
        print("\nDry run complete. No data published.")
        return {"dry_run": True, "post": post_data}

    # Send to Telegram for approval
    msg = (
        f"*Reddit Seeder — Approval Required*\n\n"
        f"*Product:* {product.get('Title', '')}\n"
        f"*Subreddit:* r/{subreddit}\n\n"
        f"*Post Title:*\n_{post_data.get('title', '')}_\n\n"
        f"*Post Body Preview:*\n```\n{post_data.get('body', '')[:500]}\n```\n\n"
        f"Tap APPROVE to post, SKIP to discard.\n"
        f"⚠️ Review carefully — Reddit bans promotional content."
    )

    # Store post data for retrieval when approved
    post_store = ROOT / "shared" / f"pending_reddit_{post_id}.json"
    with open(post_store, "w") as f:
        json.dump({
            "subreddit": subreddit,
            "title": post_data.get("title", ""),
            "body": post_data.get("body", ""),
            "product_title": product.get("Title", ""),
        }, f)

    telegram_bot.send_approval_request(
        message=msg,
        approval_id=f"reddit:{post_id}",
        approve_label="APPROVE",
        skip_label="SKIP",
    )

    logger.info(f"Sent Reddit post draft to Telegram for approval (id: {post_id})")
    return {"pending_approval": True, "post_id": post_id, "subreddit": subreddit}


def main():
    parser = argparse.ArgumentParser(description="Generate and send Reddit post draft for approval")
    parser.add_argument("--dry-run", action="store_true", help="Generate without sending")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
