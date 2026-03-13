"""
Stream 2 — Upsell sender: post-delivery upsell email via Gmail API.
Sends personalized upsell email linking to relevant digital products.
Usage: python stream2_freelance/upsell_sender.py --job-id JOB_ID [--dry-run]
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
load_dotenv(ROOT / ".env", override=True)

from shared import groq_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_gmail_service():
    """Get authenticated Gmail API service."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    import pickle

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds = None

    token_file = ROOT / "shared" / "gmail_token.pickle"
    if token_file.exists():
        with open(token_file, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds_file = os.environ.get("GMAIL_CREDENTIALS", "credentials.json")
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def send_upsell_email(
    to_email: str,
    client_name: str,
    job_title: str,
    relevant_product: dict,
    dry_run: bool = False,
) -> bool:
    """Send a personalized upsell email after job delivery."""
    product_title = relevant_product.get("Title", "")
    product_url = relevant_product.get("GumroadURL", "")
    product_price = relevant_product.get("Price", 0)

    prompt = f"""Write a short, genuine post-delivery follow-up email that mentions a relevant digital product.

Context:
- Client name: {client_name or 'there'}
- Job just delivered: {job_title}
- Product to mention: {product_title} (${product_price})
- Product URL: {product_url}

Requirements:
1. Lead with genuine thanks for the opportunity
2. Reference something specific about their project (keep it general since you're AI)
3. Mention the product naturally — "I also put together a template that might help with..."
4. 1 soft mention only — not pushy at all
5. Max 100 words
6. Include Calendly link for future projects
7. Return format: Subject line on first line, then blank line, then body

Return only the email text."""

    try:
        email_text = groq_client.complete(
            prompt,
            system="You write brief, genuine follow-up emails. Never pushy. Always helpful.",
            max_tokens=300,
            temperature=0.7,
        )

        lines = email_text.strip().split("\n")
        subject = lines[0].replace("Subject:", "").strip() if lines else "Following up"
        body = "\n".join(lines[2:]) if len(lines) > 2 else email_text

        if dry_run:
            print(f"\n[DRY RUN] Would send upsell email to: {to_email}")
            print(f"Subject: {subject}")
            print(f"Body: {body[:200]}...")
            return True

        # Send via Gmail API
        import base64
        from email.mime.text import MIMEText

        service = get_gmail_service()
        msg = MIMEText(body)
        msg["to"] = to_email
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Upsell email sent to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Upsell email failed: {e}")
        return False


def find_relevant_product(job_title: str) -> dict:
    """Find the most relevant digital product for upsell."""
    try:
        from pyairtable import Api
        api = Api(os.environ.get("AIRTABLE_API_KEY"))
        table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Products")
        products = table.all(formula="Status='Published'", max_records=10)

        if not products:
            return {}

        # Simple relevance: find product with most overlapping words
        job_words = set(job_title.lower().split())
        best = None
        best_score = 0

        for p in products:
            fields = p.get("fields", {})
            product_words = set((fields.get("Title", "") + " " + fields.get("Niche", "")).lower().split())
            score = len(job_words & product_words)
            if score > best_score:
                best_score = score
                best = fields

        return best or products[0].get("fields", {})
    except Exception as e:
        logger.warning(f"Could not find relevant product: {e}")
        return {}


def run(job_id: str = None, dry_run: bool = False) -> bool:
    """Send upsell email for a completed job."""
    logger.info(f"Running upsell sender for job: {job_id}")

    # Get job details from Airtable
    job = {}
    client_email = ""
    if job_id:
        try:
            from pyairtable import Api
            api = Api(os.environ.get("AIRTABLE_API_KEY"))
            table = api.table(os.environ.get("AIRTABLE_BASE_ID"), "Jobs")
            records = table.all(formula=f"RECORD_ID()='{job_id}'", max_records=1)
            if records:
                job = records[0].get("fields", {})
                client_email = job.get("ClientEmail", "")
        except Exception as e:
            logger.error(f"Could not fetch job: {e}")

    if not client_email:
        logger.warning("No client email found — cannot send upsell")
        if dry_run:
            print("\nDry run complete. No data published.")
        return False

    relevant_product = find_relevant_product(job.get("Title", ""))
    if not relevant_product:
        logger.info("No relevant product found for upsell")
        return False

    return send_upsell_email(
        to_email=client_email,
        client_name=job.get("ClientName", ""),
        job_title=job.get("Title", ""),
        relevant_product=relevant_product,
        dry_run=dry_run,
    )


def main():
    parser = argparse.ArgumentParser(description="Send post-delivery upsell email")
    parser.add_argument("--job-id", type=str, help="Airtable job record ID")
    parser.add_argument("--dry-run", action="store_true", help="Generate without sending")
    args = parser.parse_args()
    result = run(job_id=args.job_id, dry_run=args.dry_run)
    if args.dry_run:
        print("\nDry run complete. No data published.")


if __name__ == "__main__":
    main()
