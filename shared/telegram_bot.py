"""
Telegram bot — send alerts, receive YES/NO/APPROVE/SKIP commands.
Supports pending approval callbacks stored in Airtable.
"""
import os
import logging
import time
from typing import Optional, Callable
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def _token() -> str:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN environment variable not set")
    return token


def _chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise ValueError("TELEGRAM_CHAT_ID environment variable not set")
    return chat_id


def send(message: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{_token()}/sendMessage"
    payload = {
        "chat_id": _chat_id(),
        "text": message,
        "parse_mode": parse_mode,
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        raise


def send_approval_request(
    message: str,
    approval_id: str,
    approve_label: str = "YES",
    skip_label: str = "NO",
) -> dict:
    """Send a message with inline keyboard for YES/NO approval."""
    url = f"https://api.telegram.org/bot{_token()}/sendMessage"
    payload = {
        "chat_id": _chat_id(),
        "text": message,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": f"✅ {approve_label}", "callback_data": f"APPROVE:{approval_id}"},
                    {"text": f"❌ {skip_label}", "callback_data": f"SKIP:{approval_id}"},
                ]
            ]
        },
    }
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram approval request: {e}")
        raise


def get_updates(offset: Optional[int] = None, timeout: int = 10) -> list:
    """Get pending updates from Telegram."""
    url = f"https://api.telegram.org/bot{_token()}/getUpdates"
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=timeout + 5)
        response.raise_for_status()
        return response.json().get("result", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get Telegram updates: {e}")
        return []


def parse_update(update: dict) -> Optional[dict]:
    """
    Parse a Telegram update into a structured command.
    Returns dict with 'type', 'action', 'id', 'raw'.
    """
    # Handle callback queries (inline keyboard)
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        if ":" in data:
            action, item_id = data.split(":", 1)
            return {
                "type": "callback",
                "action": action.upper(),  # APPROVE or SKIP
                "id": item_id,
                "callback_query_id": cb["id"],
                "raw": cb,
            }

    # Handle regular messages
    if "message" in update:
        text = update["message"].get("text", "").strip().upper()
        if text in ("YES", "Y"):
            return {"type": "message", "action": "YES", "id": None, "raw": update["message"]}
        if text in ("NO", "N"):
            return {"type": "message", "action": "NO", "id": None, "raw": update["message"]}
        if text == "APPROVE":
            return {"type": "message", "action": "APPROVE", "id": None, "raw": update["message"]}
        if text == "SKIP":
            return {"type": "message", "action": "SKIP", "id": None, "raw": update["message"]}

    return None


def answer_callback(callback_query_id: str, text: str = "Got it!") -> None:
    """Acknowledge an inline keyboard callback."""
    url = f"https://api.telegram.org/bot{_token()}/answerCallbackQuery"
    try:
        requests.post(
            url,
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Failed to answer callback: {e}")


def poll_for_response(
    approval_id: str,
    timeout_seconds: int = 86400,
    poll_interval: int = 5,
) -> Optional[str]:
    """
    Poll Telegram until a YES/NO/APPROVE/SKIP for the given approval_id.
    Returns the action string or None if timed out.
    """
    offset = None
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        updates = get_updates(offset=offset, timeout=poll_interval)
        for update in updates:
            offset = update["update_id"] + 1
            parsed = parse_update(update)
            if parsed:
                if parsed.get("id") == approval_id or parsed["type"] == "message":
                    if parsed["type"] == "callback":
                        answer_callback(parsed["callback_query_id"])
                    return parsed["action"]
        time.sleep(poll_interval)

    return None


def test_connection() -> bool:
    """Test Telegram bot connectivity. Returns True on success."""
    try:
        url = f"https://api.telegram.org/bot{_token()}/getMe"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("ok", False)
    except Exception as e:
        logger.error(f"Telegram connection test failed: {e}")
        return False
