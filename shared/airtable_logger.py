"""
Airtable logger — writes to all 4 tables: Revenue_Log, Arbitrage_Deals, Products, Jobs.
Uses pyairtable. All credentials from environment variables.
"""
import os
import logging
from datetime import datetime
from typing import Optional
from pyairtable import Api

logger = logging.getLogger(__name__)

# Table names
TABLE_REVENUE_LOG = "Revenue_Log"
TABLE_ARBITRAGE_DEALS = "Arbitrage_Deals"
TABLE_PRODUCTS = "Products"
TABLE_JOBS = "Jobs"


def _get_table(table_name: str):
    api_key = os.environ.get("AIRTABLE_API_KEY")
    base_id = os.environ.get("AIRTABLE_BASE_ID")
    if not api_key:
        raise ValueError("AIRTABLE_API_KEY environment variable not set")
    if not base_id:
        raise ValueError("AIRTABLE_BASE_ID environment variable not set")
    api = Api(api_key)
    return api.table(base_id, table_name)


def log_revenue(
    stream: str,
    amount: float,
    source: str,
    description: str = "",
    date: Optional[str] = None,
) -> dict:
    """Log a revenue entry to Revenue_Log table."""
    table = _get_table(TABLE_REVENUE_LOG)
    record = {
        "Stream": stream,
        "Amount": amount,
        "Source": source,
        "Description": description,
        "Date": date or datetime.now().strftime("%Y-%m-%d"),
        "CreatedAt": datetime.now().isoformat(),
    }
    try:
        result = table.create(record)
        logger.info(f"Revenue logged: {stream} ${amount} from {source}")
        return result
    except Exception as e:
        logger.error(f"Failed to log revenue: {e}")
        raise


def log_arbitrage_deal(
    title: str,
    buy_price: float,
    sell_price: float,
    confidence_score: int,
    source_url: str = "",
    status: str = "Pending Approval",
    deal_id: Optional[str] = None,
    extra_fields: Optional[dict] = None,
) -> dict:
    """Log an arbitrage deal to Arbitrage_Deals table."""
    table = _get_table(TABLE_ARBITRAGE_DEALS)
    record = {
        "Title": title,
        "BuyPrice": buy_price,
        "SellPrice": sell_price,
        "EstimatedProfit": sell_price - buy_price,
        "ConfidenceScore": confidence_score,
        "SourceURL": source_url,
        "Status": status,
        "CreatedAt": datetime.now().isoformat(),
    }
    if deal_id:
        record["DealID"] = deal_id
    if extra_fields:
        record.update(extra_fields)
    try:
        result = table.create(record)
        logger.info(f"Arbitrage deal logged: {title} (score: {confidence_score})")
        return result
    except Exception as e:
        logger.error(f"Failed to log arbitrage deal: {e}")
        raise


def update_arbitrage_deal_status(record_id: str, status: str) -> dict:
    """Update status of an arbitrage deal."""
    table = _get_table(TABLE_ARBITRAGE_DEALS)
    try:
        result = table.update(record_id, {"Status": status})
        return result
    except Exception as e:
        logger.error(f"Failed to update deal status: {e}")
        raise


def log_product(
    title: str,
    niche: str,
    gumroad_url: str = "",
    etsy_listing_id: str = "",
    pinterest_pin_id: str = "",
    price: float = 0.0,
    status: str = "Published",
) -> dict:
    """Log a digital product to Products table."""
    table = _get_table(TABLE_PRODUCTS)
    record = {
        "Title": title,
        "Niche": niche,
        "GumroadURL": gumroad_url,
        "EtsyListingID": etsy_listing_id,
        "PinterestPinID": pinterest_pin_id,
        "Price": price,
        "Status": status,
        "PublishedAt": datetime.now().isoformat(),
    }
    try:
        result = table.create(record)
        logger.info(f"Product logged: {title}")
        return result
    except Exception as e:
        logger.error(f"Failed to log product: {e}")
        raise


def update_product(record_id: str, fields: dict) -> dict:
    """Update fields on a product record."""
    table = _get_table(TABLE_PRODUCTS)
    try:
        return table.update(record_id, fields)
    except Exception as e:
        logger.error(f"Failed to update product: {e}")
        raise


def log_job(
    platform: str,
    title: str,
    budget: str = "",
    url: str = "",
    status: str = "Scraped",
    proposal_text: str = "",
) -> dict:
    """Log a freelance job to Jobs table."""
    table = _get_table(TABLE_JOBS)
    record = {
        "Platform": platform,
        "Title": title,
        "Budget": budget,
        "URL": url,
        "Status": status,
        "ProposalText": proposal_text,
        "ScrapedAt": datetime.now().isoformat(),
    }
    try:
        result = table.create(record)
        logger.info(f"Job logged: {platform} — {title}")
        return result
    except Exception as e:
        logger.error(f"Failed to log job: {e}")
        raise


def update_job_status(record_id: str, status: str, extra: Optional[dict] = None) -> dict:
    """Update status of a job record."""
    table = _get_table(TABLE_JOBS)
    fields = {"Status": status}
    if extra:
        fields.update(extra)
    try:
        return table.update(record_id, fields)
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")
        raise


def get_revenue_by_stream(days: int = 7) -> dict:
    """Get revenue totals by stream for the last N days."""
    table = _get_table(TABLE_REVENUE_LOG)
    cutoff = datetime.now()
    try:
        records = table.all()
        totals = {"Digital Products": 0.0, "Freelancing": 0.0, "Arbitrage": 0.0}
        for r in records:
            fields = r.get("fields", {})
            stream = fields.get("Stream", "")
            amount = fields.get("Amount", 0.0)
            if stream in totals:
                totals[stream] += amount
        return totals
    except Exception as e:
        logger.error(f"Failed to get revenue: {e}")
        return {}


def get_pending_arbitrage_deals() -> list:
    """Get all arbitrage deals with status 'Pending Approval'."""
    table = _get_table(TABLE_ARBITRAGE_DEALS)
    try:
        return table.all(formula="Status='Pending Approval'")
    except Exception as e:
        logger.error(f"Failed to get pending deals: {e}")
        return []


def archive_old_records(days: int = 90) -> int:
    """Archive records older than N days to free up Airtable space."""
    archived = 0
    for table_name in [TABLE_REVENUE_LOG, TABLE_JOBS]:
        try:
            table = _get_table(table_name)
            records = table.all()
            for r in records:
                created = r.get("fields", {}).get("CreatedAt", "")
                if created:
                    try:
                        age = (datetime.now() - datetime.fromisoformat(created)).days
                        if age > days:
                            table.delete(r["id"])
                            archived += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Archive error for {table_name}: {e}")
    logger.info(f"Archived {archived} old records")
    return archived


def test_connection() -> bool:
    """Test Airtable connection. Returns True on success."""
    try:
        table = _get_table(TABLE_REVENUE_LOG)
        table.all(max_records=1)
        return True
    except Exception as e:
        logger.error(f"Airtable connection test failed: {e}")
        return False
