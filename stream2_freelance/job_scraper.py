"""
Stream 2 — Freelance job scraper: Upwork RSS + Contra.com + Twitter/X.
Warm-up rate limits enforced in code. Telegram approval for proposals.
Usage: python stream2_freelance/job_scraper.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Warm-up schedule — enforced in code
WARMUP_LIMITS = {
    "upwork": {1: 1, 2: 2, 3: 3, 4: 5},   # week: max proposals/day
    "contra": {1: 2, 3: 3, 4: 5},
    "social": {1: 1, 2: 2, 3: 3},
}

UPWORK_RSS_FEEDS = [
    "https://www.upwork.com/ab/feed/jobs/rss?q=blog+post+writing&sort=recency",
    "https://www.upwork.com/ab/feed/jobs/rss?q=copywriting&sort=recency",
    "https://www.upwork.com/ab/feed/jobs/rss?q=content+writer&sort=recency",
    "https://www.upwork.com/ab/feed/jobs/rss?q=resume+writing&sort=recency",
]

CONTRA_SEARCH_URL = "https://contra.com/search?q=content+writer&type=opportunity"

# Writing-related keywords for Contra scraping
CONTRA_KEYWORDS = [
    "content writer", "blog writer", "copywriter", "resume writer",
    "social media content", "article writer",
]


def get_week_number() -> int:
    """Determine which warm-up week we're in based on a start date file."""
    state_file = ROOT / "shared" / "warmup_state.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            start = datetime.fromisoformat(state.get("start_date", datetime.now().isoformat()))
            weeks = (datetime.now() - start).days // 7 + 1
            return min(weeks, 4)
        except Exception:
            pass
    # Initialize warm-up state
    with open(state_file, "w") as f:
        json.dump({"start_date": datetime.now().isoformat()}, f)
    return 1


def get_daily_limit(platform: str) -> int:
    """Get max proposals per day for current warm-up week."""
    week = get_week_number()
    limits = WARMUP_LIMITS.get(platform, {})
    # Find the limit for current week (use highest week <= current)
    for w in sorted(limits.keys(), reverse=True):
        if week >= w:
            return limits[w]
    return 1


def get_proposals_sent_today(platform: str) -> int:
    """Check how many proposals were sent today for a platform."""
    state_file = ROOT / "shared" / f"proposals_today_{platform}.json"
    if not state_file.exists():
        return 0
    try:
        with open(state_file) as f:
            data = json.load(f)
        if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return data.get("count", 0)
        return 0
    except Exception:
        return 0


def increment_proposals_today(platform: str):
    """Increment the daily proposal counter."""
    state_file = ROOT / "shared" / f"proposals_today_{platform}.json"
    count = get_proposals_sent_today(platform) + 1
    with open(state_file, "w") as f:
        json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "count": count}, f)


def scrape_upwork_rss() -> list:
    """Scrape Upwork RSS feeds for writing jobs."""
    import requests

    jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ProfitPowerhouse/1.0)"}

    for feed_url in UPWORK_RSS_FEEDS[:2]:  # Limit feeds per run
        try:
            resp = requests.get(feed_url, headers=headers, timeout=20)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            channel = root.find("channel")
            if not channel:
                continue

            items = channel.findall("item")
            for item in items[:5]:  # Max 5 per feed
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                description = item.findtext("description", "").strip()
                pub_date = item.findtext("pubDate", "").strip()

                # Parse budget from description
                budget = ""
                if "$" in description:
                    import re
                    budget_match = re.search(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?", description)
                    if budget_match:
                        budget = budget_match.group()

                if title and link:
                    jobs.append({
                        "platform": "Upwork",
                        "title": title[:200],
                        "url": link,
                        "description": description[:500],
                        "budget": budget,
                        "posted_at": pub_date,
                    })

            time.sleep(3)  # Rate limiting

        except Exception as e:
            logger.warning(f"Upwork RSS failed for {feed_url}: {e}")

    return jobs


def scrape_contra_jobs() -> list:
    """Scrape Contra.com public project feed for writing work."""
    from bs4 import BeautifulSoup
    import requests

    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for keyword in CONTRA_KEYWORDS[:2]:
        try:
            url = f"https://contra.com/search?q={quote(keyword)}&type=opportunity"
            time.sleep(10)  # Respectful rate limiting

            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find job cards (adapt to current Contra DOM)
            job_cards = soup.find_all("article") or soup.find_all(attrs={"data-testid": lambda x: x and "project" in x.lower()})

            for card in job_cards[:5]:
                title_el = card.find("h2") or card.find("h3") or card.find(attrs={"class": lambda c: c and "title" in " ".join(c).lower()})
                link_el = card.find("a", href=True)

                if title_el:
                    title = title_el.get_text(strip=True)
                    link = link_el["href"] if link_el else ""
                    if not link.startswith("http"):
                        link = "https://contra.com" + link

                    jobs.append({
                        "platform": "Contra",
                        "title": title[:200],
                        "url": link,
                        "description": card.get_text(strip=True)[:300],
                        "budget": "",
                        "posted_at": "",
                    })

        except Exception as e:
            logger.warning(f"Contra scrape failed for '{keyword}': {e}")

    return jobs


def run(dry_run: bool = False) -> list:
    """Main job scraper run."""
    logger.info("Starting job scraper...")

    all_jobs = []

    # Check warm-up limits
    upwork_limit = get_daily_limit("upwork")
    contra_limit = get_daily_limit("contra")
    upwork_sent = get_proposals_sent_today("upwork")
    contra_sent = get_proposals_sent_today("contra")

    logger.info(f"Warm-up week {get_week_number()}: Upwork {upwork_sent}/{upwork_limit}, Contra {contra_sent}/{contra_limit}")

    # Scrape Upwork
    if upwork_sent < upwork_limit:
        logger.info("Scraping Upwork RSS...")
        upwork_jobs = scrape_upwork_rss()
        slots = upwork_limit - upwork_sent
        all_jobs.extend(upwork_jobs[:slots])
        logger.info(f"Found {len(upwork_jobs)} Upwork jobs, queuing {min(len(upwork_jobs), slots)}")
    else:
        logger.info("Upwork daily limit reached — skipping")

    # Scrape Contra
    if contra_sent < contra_limit:
        logger.info("Scraping Contra.com...")
        contra_jobs = scrape_contra_jobs()
        slots = contra_limit - contra_sent
        all_jobs.extend(contra_jobs[:slots])
        logger.info(f"Found {len(contra_jobs)} Contra jobs, queuing {min(len(contra_jobs), slots)}")
    else:
        logger.info("Contra daily limit reached — skipping")

    if not all_jobs:
        logger.info("No new jobs found or daily limits reached")
        if dry_run:
            print("\nDry run complete. No data published.")
        return []

    if dry_run:
        print(f"\n[DRY RUN] Found {len(all_jobs)} jobs:")
        for job in all_jobs:
            print(f"  [{job['platform']}] {job['title']} — {job.get('budget', 'Budget TBD')}")
        print("\nDry run complete. No data published.")
        return all_jobs

    # Log jobs to Airtable and queue for proposal_writer
    logged = []
    for job in all_jobs:
        try:
            record = airtable_logger.log_job(
                platform=job["platform"],
                title=job["title"],
                budget=job.get("budget", ""),
                url=job["url"],
                status="Scraped",
            )
            job["airtable_id"] = record.get("id", "")
            logged.append(job)
        except Exception as e:
            logger.warning(f"Could not log job to Airtable: {e}")

    logger.info(f"Logged {len(logged)} jobs — passing to proposal_writer.py")

    # Import proposal writer and generate proposals
    try:
        from stream2_freelance import proposal_writer
        proposal_writer.run(jobs=logged)
    except Exception as e:
        logger.error(f"Proposal writer failed: {e}")

    return logged


def main():
    parser = argparse.ArgumentParser(description="Scrape freelance jobs from Upwork and Contra")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without publishing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
