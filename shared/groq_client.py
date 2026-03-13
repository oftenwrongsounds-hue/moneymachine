"""
Groq API wrapper with retry logic, rate limiting, and auto-fallback to Together.ai.
Auto-switches to Together.ai when Groq quota is exhausted.
"""
import os
import time
import logging
from typing import Optional
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
TOGETHER_MODEL = "meta-llama/Llama-3-70b-chat-hf"

_groq_quota_exhausted = False
_quota_reset_time: Optional[float] = None


def _is_quota_error(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code == 200:
        return False
    try:
        data = response.json()
        msg = str(data.get("error", {}).get("message", "")).lower()
        return "quota" in msg or "rate limit" in msg or "exceeded" in msg
    except Exception:
        return False


def _call_groq(messages: list, model: str, max_tokens: int, temperature: float) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    response = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=60)

    if _is_quota_error(response):
        raise QuotaExhaustedError(f"Groq quota exhausted: {response.text}")

    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _call_together(messages: list, model: str, max_tokens: int, temperature: float) -> str:
    api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        raise ValueError("TOGETHER_API_KEY environment variable not set — needed as Groq fallback")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    response = requests.post(TOGETHER_API_URL, json=payload, headers=headers, timeout=60)

    if response.status_code == 429:
        raise QuotaExhaustedError(f"Together.ai also quota exhausted: {response.text}")

    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


class QuotaExhaustedError(Exception):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True,
)
def complete(
    prompt: str,
    system: str = "You are a helpful AI assistant.",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    force_together: bool = False,
) -> str:
    """
    Call Groq (or Together.ai fallback) and return the completion text.

    Args:
        prompt: User prompt
        system: System message
        model: Model name (Groq model; Together fallback uses TOGETHER_MODEL)
        max_tokens: Max tokens in response
        temperature: Sampling temperature
        force_together: Skip Groq and go straight to Together.ai

    Returns:
        Completion text string
    """
    global _groq_quota_exhausted, _quota_reset_time

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    # Reset quota flag if it's been more than 1 hour
    if _groq_quota_exhausted and _quota_reset_time and time.time() > _quota_reset_time:
        logger.info("Groq quota reset window passed — retrying Groq")
        _groq_quota_exhausted = False
        _quota_reset_time = None

    if not force_together and not _groq_quota_exhausted:
        try:
            result = _call_groq(messages, model, max_tokens, temperature)
            logger.debug("Groq call successful")
            return result
        except QuotaExhaustedError:
            logger.warning("Groq quota exhausted — switching to Together.ai for remainder of day")
            _groq_quota_exhausted = True
            _quota_reset_time = time.time() + 3600  # Reset check in 1 hour
        except Exception as e:
            logger.error(f"Groq call failed: {e}")
            raise

    # Fallback to Together.ai
    try:
        result = _call_together(messages, TOGETHER_MODEL, max_tokens, temperature)
        logger.info("Together.ai fallback call successful")
        return result
    except QuotaExhaustedError:
        raise
    except Exception as e:
        logger.error(f"Together.ai call failed: {e}")
        raise


def test_connection() -> dict:
    """Test both Groq and Together.ai connections. Returns status dict."""
    results = {"groq": False, "together": False, "errors": []}

    try:
        resp = complete("Say 'OK' and nothing else.", max_tokens=10, temperature=0)
        results["groq"] = "OK" in resp or len(resp) > 0
    except Exception as e:
        results["errors"].append(f"Groq: {e}")

    try:
        resp = _call_together(
            [{"role": "user", "content": "Say 'OK' and nothing else."}],
            TOGETHER_MODEL,
            10,
            0,
        )
        results["together"] = "OK" in resp or len(resp) > 0
    except Exception as e:
        results["errors"].append(f"Together.ai: {e}")

    return results
