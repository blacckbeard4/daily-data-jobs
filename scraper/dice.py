"""
Dice.com fallback scraper (Tier 3).

Used only when Greenhouse + Lever return insufficient jobs.
Scrapes dice.com/jobs using BeautifulSoup — no API key required.

Note: This is a best-effort scraper. Dice's HTML structure can change.
If it breaks, the pipeline continues with whatever ATS jobs were found.
"""

import asyncio
import time
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup

from config.settings import (
    CATEGORY_KEYWORDS,
    SCRAPER_MAX_DELAY_SECONDS,
    SCRAPER_MIN_DELAY_SECONDS,
    SCRAPER_REQUEST_TIMEOUT,
)
from scraper._utils import (
    extract_salary,
    is_excluded_experience,
    matches_category_keyword,
    random_delay,
)
from utils.logger import get_logger

import random

logger = get_logger()

_DICE_BASE = "https://www.dice.com/jobs"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalise_dice_job(raw: dict) -> dict:
    """Map a parsed Dice job to the universal schema."""
    return {
        "original_id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "company": raw.get("company", ""),
        "location": raw.get("location", ""),
        "apply_url": raw.get("url", ""),
        "salary_info": raw.get("salary_info"),
        "description": raw.get("description", ""),
        "source_platform": "dice",
        "posted_at": raw.get("posted_at", ""),
        "category": None,
        "remote_status": False,
        "stack_keywords": [],
        "notability_score": 0,
    }


async def _fetch_dice_search(
    session: aiohttp.ClientSession,
    query: str,
) -> list[dict]:
    """Fetch one search page from Dice and extract job listings."""
    params = {
        "q": query,
        "filters.postedDate": "ONE",   # last 24 hours
        "filters.employmentType": "FULLTIME",
        "countryCode": "US",
        "language": "en",
    }
    try:
        async with session.get(_DICE_BASE, params=params, headers=_HEADERS) as resp:
            if resp.status != 200:
                logger.warning(f"Dice: HTTP {resp.status} for query '{query}'")
                return []
            html = await resp.text()
    except Exception as exc:
        logger.warning(f"Dice: error fetching '{query}': {exc}")
        return []
    finally:
        await random_delay()

    jobs: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Dice renders job cards as <div data-cy="card"> elements
        cards = soup.find_all("div", attrs={"data-cy": "card"})
        for card in cards:
            title_el = card.find(attrs={"data-cy": "card-title"})
            company_el = card.find(attrs={"data-cy": "search-result-company-name"})
            location_el = card.find(attrs={"data-cy": "search-result-location"})
            link_el = card.find("a", href=True)

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            location = location_el.get_text(strip=True) if location_el else ""
            url = link_el["href"] if link_el else ""
            if url and not url.startswith("http"):
                url = "https://www.dice.com" + url

            if not title or is_excluded_experience(title):
                continue
            if not matches_category_keyword(title):
                continue

            jobs.append({
                "id": url,  # use URL as ID for Dice
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "salary_info": None,
                "description": "",
                "posted_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as exc:
        logger.warning(f"Dice: HTML parsing failed for query '{query}': {exc}")

    return jobs


async def scrape_dice() -> list[dict]:
    """
    Scrape Dice for all 5 target role categories.
    Returns a flat list of normalised job dicts.
    """
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    logger.info("Dice scraper: starting.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                raw_jobs = await _fetch_dice_search(session, keyword)
                for raw in raw_jobs:
                    job_id = raw.get("id", "")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)
                    all_jobs.append(_normalise_dice_job(raw))

    logger.info(f"Dice scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
