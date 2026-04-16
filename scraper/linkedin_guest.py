"""
LinkedIn guest API scraper (Tier 4 / last resort).

Uses the undocumented LinkedIn jobs guest endpoint:
    https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/

Parameters:
    keywords    — job title search term
    f_TPR=r86400 — posted in last 24 hours
    f_E=3,4      — Mid-Senior + Director experience levels
    geoId=103644278 — United States

This endpoint returns HTML fragments, not JSON. Parse with BeautifulSoup.
Only used when all other sources (Greenhouse, Lever, Dice) return < 5 jobs.
"""

import asyncio
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup

from config.settings import (
    CATEGORY_KEYWORDS,
    SCRAPER_REQUEST_TIMEOUT,
)
from scraper._utils import (
    extract_salary,
    is_excluded_experience,
    matches_category_keyword,
    random_delay,
)
from utils.logger import get_logger

logger = get_logger()

_LINKEDIN_GUEST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/",
}


def _parse_linkedin_html(html: str, keyword: str) -> list[dict]:
    """
    Parse the HTML fragment returned by the LinkedIn guest API.
    Returns a list of raw job dicts.
    """
    jobs: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("li")

        for card in cards:
            title_el = card.find(class_="base-search-card__title")
            company_el = card.find(class_="base-search-card__subtitle")
            location_el = card.find(class_="job-search-card__location")
            link_el = card.find("a", class_="base-card__full-link", href=True)

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            location = location_el.get_text(strip=True) if location_el else ""
            url = link_el["href"] if link_el else ""

            if not title or is_excluded_experience(title):
                continue
            if not matches_category_keyword(title):
                continue

            jobs.append({
                "original_id": url,
                "title": title,
                "company": company,
                "location": location,
                "apply_url": url,
                "salary_info": None,
                "description": "",
                "source_platform": "linkedin_guest",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "category": None,
                "remote_status": False,
                "stack_keywords": [],
                "notability_score": 0,
            })
    except Exception as exc:
        logger.warning(f"LinkedIn guest: HTML parsing error for '{keyword}': {exc}")

    return jobs


async def _fetch_keyword(
    session: aiohttp.ClientSession,
    keyword: str,
) -> list[dict]:
    """Fetch one keyword search from the LinkedIn guest endpoint."""
    params = {
        "keywords": keyword,
        "f_TPR": "r86400",         # last 24 hours
        "f_E": "3,4",              # Mid-Senior + Director
        "geoId": "103644278",      # United States
        "start": "0",
    }
    try:
        async with session.get(_LINKEDIN_GUEST_URL, params=params, headers=_HEADERS) as resp:
            if resp.status != 200:
                logger.warning(f"LinkedIn guest: HTTP {resp.status} for '{keyword}'")
                return []
            html = await resp.text()
    except Exception as exc:
        logger.warning(f"LinkedIn guest: error fetching '{keyword}': {exc}")
        return []
    finally:
        await random_delay()

    return _parse_linkedin_html(html, keyword)


async def scrape_linkedin_guest() -> list[dict]:
    """
    Scrape LinkedIn guest API for all target keywords.
    Returns a flat list of normalised job dicts.
    """
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    logger.info("LinkedIn guest scraper: starting.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                raw_jobs = await _fetch_keyword(session, keyword)
                for job in raw_jobs:
                    job_id = job.get("original_id", "")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)
                    all_jobs.append(job)

    logger.info(f"LinkedIn guest scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
