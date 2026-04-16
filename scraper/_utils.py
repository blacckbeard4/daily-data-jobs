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


def matches_category_keyword(title: str) -> bool:
    """
    Return True if the job title (case-insensitive) contains any keyword
    from any of the 5 target categories.
    """
    lower = title.lower()
    for keywords in CATEGORY_KEYWORDS.values():
        for kw in keywords:
            if kw in lower:
                return True
    return False


def is_excluded_experience(title: str) -> bool:
    """
    Return True if the job title contains any experience-level exclusion term.
    Used as a pre-filter before sending to the LLM.
    """
    lower = title.lower()
    return any(term in lower for term in EXPERIENCE_EXCLUDE_TERMS)


async def random_delay() -> None:
    """
    Sleep for a random duration between SCRAPER_MIN_DELAY_SECONDS and
    SCRAPER_MAX_DELAY_SECONDS to avoid hammering ATS APIs.
    """
    delay = random.uniform(SCRAPER_MIN_DELAY_SECONDS, SCRAPER_MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)
