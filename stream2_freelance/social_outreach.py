"""
Stream 2 — Social outreach: Reddit + Twitter/X warm lead finder.
Finds people asking for writing help, drafts replies, Telegram approval.
Usage: python stream2_freelance/social_outreach.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client, telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Reddit subreddits to search
REDDIT_SUBREDDITS = [
    "entrepreneur", "smallbusiness", "startups", "SideProject",
    "forhire", "HireAWriter", "freelance",
]

# Search terms indicating someone needs writing help
WRITING_NEED_TERMS = [
    "need a writer", "looking for copywriter", "anyone write",
    "need help writing", "hire a writer", "need content writer",
    "looking for content", "need someone to write", "need blog posts written",
]

# Twitter search queries
TWITTER_QUERIES = [
    "need a copywriter",
    "looking for content writer",
    "hire freelance writer",
    "need someone to write",
    "need blog content",
]

SYSTEM_PROMPT = """You are a skilled freelance writer responding to potential clients on social media.
Your goal is to be genuinely helpful — answer their question first, then naturally mention your availability.
Never be pushy or spam-like. Write in a conversational, human tone."""


def search_reddit_leads() -> list:
    """Search Reddit for warm leads — people asking for writing help."""
    import praw
    import requests

    leads = []

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")

    if not all([client_id, client_secret, username]):
        # Fallback: use public Reddit JSON API (no auth needed for search)
        logger.info("Reddit credentials not set — using public API fallback")
        return _search_reddit_public()

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent="ProfitPowerhouse/1.0 (writing services outreach)",
        )

        for subreddit_name in REDDIT_SUBREDDITS[:4]:
            for term in WRITING_NEED_TERMS[:3]:
                try:
                    subreddit = reddit.subreddit(subreddit_name)
                    results = subreddit.search(term, sort="new", time_filter="day", limit=5)

                    for post in results:
                        # Filters: recent post, has upvotes, no existing hired comment
                        if post.score < 1:
                            continue
                        # Check if already has a "hired" comment
                        hired_found = any(
                            "hired" in str(c.body).lower() or "closed" in str(c.body).lower()
                            for c in post.comments[:5]
                        )
                        if hired_found:
                            continue

                        leads.append({
                            "source": "reddit",
                            "subreddit": subreddit_name,
                            "title": post.title,
                            "text": post.selftext[:300],
                            "url": f"https://reddit.com{post.permalink}",
                            "post_id": post.id,
                            "score": post.score,
                            "created_utc": post.created_utc,
                        })

                    time.sleep(2)

                except Exception as e:
                    logger.warning(f"Reddit search error ({subreddit_name}, {term}): {e}")

    except Exception as e:
        logger.error(f"Reddit PRAW initialization failed: {e}")
        return _search_reddit_public()

    return leads


def _search_reddit_public() -> list:
    """Public Reddit API fallback (no auth required, read-only)."""
    import requests

    leads = []
    headers = {"User-Agent": "ProfitPowerhouse/1.0 (writing lead finder)"}

    for subreddit in REDDIT_SUBREDDITS[:3]:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{subreddit}/search.json",
                params={"q": "need a writer", "sort": "new", "t": "day", "limit": 10},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            for post in posts:
                d = post.get("data", {})
                leads.append({
                    "source": "reddit",
                    "subreddit": subreddit,
                    "title": d.get("title", ""),
                    "text": d.get("selftext", "")[:300],
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "post_id": d.get("id", ""),
                    "score": d.get("score", 0),
                    "created_utc": d.get("created_utc", 0),
                })
            time.sleep(3)
        except Exception as e:
            logger.warning(f"Public Reddit API failed for r/{subreddit}: {e}")

    return leads


def search_twitter_leads() -> list:
    """Search Twitter/X for writing service leads."""
    import requests

    bearer_token = os.environ.get("TWITTER_BEARER_TOKEN")
    if not bearer_token:
        logger.info("TWITTER_BEARER_TOKEN not set — skipping Twitter search")
        return []

    leads = []
    headers = {"Authorization": f"Bearer {bearer_token}"}

    for query in TWITTER_QUERIES[:2]:
        try:
            full_query = f"{query} -is:retweet lang:en"
            resp = requests.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers=headers,
                params={
                    "query": full_query,
                    "max_results": 10,
                    "tweet.fields": "created_at,author_id,public_metrics",
                    "user.fields": "public_metrics,username",
                    "expansions": "author_id",
                },
                timeout=15,
            )

            if resp.status_code == 429:
                logger.warning("Twitter rate limit hit — stopping Twitter search")
                break

            if resp.status_code != 200:
                logger.warning(f"Twitter API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            tweets = data.get("data", [])
            users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

            for tweet in tweets:
                author = users.get(tweet.get("author_id", ""), {})
                follower_count = author.get("public_metrics", {}).get("followers_count", 0)

                # Filter: account has followers (not a bot)
                if follower_count < 10:
                    continue

                leads.append({
                    "source": "twitter",
                    "tweet_id": tweet["id"],
                    "text": tweet.get("text", ""),
                    "author": author.get("username", ""),
                    "followers": follower_count,
                    "url": f"https://twitter.com/i/web/status/{tweet['id']}",
                    "created_at": tweet.get("created_at", ""),
                })

            time.sleep(5)  # Twitter rate limiting

        except Exception as e:
            logger.warning(f"Twitter search failed for '{query}': {e}")

    return leads


def draft_reply(lead: dict) -> str:
    """Draft a genuine, helpful reply to a lead using Groq."""
    source = lead.get("source", "reddit")
    title = lead.get("title", lead.get("text", ""))[:200]
    text = lead.get("text", "")[:300]

    prompt = f"""Draft a genuine, helpful reply to this {'Reddit post' if source == 'reddit' else 'tweet'}
from someone who needs writing help:

Title/Post: {title}
Additional context: {text}

Requirements:
1. Be genuinely helpful — answer their need first
2. Keep it under 100 words
3. Naturally mention you're a freelance writer available to help
4. Don't be pushy — one soft mention of availability is enough
5. Sound human, not like a template or advertisement
6. If it's a Reddit post, write as a Reddit reply (conversational)
7. End with an invitation to DM or link to your profile

Return only the reply text."""

    return groq_client.complete(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=200,
        temperature=0.8,
    )


def post_reddit_reply(post_id: str, reply_text: str) -> bool:
    """Post a reply to a Reddit thread."""
    import praw

    try:
        reddit = praw.Reddit(
            client_id=os.environ.get("REDDIT_CLIENT_ID"),
            client_secret=os.environ.get("REDDIT_CLIENT_SECRET"),
            username=os.environ.get("REDDIT_USERNAME"),
            password=os.environ.get("REDDIT_PASSWORD"),
            user_agent="ProfitPowerhouse/1.0",
        )
        submission = reddit.submission(id=post_id)
        submission.reply(reply_text)
        logger.info(f"Posted reply to Reddit post {post_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to post Reddit reply: {e}")
        return False


def run(dry_run: bool = False) -> dict:
    """Main social outreach run."""
    logger.info("Starting social outreach...")

    # Check daily limit
    from stream2_freelance.job_scraper import get_daily_limit, get_proposals_sent_today
    social_limit = get_daily_limit("social")
    social_sent = get_proposals_sent_today("social")

    if social_sent >= social_limit:
        logger.info(f"Social outreach daily limit reached ({social_sent}/{social_limit})")
        return {"sent": 0, "limit_reached": True}

    slots = social_limit - social_sent
    logger.info(f"Social outreach: {social_sent}/{social_limit} sent today, {slots} slots remaining")

    # Find leads
    logger.info("Searching Reddit for leads...")
    reddit_leads = search_reddit_leads()
    logger.info(f"Found {len(reddit_leads)} Reddit leads")

    logger.info("Searching Twitter for leads...")
    twitter_leads = search_twitter_leads()
    logger.info(f"Found {len(twitter_leads)} Twitter leads")

    all_leads = reddit_leads + twitter_leads
    if not all_leads:
        logger.info("No leads found today")
        return {"sent": 0, "leads": 0}

    # Draft replies and send to Telegram for approval
    queued = 0
    for lead in all_leads[:slots]:
        try:
            reply_text = draft_reply(lead)
            lead_id = str(uuid.uuid4())[:8]

            if dry_run:
                source = lead.get("source", "unknown")
                print(f"\n[DRY RUN] Reply for {source} lead:")
                print(f"  Post: {lead.get('title', lead.get('text', ''))[:80]}")
                print(f"  Reply: {reply_text}")
            else:
                source = lead.get("source", "unknown")
                title = lead.get("title", lead.get("text", ""))[:80]
                url = lead.get("url", "")

                msg = (
                    f"*Social Outreach Lead — {source.title()}*\n\n"
                    f"*Post:* {title}\n"
                    f"*URL:* {url[:100]}\n\n"
                    f"*Draft Reply:*\n```\n{reply_text[:600]}\n```\n\n"
                    f"Tap APPROVE to post, SKIP to discard."
                )
                telegram_bot.send_approval_request(
                    message=msg,
                    approval_id=f"social:{lead_id}:{lead.get('post_id', lead_id)}:{source}",
                    approve_label="APPROVE",
                    skip_label="SKIP",
                )
                queued += 1

            time.sleep(2)

        except Exception as e:
            logger.error(f"Failed to draft reply for lead: {e}")

    if dry_run:
        print(f"\nDry run complete. No data published. ({len(all_leads)} leads found)")

    return {"sent": queued, "leads": len(all_leads)}


def main():
    parser = argparse.ArgumentParser(description="Find warm leads and draft outreach replies")
    parser.add_argument("--dry-run", action="store_true", help="Generate without sending")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
