"""
Greenhouse ATS scraper.

Fetches jobs from boards-api.greenhouse.io/v1/boards/{slug}/jobs
for each company slug in config/companies.json["greenhouse"].

Critical: use ?content=true to include job descriptions in the response.
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

_GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise_greenhouse_job(raw: dict, company_slug: str) -> dict:
    """
    Map a raw Greenhouse API job object to the universal job schema.

    LLM pipeline fields (category, remote_status, stack_keywords,
    notability_score) are initialised to defaults — Node 1 fills them in.
    """
    title = raw.get("title", "")
    location_obj = raw.get("location") or {}
    location = location_obj.get("name", "") if isinstance(location_obj, dict) else ""

    # Company name: prefer API-provided value, fall back to slug conversion
    company_obj = raw.get("company") or {}
    if isinstance(company_obj, dict):
        company = company_obj.get("name") or slug_to_name(company_slug)
    else:
        company = slug_to_name(company_slug)

    description = raw.get("content", "") or ""
    salary_info = extract_salary(description)

    return {
        "original_id": str(raw.get("id", "")),
        "title": title,
        "company": company,
        "location": location,
        "apply_url": raw.get("absolute_url", ""),
        "salary_info": salary_info,
        "description": description,
        "source_platform": "greenhouse",
        "posted_at": raw.get("updated_at", ""),
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
    cutoff_dt: datetime,
) -> list[dict]:
    """
    Fetch all recent matching jobs for one Greenhouse company slug.

    Filters applied here (pre-LLM, fast):
    - updated_at within last SCRAPER_MAX_AGE_HOURS
    - title matches at least one target category keyword
    - title does NOT contain an experience exclusion term
    """
    url = _GREENHOUSE_BASE.format(slug=slug)
    params = {"content": "true"}

    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                logger.debug(f"Greenhouse: slug '{slug}' not found (404) — skipping.")
                return []
            if resp.status != 200:
                logger.warning(
                    f"Greenhouse: slug '{slug}' returned HTTP {resp.status} — skipping."
                )
                return []
            data: dict[str, Any] = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        logger.warning(f"Greenhouse: network error for slug '{slug}': {exc}")
        return []
    except Exception as exc:
        logger.warning(f"Greenhouse: unexpected error for slug '{slug}': {exc}")
        return []
    finally:
        await random_delay()

    jobs_raw: list[dict] = data.get("jobs", [])
    matching: list[dict] = []

    for raw in jobs_raw:
        title = raw.get("title", "")

        # Pre-filter: experience level
        if is_excluded_experience(title):
            continue

        # Pre-filter: category keyword match
        if not matches_category_keyword(title):
            continue

        # Pre-filter: recency — parse updated_at
        updated_at_str = raw.get("updated_at", "")
        if updated_at_str:
            try:
                updated_at = dateutil_parser.parse(updated_at_str)
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if updated_at < cutoff_dt:
                    continue
            except Exception:
                # If we can't parse the date, include the job (LLM will decide)
                pass

        matching.append(_normalise_greenhouse_job(raw, slug))

    logger.debug(
        f"Greenhouse: slug '{slug}' — {len(jobs_raw)} total, {len(matching)} matching."
    )
    return matching


# ---------------------------------------------------------------------------
# Main scraper entry point
# ---------------------------------------------------------------------------

async def scrape_all_greenhouse(slugs: list[str]) -> list[dict]:
    """
    Scrape all Greenhouse slugs sequentially and return a flat list of jobs.

    Sequential (not concurrent) to honour per-request delays.
    Failures per slug are logged and skipped — never abort the whole run.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=SCRAPER_MAX_AGE_HOURS)
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []

    logger.info(f"Greenhouse scraper: starting — {len(slugs)} slugs to fetch.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for slug in slugs:
            jobs = await fetch_jobs_for_company(session, slug, cutoff_dt)
            all_jobs.extend(jobs)

    logger.info(f"Greenhouse scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
