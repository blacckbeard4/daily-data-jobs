from __future__ import annotations

"""
Lever ATS scraper.

Fetches jobs from api.lever.co/v0/postings/{slug}
for each company slug in config/companies.json["lever"].

Lever's createdAt field is a millisecond epoch integer.
Requests are sequential (not concurrent) to honour the per-request delay.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from dateutil import parser as dateutil_parser

from config.settings import (
    SCRAPER_MAX_AGE_HOURS,
    SCRAPER_REQUEST_TIMEOUT,
)
from scraper._utils import (
    extract_salary,
    is_excluded_experience,
    matches_category_keyword,
    random_delay,
    slug_to_name,
)
from utils.logger import get_logger

logger = get_logger()

_LEVER_BASE = "https://api.lever.co/v0/postings/{slug}"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise_lever_job(raw: dict, company_slug: str) -> dict:
    """
    Map a raw Lever API posting to the universal job schema.

    Key differences from Greenhouse:
    - createdAt is millisecond epoch (divide by 1000 for datetime.fromtimestamp)
    - Location is nested under categories.location
    - Description is in descriptionPlain (no HTML)
    """
    title = raw.get("text", "")
    categories = raw.get("categories") or {}
    location = categories.get("location", "") if isinstance(categories, dict) else ""

    created_at_ms = raw.get("createdAt", 0)
    try:
        posted_at = datetime.fromtimestamp(
            int(created_at_ms) / 1000, tz=timezone.utc
        ).isoformat()
    except Exception:
        posted_at = ""

    description = raw.get("descriptionPlain", "") or ""
    salary_info = extract_salary(description)

    # Company name: Lever postings don't always include company name,
    # so we derive it from the slug
    company = raw.get("company") or slug_to_name(company_slug)

    return {
        "original_id": raw.get("id", ""),
        "title": title,
        "company": company,
        "location": location,
        "apply_url": raw.get("hostedUrl", ""),
        "salary_info": salary_info,
        "description": description,
        "source_platform": "lever",
        "posted_at": posted_at,
        # LLM pipeline fields — initialised here, set by Node 1
        "category": None,
        "remote_status": False,
        "stack_keywords": [],
        "notability_score": 0,
    }


# ---------------------------------------------------------------------------
# Per-company fetch
# ---------------------------------------------------------------------------

async def fetch_jobs_for_company(
    session: aiohttp.ClientSession,
    slug: str,
    cutoff_ts_ms: int,
) -> list[dict]:
    """
    Fetch all recent matching jobs for one Lever company slug.

    Query params:
    - mode=json  — ensures JSON response (default can be HTML)
    - limit=250  — fetch all public postings in one call
    """
    url = _LEVER_BASE.format(slug=slug)
    params = {"mode": "json", "limit": "250"}

    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                logger.debug(f"Lever: slug '{slug}' not found (404) — skipping.")
                return []
            if resp.status != 200:
                logger.warning(
                    f"Lever: slug '{slug}' returned HTTP {resp.status} — skipping."
                )
                return []
            data: list[dict] | Any = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        logger.warning(f"Lever: network error for slug '{slug}': {exc}")
        return []
    except Exception as exc:
        logger.warning(f"Lever: unexpected error for slug '{slug}': {exc}")
        return []
    finally:
        await random_delay()

    # Lever returns a list directly (not wrapped in a key)
    if not isinstance(data, list):
        logger.warning(f"Lever: unexpected response format for slug '{slug}' — skipping.")
        return []

    matching: list[dict] = []

    for raw in data:
        title = raw.get("text", "")

        # Pre-filter: experience level
        if is_excluded_experience(title):
            continue

        # Pre-filter: category keyword match
        if not matches_category_keyword(title):
            continue

        # Pre-filter: recency — createdAt is millisecond epoch
        created_at_ms = raw.get("createdAt", 0)
        try:
            if int(created_at_ms) < cutoff_ts_ms:
                continue
        except (TypeError, ValueError):
            pass  # If unparseable, include the job

        matching.append(_normalise_lever_job(raw, slug))

    logger.debug(
        f"Lever: slug '{slug}' — {len(data)} total, {len(matching)} matching."
    )
    return matching


# ---------------------------------------------------------------------------
# Main scraper entry point
# ---------------------------------------------------------------------------

async def scrape_all_lever(slugs: list[str]) -> list[dict]:
    """
    Scrape all Lever slugs sequentially and return a flat list of jobs.

    Sequential (not concurrent) to honour per-request delays.
    Failures per slug are logged and skipped — never abort the whole run.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=SCRAPER_MAX_AGE_HOURS)
    cutoff_ts_ms = int(cutoff_dt.timestamp() * 1000)
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []

    logger.info(f"Lever scraper: starting — {len(slugs)} slugs to fetch.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for slug in slugs:
            jobs = await fetch_jobs_for_company(session, slug, cutoff_ts_ms)
            all_jobs.extend(jobs)

    logger.info(f"Lever scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
