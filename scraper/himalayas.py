"""
Himalayas.app scraper.

Fetches remote-first data/AI jobs from the Himalayas public API.
Endpoint: https://himalayas.app/jobs/api?limit=20&offset={n}
Paginates until the jobs array in the response is empty.

Raw API field names (confirmed 2026-04):
  companyName, applicationLink, pubDate (Unix timestamp),
  minSalary, maxSalary, currency, locationRestrictions (list)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiohttp

from config.settings import (
    SCRAPER_MAX_AGE_HOURS,
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

_HIMALAYAS_URL = "https://himalayas.app/jobs/api"
_PAGE_SIZE = 20
_MAX_PAGES = 10  # safety cap — max 200 jobs fetched per run


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _parse_pubdate(raw: dict) -> datetime | None:
    """Parse pubDate (Unix timestamp string) or fall back to None."""
    ts = raw.get("pubDate")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    return None


def _build_salary(raw: dict) -> str | None:
    """Build salary string from minSalary/maxSalary, fall back to description regex."""
    min_sal = raw.get("minSalary")
    max_sal = raw.get("maxSalary")
    currency = raw.get("currency") or "USD"

    if min_sal and min_sal != "None" and str(min_sal).strip():
        try:
            lo = int(float(str(min_sal)))
            if max_sal and max_sal != "None" and str(max_sal).strip():
                hi = int(float(str(max_sal)))
                return f"${lo:,} - ${hi:,} {currency}"
            return f"${lo:,} {currency}"
        except (ValueError, TypeError):
            pass

    # Fall back to regex on description text
    return extract_salary(raw.get("description", "") or "")


def _normalise_himalayas_job(raw: dict) -> dict:
    company = (raw.get("companyName") or "").strip()
    apply_url = (
        raw.get("applicationLink")
        or raw.get("guid")
        or raw.get("url")
        or raw.get("applyUrl")
        or ""
    ).strip()

    location = raw.get("locationRestrictions") or "Remote"
    if isinstance(location, list):
        location = ", ".join(location) if location else "Remote"

    salary_info = _build_salary(raw)
    published_at = _parse_pubdate(raw)

    return {
        "original_id": str(raw.get("guid") or raw.get("applicationLink") or ""),
        "title": raw.get("title", ""),
        "company": company,
        "location": str(location),
        "apply_url": apply_url,
        "salary_info": salary_info,
        "description": raw.get("description", "") or "",
        "source_platform": "himalayas",
        "posted_at": published_at.isoformat() if published_at else "",
        "category": None,
        "remote_status": True,  # Himalayas is remote-first
        "stack_keywords": [],
        "notability_score": 0,
    }


# ---------------------------------------------------------------------------
# Main scraper entry point
# ---------------------------------------------------------------------------

async def scrape_himalayas() -> list[dict]:
    """
    Scrape Himalayas job feed with pagination.
    Stops when the API returns an empty jobs array or after _MAX_PAGES pages.
    Returns a flat list of normalised job dicts.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=SCRAPER_MAX_AGE_HOURS)
    timeout = aiohttp.ClientTimeout(total=SCRAPER_REQUEST_TIMEOUT)
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    logger.info("Himalayas scraper: starting.")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for page in range(_MAX_PAGES):
            offset = page * _PAGE_SIZE
            params = {"limit": str(_PAGE_SIZE), "offset": str(offset)}

            try:
                async with session.get(_HIMALAYAS_URL, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"Himalayas: HTTP {resp.status} at offset {offset} — stopping."
                        )
                        break
                    data = await resp.json(content_type=None)
            except Exception as exc:
                logger.warning(f"Himalayas: error at offset {offset}: {exc}")
                break
            finally:
                await random_delay()

            jobs_raw = data.get("jobs", []) if isinstance(data, dict) else []
            if not jobs_raw:
                break  # pagination complete

            for raw in jobs_raw:
                title = raw.get("title", "")

                if is_excluded_experience(title):
                    continue
                if not matches_category_keyword(title):
                    continue

                # Recency filter using pubDate Unix timestamp
                published_at = _parse_pubdate(raw)
                if published_at and published_at < cutoff_dt:
                    continue

                job_id = str(raw.get("guid") or raw.get("applicationLink") or "") or title
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                job = _normalise_himalayas_job(raw)

                # Validation: skip incomplete records
                if not job["company"] or not job["apply_url"]:
                    logger.warning(
                        f"Himalayas: skipping '{title}' — missing company={job['company']!r} "
                        f"or apply_url={job['apply_url']!r}"
                    )
                    continue

                all_jobs.append(job)

    logger.info(f"Himalayas scraper: finished — {len(all_jobs)} jobs collected.")
    return all_jobs
