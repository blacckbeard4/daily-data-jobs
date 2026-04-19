"""
AIJobs.net scraper — DISABLED.

The AIJobs.net RSS feed (https://aijobs.net/feed/ and all known variants)
returns 404. The site has no public feed or API endpoint as of 2026-04.
This module is retained so main.py can import it without error.
"""

from utils.logger import get_logger

logger = get_logger()


async def scrape_aijobs() -> list[dict]:
    """AIJobs feed URL unavailable — skipping."""
    logger.info("AIJobs feed URL unavailable — skipping.")
    return []
