"""
Stream 2 — Deliverable drafter: Groq drafts work product when a job is won.
Usage: python stream2_freelance/deliverable_drafter.py --job-id JOB_ID [--dry-run]
"""
import os
import sys
import json
import argparse
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DELIVERABLE_TYPES = {
    "blog_post": {
        "system": "You are a professional blog writer. Write engaging, SEO-optimized blog posts.",
        "template": "Write a complete {word_count}-word blog post about: {topic}\n\nRequirements:\n- Target audience: {audience}\n- Tone: professional but conversational\n- Include: introduction, 3-5 sections with headers, conclusion\n- SEO optimized for: {keywords}\n- End with a call to action",
    },
    "resume": {
        "system": "You are a professional resume writer specializing in ATS-optimized resumes.",
        "template": "Rewrite this resume for {job_title} position:\n\n{resume_content}\n\nRequirements:\n- ATS optimized\n- Action verbs throughout\n- Quantify achievements where possible\n- Clean formatting",
    },
    "social_captions": {
        "system": "You write engaging social media captions that drive engagement.",
        "template": "Write {count} social media captions for {platform} about {topic}.\nBrand voice: {brand_voice}\nInclude relevant hashtags.",
    },
    "research_report": {
        "system": "You write comprehensive, well-structured research reports.",
        "template": "Write a research report on: {topic}\n\nInclude:\n- Executive summary\n- Key findings (5-7 points)\n- Analysis\n- Recommendations\n- Sources section\n\nApproximate length: {length} words",
    },
}


def get_job_from_airtable(job_id: str) -> dict:
    """Fetch job details from Airtable."""
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Jobs")
        records = table.all(formula=f"RECORD_ID()='{job_id}'", max_records=1)
        if records:
            return {"airtable_id": records[0]["id"], **records[0]["fields"]}
    except Exception as e:
        logger.error(f"Could not fetch job {job_id}: {e}")
    return {}


def detect_deliverable_type(title: str, description: str) -> str:
    """Detect what type of deliverable to produce."""
    text = (title + " " + description).lower()
    if "resume" in text or "cv" in text:
        return "resume"
    if "caption" in text or "social media" in text:
        return "social_captions"
    if "research" in text or "report" in text:
        return "research_report"
    return "blog_post"


def draft_deliverable(job: dict, deliverable_type: str = None) -> str:
    """Draft the work product using Groq."""
    title = job.get("Title", job.get("title", ""))
    description = job.get("Description", job.get("description", ""))

    if not deliverable_type:
        deliverable_type = detect_deliverable_type(title, description)

    config = DELIVERABLE_TYPES.get(deliverable_type, DELIVERABLE_TYPES["blog_post"])

    # Build prompt from template
    if deliverable_type == "blog_post":
        # Extract topic from job title
        prompt = config["template"].format(
            word_count=500,
            topic=title.replace("Write", "").replace("blog post about", "").strip() or title,
            audience="general business audience",
            keywords=title[:50],
        )
    elif deliverable_type == "social_captions":
        prompt = config["template"].format(
            count=10,
            platform="Instagram and LinkedIn",
            topic=title,
            brand_voice="professional, engaging, authentic",
        )
    elif deliverable_type == "research_report":
        prompt = config["template"].format(
            topic=title,
            length=800,
        )
    else:
        prompt = f"Complete this freelance task:\n\nTitle: {title}\nDescription: {description}\n\nDeliver a professional, complete result."

    return groq_client.complete(
        prompt,
        system=config["system"],
        max_tokens=2000,
        temperature=0.7,
    )


def run(job_id: str = None, dry_run: bool = False) -> dict:
    """Draft a deliverable for a won job."""
    logger.info(f"Drafting deliverable for job: {job_id}")

    job = {}
    if job_id:
        job = get_job_from_airtable(job_id)

    if not job:
        logger.warning("No job data — using placeholder")
        job = {"Title": "Sample Blog Post", "description": "Write a 500-word blog post"}

    deliverable_type = detect_deliverable_type(
        job.get("Title", ""),
        job.get("Description", ""),
    )

    logger.info(f"Detected deliverable type: {deliverable_type}")
    draft = draft_deliverable(job, deliverable_type)

    if dry_run:
        print(f"\n[DRY RUN] Draft deliverable ({deliverable_type}):")
        print("-" * 40)
        print(draft[:500])
        print("...")
        print(f"\n[Full draft: {len(draft)} chars]")
        print("\nDry run complete. No data published.")
        return {"dry_run": True, "type": deliverable_type, "length": len(draft)}

    # Send draft to Telegram for review
    try:
        msg = (
            f"*Deliverable Draft Ready*\n\n"
            f"*Job:* {job.get('Title', '')[:80]}\n"
            f"*Type:* {deliverable_type}\n"
            f"*Length:* {len(draft.split())} words\n\n"
            f"*Preview:*\n```\n{draft[:400]}\n```\n\n"
            f"Full draft saved. Review and deliver to client."
        )
        telegram_bot.send(msg)
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")

    # Save draft to file
    output_file = ROOT / "shared" / f"draft_{job_id or 'latest'}.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"JOB: {job.get('Title', '')}\n")
        f.write(f"TYPE: {deliverable_type}\n")
        f.write("=" * 60 + "\n\n")
        f.write(draft)

    logger.info(f"Draft saved to {output_file}")
    return {"draft_file": str(output_file), "type": deliverable_type}


def main():
    parser = argparse.ArgumentParser(description="Draft deliverable for a won freelance job")
    parser.add_argument("--job-id", type=str, help="Airtable job record ID")
    parser.add_argument("--dry-run", action="store_true", help="Generate without saving")
    args = parser.parse_args()
    run(job_id=args.job_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
