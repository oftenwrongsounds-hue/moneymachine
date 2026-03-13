"""
Stream 1 — Digital Products: Daily product generation.
Groq generates hyper-niche products, publishes to Gumroad, logs to Airtable.
Usage: python stream1_digital/product_factory.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Hyper-niche product categories from concept sheet
NICHE_EXAMPLES = [
    "Ceramic artists — glaze recipe tracker, kiln log, commission tracker",
    "Indie game developers — sprint planner, asset tracker, bug log, release checklist",
    "Dog trainers — client session notes, training plan, progress tracker",
    "Tattoo artists — client intake, design request tracker, aftercare instructions",
    "Beekeepers — hive inspection log, honey harvest tracker, seasonal calendar",
    "Food truck owners — daily sales log, inventory tracker, event booking system",
    "Podcast producers — episode planner, guest tracker, show notes template",
    "Etsy sellers — order tracker, listing SEO planner, supplier contacts",
]

SYSTEM_PROMPT = """You are a digital product creator specializing in hyper-niche Notion templates
and planners. You create extremely specific templates for narrow audiences that have almost zero
competition. Focus on commercial intent and practical utility."""


def generate_product_idea(niche_gap: str = None) -> dict:
    """Use Groq to generate a complete product idea for a niche."""
    if not niche_gap:
        import random
        niche_gap = random.choice(NICHE_EXAMPLES)

    prompt = f"""Create a complete hyper-niche digital product (Notion template or planner) for this niche:
{niche_gap}

Return a JSON object with these exact keys:
- title: Product title (max 60 chars, specific and compelling)
- tagline: One-line description (max 120 chars)
- niche: The specific audience (e.g. "ceramic artists")
- description: Full product description for Gumroad (300-400 words, includes features, who it's for, what's included)
- price: Suggested price in USD (between 7 and 27, use 9, 12, 15, 19, or 27)
- tags: List of 5-8 relevant tags/keywords
- cover_image_prompt: Detailed prompt for generating a cover image (for use with image AI)
- pinterest_title: Pinterest pin title (max 100 chars, keyword-rich)
- pinterest_description: Pinterest pin description (max 500 chars, includes keywords, call to action)
- boards: List of 3 Pinterest board names to pin to
- reddit_subreddit: Most relevant subreddit for organic sharing

Return only valid JSON, no other text."""

    response = groq_client.complete(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=1500,
        temperature=0.8,
    )

    # Parse JSON
    clean = response.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    return json.loads(clean)


def generate_product_file(product: dict) -> str:
    """Generate the actual downloadable product content."""
    prompt = f"""Create a complete Notion template for: {product['title']}
Target audience: {product['niche']}

Generate a detailed Notion page structure with:
1. Page title and cover image placeholder description
2. Database schemas (with all properties/columns)
3. Sample data rows (3-5 examples)
4. Instructions section
5. Quick start guide

Format as a markdown document that can be easily converted to a Notion template.
Make it genuinely useful and professional quality."""

    return groq_client.complete(prompt, system=SYSTEM_PROMPT, max_tokens=2000, temperature=0.7)


def run(dry_run: bool = False, niche_gap: str = None) -> dict:
    """Main product generation run."""
    logger.info("Starting product factory run...")

    try:
        product = generate_product_idea(niche_gap)
        logger.info(f"Generated product idea: {product['title']}")
    except Exception as e:
        logger.error(f"Failed to generate product idea: {e}")
        raise

    if dry_run:
        print("\n[DRY RUN] Product would be created:")
        print(json.dumps(product, indent=2, ensure_ascii=False))
        print("\nDry run complete. No data published.")
        return {"dry_run": True, "product": product}

    # Generate actual product content
    try:
        content = generate_product_file(product)
        logger.info("Generated product content")
    except Exception as e:
        logger.warning(f"Could not generate product content: {e}")
        content = f"# {product['title']}\n\n{product['description']}"

    # Publish to Gumroad
    from stream1_digital import gumroad_publisher
    try:
        gumroad_result = gumroad_publisher.publish(
            title=product["title"],
            description=product["description"],
            price=int(product["price"] * 100),  # cents
            tags=product.get("tags", []),
            content=content,
        )
        gumroad_url = gumroad_result.get("product", {}).get("short_url", "")
        logger.info(f"Published to Gumroad: {gumroad_url}")
    except Exception as e:
        logger.error(f"Gumroad publish failed: {e}")
        gumroad_url = ""

    # Log to Airtable
    try:
        airtable_record = airtable_logger.log_product(
            title=product["title"],
            niche=product["niche"],
            gumroad_url=gumroad_url,
            price=product["price"],
            status="Published",
        )
        airtable_id = airtable_record.get("id", "")
    except Exception as e:
        logger.warning(f"Airtable logging failed: {e}")
        airtable_id = ""

    # Send Telegram notification
    try:
        msg = (
            f"*New product published!*\n\n"
            f"*Title:* {product['title']}\n"
            f"*Niche:* {product['niche']}\n"
            f"*Price:* ${product['price']}\n"
            f"*URL:* {gumroad_url or 'Check Gumroad'}\n\n"
            f"Pinterest publisher will pin this at 6:15am"
        )
        telegram_bot.send(msg)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

    result = {
        "product": product,
        "gumroad_url": gumroad_url,
        "airtable_id": airtable_id,
    }
    logger.info(f"Product factory complete: {product['title']}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate and publish daily digital product")
    parser.add_argument("--dry-run", action="store_true", help="Generate without publishing")
    parser.add_argument("--niche", type=str, default=None, help="Override niche gap")
    args = parser.parse_args()

    run(dry_run=args.dry_run, niche_gap=args.niche)


if __name__ == "__main__":
    main()
