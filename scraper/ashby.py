"""
Ashby ATS scraper.

Fetches jobs from api.ashbyhq.com/posting-api/job-board/{slug}
for each company slug in config/companies.json["ashby"].

API is free and public — no authentication required.
Salary: pulled from compensation.compensationTierSummary if present,
otherwise via regex on job description.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

_ASHBY_BASE = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise_ashby_job(raw: dict, company_slug: str) -> dict:
    title = raw.get("title", "")
    location = raw.get("locationName", "") or ""
    apply_url = raw.get("applyUrl", "") or raw.get("jobUrl", "") or ""
    description = raw.get("descriptionHtml", "") or raw.get("description", "") or ""

    # Salary: prefer structured compensation field, fall back to regex
    salary_info: str | None = None
    compensation = raw.get("compensation") or {}
    if isinstance(compensation, dict):
        salary_info = compensation.get("compensationTierSummary") or None
    if not salary_info:
        salary_info = extract_salary(description)

    company = raw.get("companyName") or slug_to_name(company_slug)
    published_at = raw.get("publishedAt") or raw.get("updatedAt") or ""

    return {
        "original_id": raw.get("id", ""),
        "title": title,
        "company": company,
        "location": location,
        "apply_url": apply_url,
        "salary_info": salary_info,
        "description": description,
        "source_platform": "ashby",
        "posted_at": published_at,
        "category": None,
        "remote_status": bool(raw.get("isRemote", False)),
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
    url = _ASHBY_BASE.format(slug=slug)
    params = {"includeCompensation": "true"}

    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                logger.debug(f"Ashby: slug '{slug}' not found (404) — skipping.")
                return []
            if resp.status != 200:
                logger.warning(f"Ashby: slug '{slug}' returned HTTP {resp.status} — skipping.")
                return []
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        logger.warning(f"Ashby: network error for slug '{slug}': {exc}")
        return []
    except Exception as exc:
        logger.warning(f"Ashby: unexpected error for slug '{slug}': {exc}")
        return []
    finally:
        await random_delay()

    jobs_raw: list[dict] = data.get("jobs", []) if isinstance(data, dict) else []
    matching: list[dict] = []

    for raw in jobs_raw:
        title = raw.get("title", "")

        if is_excluded_experience(title):
            continue
        if not matches_category_keyword(title):
            continue

        # Recency filter
        date_str = raw.get("publishedAt") or raw.get("updatedAt") or ""
        if date_str:
            try:
                posted_at = dateutil_parser.parse(date_str)
                if posted_at.tzinfo is None:
                    posted_at = posted_at.replace(tzinfo=timezone.utc)
                if posted_at < cutoff_dt:
                    continue
            except Exception:
                pass  # unparseable date — include the job, LLM will decide

        matching.append(_normalise_ashby_job(raw, slug))

    logger.debug(f"Ashby: slug '{slug}' — {len(jobs_raw)} total, {len(matching)} matching.")
    return matching


# ---------------------------------------------------------------------------
# Main scraper entry point
# ---------------------------------------------------------------------------

async def scrape_all_ashby(slugs: list[str]) -> list[dict]:
    """
    Scrape all Ashby slugs sequentially and return a flat list of jobs.

    Sequential (not concurrent) to honour per-request delays.
    Failures per slug are logged and skipped — never abort the whole run.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=SCRAPER_MAX_AGE_HOURS)
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []

    logger.info(f"Ashby scraper: starting — {len(slugs)} slugs to fetch.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for slug in slugs:
            jobs = await fetch_jobs_for_company(session, slug, cutoff_dt)
            all_jobs.extend(jobs)

    logger.info(f"Ashby scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
