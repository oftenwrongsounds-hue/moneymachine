"""
Scraper watchdog — detects broken scrapers, auto-repairs via Groq.
Zero-result or abnormally low result scraper runs trigger repair.
"""
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shared import groq_client, telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Scrapers to watch: name -> (script_path, min_expected_results)
WATCHED_SCRAPERS = {
    "clearance_scraper": ("stream3_arbitrage/clearance_scraper.py", 5),
    "job_scraper": ("stream2_freelance/job_scraper.py", 2),
    "trend_scraper": ("stream1_digital/trend_scraper.py", 3),
    "social_outreach": ("stream2_freelance/social_outreach.py", 1),
}

SCRAPER_HISTORY_FILE = ROOT / "shared" / "scraper_history.json"


def load_scraper_history() -> dict:
    if SCRAPER_HISTORY_FILE.exists():
        try:
            with open(SCRAPER_HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_scraper_history(history: dict):
    with open(SCRAPER_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def record_run(scraper_name: str, result_count: int, success: bool):
    """Record a scraper run result."""
    history = load_scraper_history()
    if scraper_name not in history:
        history[scraper_name] = []

    history[scraper_name].append({
        "timestamp": datetime.now().isoformat(),
        "result_count": result_count,
        "success": success,
    })

    # Keep last 10 runs
    history[scraper_name] = history[scraper_name][-10:]
    save_scraper_history(history)


def is_scraper_broken(scraper_name: str, current_count: int, min_expected: int) -> bool:
    """
    Detect if a scraper is broken based on:
    - Zero results when min_expected > 0
    - Consistently low results over last 3 runs
    """
    if current_count == 0 and min_expected > 0:
        return True

    history = load_scraper_history()
    runs = history.get(scraper_name, [])

    if len(runs) >= 3:
        last_3 = runs[-3:]
        avg_count = sum(r["result_count"] for r in last_3) / 3
        if avg_count < min_expected * 0.5:  # Less than 50% of expected
            return True

    return False


def fetch_page_html(url: str) -> str:
    """Fetch current HTML of a target page for repair analysis."""
    import requests
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        # Truncate to avoid huge context
        return resp.text[:8000]
    except Exception as e:
        return f"ERROR fetching page: {e}"


def attempt_scraper_repair(scraper_name: str, script_path: str) -> bool:
    """
    Use Groq to analyze broken scraper and suggest fixes.
    Applies fix, tests with dry-run, commits if successful.
    """
    logger.info(f"Attempting auto-repair for {scraper_name}...")

    script_file = ROOT / script_path
    if not script_file.exists():
        logger.error(f"Script file not found: {script_file}")
        return False

    with open(script_file, "r", encoding="utf-8") as f:
        current_code = f.read()

    # Try to fetch a live page to see current HTML structure
    # Map scrapers to representative URLs
    test_urls = {
        "clearance_scraper": "https://www.target.com/c/clearance/-/N-5q0e3",
        "job_scraper": "https://www.upwork.com",
        "trend_scraper": "https://www.etsy.com/search?q=notion+template",
    }
    url = test_urls.get(scraper_name, "")
    html_sample = fetch_page_html(url) if url else ""

    prompt = f"""This Python scraper is returning zero results or fewer than expected.
Analyze the code and HTML sample to identify why it's broken and provide a fix.

BROKEN SCRAPER CODE:
```python
{current_code[:3000]}
```

CURRENT LIVE HTML SAMPLE (first 2000 chars):
```html
{html_sample[:2000]}
```

COMMON REASONS FOR SCRAPER FAILURES:
1. CSS class names changed (very common after site updates)
2. New HTML structure / different element hierarchy
3. Anti-bot measures added (requires different approach)
4. API endpoint changed
5. JSON structure changed

Provide the COMPLETE fixed scraper code with the broken selectors updated to match
the current page structure. Focus specifically on the scraping/parsing logic.
Return ONLY the complete fixed Python code, no explanation, no markdown fences."""

    try:
        fixed_code = groq_client.complete(
            prompt,
            system="You are an expert web scraper developer. Fix broken selectors and parsing code.",
            max_tokens=3000,
            temperature=0.3,
        )

        # Clean up code fences if present
        if fixed_code.strip().startswith("```"):
            lines = fixed_code.strip().split("\n")
            fixed_code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Write to a temp test file
        test_file = ROOT / "shared" / f"_test_{scraper_name}.py"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(fixed_code)

        # Test with dry-run
        result = subprocess.run(
            [sys.executable, str(test_file), "--dry-run"],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )

        if result.returncode == 0 and "error" not in result.stdout.lower():
            # Fix worked — replace original file
            with open(script_file, "w", encoding="utf-8") as f:
                f.write(fixed_code)

            test_file.unlink(missing_ok=True)
            logger.info(f"Auto-repair successful for {scraper_name}")

            # Try to commit the fix
            try:
                subprocess.run(["git", "add", str(script_file)], cwd=str(ROOT), capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", f"Auto-repair: fix {scraper_name} selectors"],
                    cwd=str(ROOT), capture_output=True,
                )
            except Exception:
                pass

            return True
        else:
            logger.warning(f"Auto-repair test failed: {result.stderr[:500]}")
            test_file.unlink(missing_ok=True)
            return False

    except Exception as e:
        logger.error(f"Auto-repair failed: {e}")
        return False


def run():
    """Check all watched scrapers for issues and attempt repairs."""
    history = load_scraper_history()
    repairs_needed = []
    repairs_done = []

    for scraper_name, (script_path, min_expected) in WATCHED_SCRAPERS.items():
        runs = history.get(scraper_name, [])
        if not runs:
            continue

        last_run = runs[-1]
        current_count = last_run.get("result_count", 0)

        if is_scraper_broken(scraper_name, current_count, min_expected):
            repairs_needed.append(scraper_name)
            logger.warning(f"Broken scraper detected: {scraper_name} (last count: {current_count})")

            # Attempt 1
            success = attempt_scraper_repair(scraper_name, script_path)
            if not success:
                # Attempt 2
                success = attempt_scraper_repair(scraper_name, script_path)

            if success:
                repairs_done.append(scraper_name)
                try:
                    telegram_bot.send(f"*Scraper auto-repaired:* {scraper_name}\nBack to normal operation.")
                except Exception:
                    pass
            else:
                try:
                    telegram_bot.send(
                        f"*Scraper repair FAILED:* {scraper_name}\n"
                        f"Two repair attempts failed.\n"
                        f"Script: `{script_path}`\n"
                        f"Manual inspection needed."
                    )
                except Exception:
                    pass

    if not repairs_needed:
        logger.info("All scrapers healthy")

    return {"checked": len(WATCHED_SCRAPERS), "broken": len(repairs_needed), "repaired": len(repairs_done)}


if __name__ == "__main__":
    run()
