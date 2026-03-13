"""
Stream 2 — Proposal writer: Groq writes tailored proposals, Telegram approval.
Pricing follows warm-up schedule (low month 1, increasing over time).
Usage: python stream2_freelance/proposal_writer.py [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot, airtable_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a top-rated freelance writer on Upwork and Contra with excellent reviews.
Write genuine, tailored proposals that win contracts. Be concise, specific to the job, and professional.
Never use generic templates — every proposal must reference the specific job requirements."""

# Pricing schedule (month of operation → pricing tier)
PRICING_TIERS = {
    "month_1": {
        "blog_500w": 8,
        "blog_1000w": 15,
        "resume": 15,
        "captions_10": 12,
        "research_report": 20,
    },
    "month_2": {
        "blog_500w": 15,
        "blog_1000w": 25,
        "resume": 25,
        "captions_10": 20,
        "research_report": 35,
    },
    "month_3_plus": {
        "blog_500w": 25,
        "blog_1000w": 40,
        "resume": 45,
        "captions_10": 35,
        "research_report": 60,
    },
}


def get_current_pricing() -> dict:
    """Get pricing based on warm-up week."""
    state_file = ROOT / "shared" / "warmup_state.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            start = datetime.fromisoformat(state.get("start_date", datetime.now().isoformat()))
            months = (datetime.now() - start).days // 30 + 1
            if months <= 1:
                return PRICING_TIERS["month_1"]
            elif months == 2:
                return PRICING_TIERS["month_2"]
            else:
                return PRICING_TIERS["month_3_plus"]
        except Exception:
            pass
    return PRICING_TIERS["month_1"]


def detect_job_type(title: str, description: str) -> str:
    """Detect job type from title/description to set appropriate pricing."""
    text = (title + " " + description).lower()
    if "resume" in text or "cv" in text:
        return "resume"
    if "caption" in text or "social media" in text:
        return "captions_10"
    if "research" in text or "report" in text:
        return "research_report"
    if "1000" in text or "1,000" in text or "long" in text:
        return "blog_1000w"
    return "blog_500w"


def write_proposal(job: dict) -> str:
    """Use Groq to write a tailored proposal for a job."""
    pricing = get_current_pricing()
    job_type = detect_job_type(job.get("title", ""), job.get("description", ""))
    price = pricing.get(job_type, pricing["blog_500w"])

    prompt = f"""Write a winning freelance proposal for this job posting:

Platform: {job.get('platform', 'Upwork')}
Job Title: {job.get('title', '')}
Description: {job.get('description', '')[:400]}
Budget Posted: {job.get('budget', 'Not specified')}

My proposed rate: ${price}

Requirements for the proposal:
1. Open with something specific from THEIR job posting (shows you read it)
2. Briefly state relevant experience (1-2 sentences)
3. Outline your exact approach for THEIR specific project
4. State your timeline (fast delivery is a selling point)
5. Include your rate of ${price}
6. End with a specific question that shows engagement
7. Keep it under 200 words — clients prefer concise proposals
8. Do NOT start with "I" — start with something about their project

Return only the proposal text, no JSON, no headers."""

    return groq_client.complete(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=400,
        temperature=0.7,
    )


def run(jobs: list = None, dry_run: bool = False) -> list:
    """
    Generate proposals for a list of jobs.
    If jobs is None, fetch pending jobs from Airtable.
    """
    logger.info("Starting proposal writer...")

    if jobs is None:
        # Fetch scraped jobs without proposals from Airtable
        try:
            from pyairtable import Api
            api = Api(os.environ.get("AIRTABLE_API_KEY"))
            table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Jobs")
            records = table.all(formula="Status='Scraped'", max_records=10)
            jobs = []
            for r in records:
                fields = r.get("fields", {})
                fields["airtable_id"] = r["id"]
                jobs.append(fields)
        except Exception as e:
            logger.error(f"Could not fetch jobs from Airtable: {e}")
            return []

    if not jobs:
        logger.info("No jobs to write proposals for")
        return []

    proposals = []
    for job in jobs:
        try:
            logger.info(f"Writing proposal for: {job.get('title', job.get('Title', ''))[:50]}")
            proposal_text = write_proposal(job)

            job_id = str(uuid.uuid4())[:8]
            proposal = {
                "job": job,
                "proposal": proposal_text,
                "job_id": job_id,
            }
            proposals.append(proposal)

            if dry_run:
                print(f"\n[DRY RUN] Proposal for: {job.get('title', '')}")
                print("-" * 40)
                print(proposal_text)
                print("-" * 40)
            else:
                # Send to Telegram for approval
                platform = job.get("platform", job.get("Platform", "Platform"))
                title = job.get("title", job.get("Title", "Job"))
                budget = job.get("budget", job.get("Budget", ""))
                url = job.get("url", job.get("URL", ""))

                msg = (
                    f"*New Proposal Ready — {platform}*\n\n"
                    f"*Job:* {title[:80]}\n"
                    f"*Budget:* {budget or 'Not specified'}\n"
                    f"*URL:* {url[:100] if url else 'N/A'}\n\n"
                    f"*Proposed Text:*\n```\n{proposal_text[:800]}\n```"
                )
                telegram_bot.send_approval_request(
                    message=msg,
                    approval_id=f"proposal:{job_id}",
                    approve_label="SEND IT",
                    skip_label="SKIP",
                )

                # Update Airtable status
                airtable_id = job.get("airtable_id", "")
                if airtable_id:
                    try:
                        airtable_logger.update_job_status(
                            airtable_id,
                            "Awaiting Approval",
                            {"ProposalText": proposal_text},
                        )
                    except Exception as e:
                        logger.warning(f"Could not update Airtable: {e}")

            import time
            time.sleep(2)  # Rate limiting between proposals

        except Exception as e:
            logger.error(f"Proposal generation failed for job: {e}")

    if dry_run:
        print(f"\nDry run complete. No data published. ({len(proposals)} proposals generated)")

    logger.info(f"Generated {len(proposals)} proposals")
    return proposals


def main():
    parser = argparse.ArgumentParser(description="Write and send freelance proposals")
    parser.add_argument("--dry-run", action="store_true", help="Generate without sending")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
