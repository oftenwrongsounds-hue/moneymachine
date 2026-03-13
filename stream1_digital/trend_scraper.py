"""
Trend scraper — finds hyper-niche gaps via Apify + Etsy + Reddit.
Sends gap list to Groq for ranking. Returns top 3 niches for product_factory.py.
Usage: python stream1_digital/trend_scraper.py [--dry-run]
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
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ETSY_API = "https://openapi.etsy.com/v3/application"
APIFY_API = "https://api.apify.com/v2"


def _etsy_headers() -> dict:
    key = os.environ.get("ETSY_API_KEY")
    if not key:
        raise ValueError("ETSY_API_KEY not set")
    return {"x-api-key": key}


def scrape_etsy_niches(keywords: list = None) -> list:
    """
    Search Etsy for broad template terms, find subcategories with recent sales but few listings.
    Falls back to BeautifulSoup if Apify credit exhausted.
    """
    import requests

    if keywords is None:
        keywords = ["notion template", "planner template", "tracker template", "worksheet", "digital planner"]

    niches = []

    for keyword in keywords[:3]:  # Rate limit: max 3 searches
        try:
            # Use Etsy search API
            resp = requests.get(
                f"{ETSY_API}/listings/active",
                headers=_etsy_headers(),
                params={
                    "keywords": keyword,
                    "sort_on": "created",
                    "limit": 25,
                    "includes": ["tags"],
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            listings = data.get("results", [])
            for listing in listings:
                tags = listing.get("tags", [])
                title = listing.get("title", "")
                # Extract potential niche from tags
                for tag in tags:
                    if len(tag) > 8 and tag not in ["template", "notion", "digital", "planner", "printable"]:
                        niches.append({
                            "keyword": tag,
                            "source": "etsy",
                            "listing_title": title[:60],
                        })

            time.sleep(1)

        except Exception as e:
            logger.warning(f"Etsy search failed for '{keyword}': {e}")
            # Fallback: BeautifulSoup direct scrape
            try:
                niches.extend(_scrape_etsy_direct(keyword))
            except Exception as e2:
                logger.warning(f"BeautifulSoup fallback also failed: {e2}")

    return niches


def _scrape_etsy_direct(keyword: str) -> list:
    """BeautifulSoup fallback for Etsy trend scraping."""
    from bs4 import BeautifulSoup
    import requests
    import urllib.parse

    url = f"https://www.etsy.com/search?q={urllib.parse.quote(keyword)}&order=date_desc"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    time.sleep(8)  # Rate limiting

    resp = requests.get(url, headers=headers, timeout=20)
    soup = BeautifulSoup(resp.text, "html.parser")

    niches = []
    # Extract listing titles to identify sub-niches
    listings = soup.find_all("h3", class_=lambda c: c and "search-listing" in c.lower())
    for listing in listings[:10]:
        title = listing.get_text(strip=True)
        if title:
            niches.append({"keyword": title[:50], "source": "etsy_direct", "listing_title": title[:60]})

    return niches


def scrape_reddit_gaps(subreddits: list = None) -> list:
    """Search Reddit for 'does anyone have a template for...' posts."""
    import requests

    if subreddits is None:
        subreddits = ["Notion", "productivity", "smallbusiness", "freelance", "Entrepreneur"]

    gaps = []
    queries = [
        "does anyone have a template for",
        "looking for a planner for",
        "need a tracker for",
        "anyone made a notion template for",
        "need help organizing my",
    ]

    for subreddit in subreddits[:3]:
        for query in queries[:2]:
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{subreddit}/search.json",
                    params={"q": query, "sort": "new", "limit": 10, "t": "month"},
                    headers={"User-Agent": "ProfitPowerhouse/1.0 (template research bot)"},
                    timeout=15,
                )
                resp.raise_for_status()
                posts = resp.json().get("data", {}).get("children", [])

                for post in posts:
                    post_data = post.get("data", {})
                    title = post_data.get("title", "")
                    if query.split()[0] in title.lower():
                        gaps.append({
                            "title": title,
                            "subreddit": subreddit,
                            "score": post_data.get("score", 0),
                            "url": post_data.get("url", ""),
                        })

                time.sleep(2)  # Reddit rate limiting

            except Exception as e:
                logger.warning(f"Reddit search failed for r/{subreddit}: {e}")

    return gaps


def check_pinterest_trends() -> list:
    """Check Pinterest Trends API for rising search terms."""
    import requests

    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        return []

    trending_terms = []
    try:
        # Pinterest Trends endpoint
        resp = requests.get(
            "https://api.pinterest.com/v5/trends/keywords",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "region": "US",
                "trend_type": "growing",
                "interests": ["productivity", "organization", "business", "education"],
                "genders": ["female", "male", "unknown"],
                "ages": ["35-44", "25-34", "18-24"],
                "limit": 20,
            },
            timeout=15,
        )
        resp.raise_for_status()
        trends = resp.json().get("trends", [])
        for trend in trends:
            keyword = trend.get("keyword", "")
            if keyword:
                trending_terms.append({
                    "keyword": keyword,
                    "normalized_score": trend.get("trend_data", {}).get("weekly_trend_score", 0),
                    "source": "pinterest_trends",
                })
    except Exception as e:
        logger.warning(f"Pinterest Trends API failed: {e}")

    return trending_terms


def rank_niches_with_ai(raw_niches: list, reddit_gaps: list, pinterest_trends: list) -> list:
    """Send gap list to Groq for ranking by commercial intent and competition."""
    niche_text = "\n".join([f"- {n.get('keyword', n.get('title', ''))}" for n in raw_niches[:20]])
    gap_text = "\n".join([f"- {g['title']} (r/{g['subreddit']}, score: {g['score']})" for g in reddit_gaps[:10]])
    trend_text = "\n".join([f"- {t['keyword']} (trend score: {t.get('normalized_score', 0)})" for t in pinterest_trends[:10]])

    prompt = f"""Analyze these potential product niches from Etsy, Reddit, and Pinterest.
Rank them by commercial intent and competition level.

ETSY NICHE SIGNALS:
{niche_text or 'No data'}

REDDIT BUYER REQUESTS (people asking for templates):
{gap_text or 'No data'}

PINTEREST TRENDING:
{trend_text or 'No data'}

Return a JSON array of the TOP 3 niches with:
- niche: specific audience description
- product_idea: concrete product to build
- reasoning: why this niche has high commercial intent and low competition
- estimated_competition: "very low" | "low" | "medium"
- reddit_subreddit: best subreddit for this niche

Return only valid JSON array."""

    response = groq_client.complete(
        prompt,
        system="You are an expert at finding underserved niches for digital products. Prioritize specificity over breadth.",
        max_tokens=1000,
        temperature=0.7,
    )

    clean = response.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(clean)


def run(dry_run: bool = False) -> list:
    """Main trend scraper run. Returns top 3 niche opportunities."""
    logger.info("Starting trend scraper...")

    logger.info("Scraping Etsy for niche signals...")
    etsy_niches = scrape_etsy_niches()
    logger.info(f"Found {len(etsy_niches)} Etsy signals")

    logger.info("Searching Reddit for buyer requests...")
    reddit_gaps = scrape_reddit_gaps()
    logger.info(f"Found {len(reddit_gaps)} Reddit gaps")

    logger.info("Checking Pinterest trends...")
    pinterest_trends = check_pinterest_trends()
    logger.info(f"Found {len(pinterest_trends)} Pinterest trends")

    logger.info("Ranking niches with Groq...")
    try:
        top_niches = rank_niches_with_ai(etsy_niches, reddit_gaps, pinterest_trends)
    except Exception as e:
        logger.error(f"AI ranking failed: {e}")
        top_niches = [{"niche": "Notion power users", "product_idea": "Weekly review template", "reasoning": "Fallback"}]

    if dry_run:
        print("\n[DRY RUN] Top niche opportunities:")
        print(json.dumps(top_niches, indent=2))
        print("\nDry run complete. No data published.")
    else:
        logger.info("Top 3 niche opportunities:")
        for i, niche in enumerate(top_niches, 1):
            logger.info(f"  {i}. {niche.get('niche')} — {niche.get('product_idea')}")

        # Save to shared state for product_factory.py to pick up
        output_path = ROOT / "shared" / "today_niches.json"
        with open(output_path, "w") as f:
            json.dump(top_niches, f, indent=2)
        logger.info(f"Saved niche data to {output_path}")

    return top_niches


def main():
    parser = argparse.ArgumentParser(description="Scrape trends and find hyper-niche product gaps")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving output")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
