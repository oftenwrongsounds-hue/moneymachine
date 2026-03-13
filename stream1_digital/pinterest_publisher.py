"""
Pinterest publisher — PRIMARY discovery engine for Stream 1.
Runs daily at 6:15am (after product_factory.py).
Pins every new product, re-pins top performers weekly.
Usage: python stream1_digital/pinterest_publisher.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, airtable_logger, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PINTEREST_API = "https://api.pinterest.com/v5"

# Default boards to always pin to
DEFAULT_BOARDS = ["Notion Templates", "Productivity Tools", "Free Digital Downloads"]


def _headers() -> dict:
    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        raise ValueError("PINTEREST_ACCESS_TOKEN environment variable not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def get_user_boards() -> list:
    """Get all Pinterest boards for the authenticated user."""
    import requests
    try:
        response = requests.get(
            f"{PINTEREST_API}/boards",
            headers=_headers(),
            params={"page_size": 100},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        logger.error(f"Failed to get Pinterest boards: {e}")
        return []


def get_or_create_board(board_name: str) -> str:
    """Get board ID by name, or create it if it doesn't exist."""
    import requests
    boards = get_user_boards()
    for board in boards:
        if board.get("name", "").lower() == board_name.lower():
            return board["id"]

    # Create the board
    try:
        payload = {
            "name": board_name,
            "description": f"Curated {board_name}",
            "privacy": "PUBLIC",
        }
        response = requests.post(
            f"{PINTEREST_API}/boards",
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        board_id = response.json().get("id")
        logger.info(f"Created Pinterest board: {board_name} (id: {board_id})")
        return board_id
    except Exception as e:
        logger.error(f"Failed to create board '{board_name}': {e}")
        return None


def create_pin(
    board_id: str,
    title: str,
    description: str,
    link: str,
    image_url: str = None,
    alt_text: str = "",
) -> dict:
    """Create a Pinterest pin."""
    import requests

    media = {}
    if image_url:
        media = {"source_type": "image_url", "url": image_url}
    else:
        # Use a placeholder/default image if no image URL
        media = {
            "source_type": "image_url",
            "url": "https://via.placeholder.com/1000x1500/6366F1/FFFFFF?text=" + title[:20].replace(" ", "+"),
        }

    payload = {
        "board_id": board_id,
        "title": title[:100],
        "description": description[:500],
        "link": link,
        "media_source": media,
        "alt_text": alt_text[:500] if alt_text else title[:100],
    }

    try:
        response = requests.post(
            f"{PINTEREST_API}/pins",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to create Pinterest pin: {e}")
        raise


def generate_pin_content(product_title: str, product_description: str, niche: str, gumroad_url: str) -> dict:
    """Use Groq to generate optimized Pinterest pin content."""
    prompt = f"""Create optimized Pinterest pin content for this digital product:

Product: {product_title}
Niche: {niche}
Description: {product_description[:300]}
Link: {gumroad_url}

Return JSON with:
- title: Pinterest pin title (max 100 chars, keyword-rich, compelling)
- description: Pin description (max 500 chars, includes 3-5 keywords, ends with call-to-action)
- boards: List of 3 Pinterest board names most relevant to pin this to
- alt_text: Image alt text (max 500 chars, keyword-rich)

Return only valid JSON."""

    response = groq_client.complete(
        prompt,
        system="You are a Pinterest SEO expert. Write content that maximizes discoverability and click-through rates.",
        max_tokens=600,
        temperature=0.7,
    )

    clean = response.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(clean)


def pin_product(product: dict, dry_run: bool = False) -> dict:
    """
    Pin a product to Pinterest boards.
    product dict should have: title, niche, gumroad_url, description
    """
    title = product.get("title", "")
    niche = product.get("niche", "")
    gumroad_url = product.get("gumroad_url", "")
    description = product.get("description", "")

    logger.info(f"Generating Pinterest content for: {title}")

    try:
        pin_content = generate_pin_content(title, description, niche, gumroad_url)
    except Exception as e:
        logger.warning(f"Could not generate pin content via AI, using defaults: {e}")
        pin_content = {
            "title": title[:100],
            "description": f"{description[:400]}. Download now!",
            "boards": DEFAULT_BOARDS,
            "alt_text": title,
        }

    boards_to_pin = pin_content.get("boards", DEFAULT_BOARDS)
    image_url = product.get("cover_image_url", None)

    if dry_run:
        print(f"\n[DRY RUN] Would pin to Pinterest:")
        print(json.dumps({
            "title": pin_content["title"],
            "description": pin_content["description"],
            "boards": boards_to_pin,
            "link": gumroad_url,
        }, indent=2))
        print("\nDry run complete. No data published.")
        return {"dry_run": True, "pin_content": pin_content}

    pin_ids = []
    for board_name in boards_to_pin[:3]:  # Max 3 boards per product
        try:
            board_id = get_or_create_board(board_name)
            if not board_id:
                continue

            pin_result = create_pin(
                board_id=board_id,
                title=pin_content["title"],
                description=pin_content["description"],
                link=gumroad_url,
                image_url=image_url,
                alt_text=pin_content.get("alt_text", title),
            )
            pin_id = pin_result.get("id")
            pin_ids.append(pin_id)
            logger.info(f"Pinned to board '{board_name}': pin id {pin_id}")

            import time
            time.sleep(2)  # Rate limiting between pins

        except Exception as e:
            logger.error(f"Failed to pin to board '{board_name}': {e}")

    return {
        "pin_ids": pin_ids,
        "boards": boards_to_pin,
        "pin_content": pin_content,
    }


def get_recent_products_from_airtable(limit: int = 5) -> list:
    """Get recently published products from Airtable."""
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        records = table.all(
            formula="Status='Published'",
            max_records=limit,
            sort=[{"field": "PublishedAt", "direction": "desc"}],
        )
        return [r.get("fields", {}) for r in records]
    except Exception as e:
        logger.warning(f"Could not fetch products from Airtable: {e}")
        return []


def repin_top_performers() -> int:
    """Weekly: re-pin top-performing products to seasonal boards."""
    logger.info("Re-pinning top performers to seasonal boards...")
    seasonal_boards = _get_seasonal_boards()
    repinned = 0

    products = get_recent_products_from_airtable(limit=3)
    for product in products:
        if not product.get("GumroadURL"):
            continue
        try:
            for board_name in seasonal_boards:
                board_id = get_or_create_board(board_name)
                if board_id:
                    create_pin(
                        board_id=board_id,
                        title=product.get("Title", ""),
                        description=f"Still relevant: {product.get('Title', '')}. Download now!",
                        link=product.get("GumroadURL", ""),
                    )
                    repinned += 1
                    import time
                    time.sleep(2)
        except Exception as e:
            logger.warning(f"Failed to repin {product.get('Title')}: {e}")

    logger.info(f"Re-pinned {repinned} pins to seasonal boards")
    return repinned


def _get_seasonal_boards() -> list:
    """Return relevant seasonal board names based on current month."""
    month = datetime.now().month
    seasonal = {
        1: ["New Year Planning", "January Organization", "2026 Goals"],
        2: ["Valentine's Day Planning", "February Productivity"],
        3: ["Spring Planning", "March Organization"],
        4: ["Spring Clean Your Business", "Q2 Planning"],
        5: ["May Productivity", "Spring Business Tools"],
        6: ["Summer Business Planning", "Mid-Year Review"],
        7: ["Summer Productivity", "July Organization"],
        8: ["Back to School", "August Planning", "Fall Prep"],
        9: ["Fall Business Planning", "September Goals"],
        10: ["Q4 Planning", "October Business Tools"],
        11: ["Holiday Season Planning", "November Productivity"],
        12: ["Year End Review", "Holiday Business Planning"],
    }
    return seasonal.get(month, ["Productivity Tools"])


def run(dry_run: bool = False) -> dict:
    """Main Pinterest publisher run — pins today's new product."""
    logger.info("Starting Pinterest publisher run...")

    # Get most recently published product from Airtable
    products = get_recent_products_from_airtable(limit=1)

    if not products:
        logger.warning("No recent products found in Airtable — nothing to pin")
        if dry_run:
            print("\nDry run complete. No data published.")
        return {"pinned": 0}

    product = products[0]
    result = pin_product(product, dry_run=dry_run)

    if not dry_run and result.get("pin_ids"):
        # Update Airtable with pin IDs
        try:
            airtable_logger.log_product(
                title=product.get("Title", ""),
                niche=product.get("Niche", ""),
                gumroad_url=product.get("GumroadURL", ""),
                pinterest_pin_id=",".join(result["pin_ids"]),
                status="Pinned",
            )
        except Exception as e:
            logger.warning(f"Could not update Airtable with pin IDs: {e}")

        try:
            telegram_bot.send(
                f"*Pinterest pins posted!*\n"
                f"Product: {product.get('Title', '')}\n"
                f"Pins: {len(result['pin_ids'])} boards\n"
                f"IDs: {', '.join(result['pin_ids'][:3])}"
            )
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Pinterest publisher — daily pin creation")
    parser.add_argument("--dry-run", action="store_true", help="Generate without publishing")
    parser.add_argument("--repin", action="store_true", help="Re-pin top performers to seasonal boards")
    args = parser.parse_args()

    if args.repin:
        count = repin_top_performers()
        print(f"Re-pinned {count} pins")
    else:
        run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
