from __future__ import annotations

"""
Shared scraper utilities used by greenhouse.py and lever.py.
"""

import asyncio
import random
import re

from config.settings import (
    CATEGORY_KEYWORDS,
    EXPERIENCE_EXCLUDE_TERMS,
    SCRAPER_MAX_DELAY_SECONDS,
    SCRAPER_MIN_DELAY_SECONDS,
)
from utils.logger import get_logger

_utils_logger = get_logger()

# Priority order when a title matches multiple categories.
# Higher index = higher priority (AI Engineer wins over Data Analyst).
_CATEGORY_PRIORITY: list[str] = [
    "Data Analyst",
    "Data Engineer",
    "Data Scientist",
    "ML Engineer",
    "AI Engineer",
]

# Pre-compiled salary regex — covers common US salary formats:
# $120,000  /  $120k  /  $120K  /  $120,000 - $180,000  /  120000-180000 USD
# Requires dollar amounts to be ≥ $1,000 (comma-formatted, 4+ digits, or k/K suffix)
# to avoid matching stray values like "$124" that appear in job descriptions.
_SALARY_AMOUNT = r"\$(?:\d{1,3}(?:,\d{3})+|\d{4,}|\d+[kK])(?:[kK])?"
_SALARY_PATTERN = re.compile(
    rf"""
    (?:
        {_SALARY_AMOUNT}                              # lower bound (or single value)
        (?:\s*[-–]\s*{_SALARY_AMOUNT})?              # optional upper bound
    )
    |
    (?:
        [\d,]{{5,}}             # bare 5+ digit number
        \s*(?:USD|usd)          # followed by USD
    )
    """,
    re.VERBOSE,
)


def slug_to_name(slug: str) -> str:
    """
    Convert an ATS slug to a human-readable company name.

    Examples:
        'databricks'  -> 'Databricks'
        'dbt-labs'    -> 'dbt Labs'
        'openai'      -> 'OpenAI'  (falls back to title-case)
    """
    # Known overrides for stylised names
    _OVERRIDES: dict[str, str] = {
        "openai": "OpenAI",
        "dbt-labs": "dbt Labs",
        "huggingface": "Hugging Face",
        "coinbase": "Coinbase",
        "databricks": "Databricks",
        "snowflake": "Snowflake",
        "airbyte": "Airbyte",
        "fivetran": "Fivetran",
        "confluent": "Confluent",
        "mongodb": "MongoDB",
        "cockroachdb": "CockroachDB",
        "singlestore": "SingleStore",
        "pinecone": "Pinecone",
        "weaviate": "Weaviate",
        "qdrant": "Qdrant",
        "weights-biases": "Weights & Biases",
        "pagerduty": "PagerDuty",
        "hashicorp": "HashiCorp",
        "datadog": "Datadog",
        "cloudflare": "Cloudflare",
        "scale-ai": "Scale AI",
        "together-ai": "Together AI",
        "mosaic-ml": "MosaicML",
        "mosaicml": "MosaicML",
        "anyscale": "Anyscale",
        "mlops": "MLOps",
    }
    if slug in _OVERRIDES:
        return _OVERRIDES[slug]
    # Replace hyphens with spaces, title-case each word
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())


def extract_salary(text: str) -> str | None:
    """
    Scan job description text for the first salary pattern.

    Returns the matched string (e.g. '$120,000 - $180,000') or None.
    """
    if not text:
        return None
    match = _SALARY_PATTERN.search(text)
    if match:
        return match.group(0).strip()
    return None


def get_primary_category(title: str) -> str | None:
    """
    Return the highest-priority category that matches this title, or None.

    When a title matches multiple categories (e.g. 'ML Platform Engineer'
    matches both ML Engineer and Data Engineer), the priority order resolves
    the tie: AI Engineer > ML Engineer > Data Scientist > Data Engineer >
    Data Analyst.
    """
    lower = title.lower()
    matches: list[str] = []
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            matches.append(cat)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # Multiple matches — pick highest priority
    best = max(matches, key=lambda c: _CATEGORY_PRIORITY.index(c) if c in _CATEGORY_PRIORITY else -1)
    _utils_logger.debug(
        f"Category tiebreaker: '{title}' matched {matches} → assigned '{best}'"
    )
    return best


def matches_category_keyword(title: str) -> bool:
    """
    Return True if the job title (case-insensitive) contains any keyword
    from any of the 5 target categories.
    """
    return get_primary_category(title) is not None


def is_excluded_experience(title: str) -> bool:
    """
    Return True if the job title contains any experience-level exclusion term.
    Used as a pre-filter before sending to the LLM.
    """
    lower = title.lower()
    return any(term in lower for term in EXPERIENCE_EXCLUDE_TERMS)


_NON_US_COUNTRIES = {
    "australia", "brazil", "canada", "uk", "united kingdom",
    "germany", "france", "india", "singapore", "europe", "emea",
    "international", "worldwide", "global", "mexico", "netherlands",
    "poland", "spain", "italy", "sweden", "israel", "japan",
    "south korea", "china", "new zealand", "ireland",
}

_US_INDICATORS = {"united states", "remote", "u.s.", "usa", "us "}


def filter_non_us(job: dict) -> bool:
    """
    Return True if the job should be EXCLUDED because its location is
    clearly non-US.

    Rules:
    - No location → keep (Node 1 LLM will decide)
    - 2+ non-US country names present → always exclude (global role, even if
      US is also listed — e.g. "Australia, Brazil, ... United States" is global)
    - 1 non-US country present + no US indicator → exclude
    - 1 non-US country present + US indicator also present → keep
    """
    location = (job.get("location") or "").lower()
    if not location:
        return False  # keep, let LLM decide

    non_us_hits = sum(1 for country in _NON_US_COUNTRIES if country in location)
    if non_us_hits == 0:
        return False  # no non-US signal → keep

    # 2+ non-US countries = global role, exclude regardless of US presence
    if non_us_hits >= 2:
        return True

    # Exactly 1 non-US country: keep only if a US indicator is also present
    has_us = any(us in location for us in _US_INDICATORS)
    return not has_us


async def random_delay() -> None:
    """
    Sleep for a random duration between SCRAPER_MIN_DELAY_SECONDS and
    SCRAPER_MAX_DELAY_SECONDS to avoid hammering ATS APIs.
    """
    delay = random.uniform(SCRAPER_MIN_DELAY_SECONDS, SCRAPER_MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)
