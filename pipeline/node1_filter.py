from __future__ import annotations

"""
LLM Node 1 — Filter, Categorise, and Score jobs.

Takes raw job dicts from scrapers, batches them, and calls GPT-4.1-mini
to filter for US relevance, assign categories, and score notability.

IMPORTANT:
- Uses response_format={"type": "json_object"} — NEVER json_schema
- Prompt must instruct model to wrap output in {"jobs": [...]}
- description field is stripped from output after processing (saves memory)
"""

import json
import os
import re
import time
import traceback
from typing import Any

from openai import AzureOpenAI
from pydantic import BaseModel, field_validator, ValidationError

from config.settings import (
    CATEGORY_KEYWORDS,
    LLM_API_VERSION,
    LLM_BACKOFF_BASE,
    LLM_BATCH_SIZE,
    LLM_MAX_RETRIES,
    LLM_MODEL_NAME,
    LLM_TEMPERATURE,
)
from utils.alerting import send_alert
from utils.logger import get_logger

logger = get_logger()

_ALLOWED_CATEGORIES = set(CATEGORY_KEYWORDS.keys())


# ---------------------------------------------------------------------------
# Pydantic output schema for Node 1
# ---------------------------------------------------------------------------

class FilteredJob(BaseModel):
    original_id: str
    category: str
    title: str
    company: str
    location: str
    salary_info: str | None = None
    remote_status: bool = False
    stack_keywords: list[str] = []
    notability_score: int = 0
    source_platform: str

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in _ALLOWED_CATEGORIES:
            raise ValueError(
                f"category '{v}' is not one of the 5 allowed values: {_ALLOWED_CATEGORIES}"
            )
        return v

    @field_validator("stack_keywords")
    @classmethod
    def cap_stack(cls, v: list) -> list:
        return v[:4]

    @field_validator("notability_score")
    @classmethod
    def clamp_score(cls, v: int) -> int:
        return max(0, min(v, 10))


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert data jobs curator. Your job is to filter, categorise, \
and score job postings for a daily LinkedIn newsletter targeting US-based \
mid-to-senior data professionals.

## Categories (exactly 5, no others allowed)
- Data Engineer
- Data Analyst
- ML Engineer
- Data Scientist
- AI Engineer

## Scoring rubric (additive, 0–10 scale)
+4 points: salary_info field is not null
+3 points: explicitly Remote or "Anywhere in US"
+3 points: well-known company (FAANG, major unicorn, Fortune 500, top startup)

## Hard exclusion rules (return NOTHING for these)
- Title contains: junior, entry, entry-level, intern, internship, graduate, \
new grad, early career, 0-2 years
- Location is outside the United States (exclude Canada, UK, Europe, APAC, \
Latin America)
- Role does not fit any of the 5 allowed categories

## Output format
Return a JSON object with a single key "jobs" whose value is an array.
Each element in the array must be a JSON object with these exact fields:
{
  "original_id": "<string — copy from input exactly>",
  "category": "<one of the 5 allowed categories>",
  "title": "<job title — copy from input exactly>",
  "company": "<company name — copy from input exactly>",
  "location": "<location — copy from input exactly>",
  "salary_info": "<copy the salary_info field from input exactly — do NOT re-extract from description>",
  "remote_status": <true or false>,
  "stack_keywords": ["<tech1>", "<tech2>", "<tech3>", "<tech4>"],
  "notability_score": <integer 0–10>,
  "source_platform": "<copy from input exactly>"
}

IMPORTANT — salary_info: copy the value from the input field exactly. \
If it is null in the input, output null. Do NOT attempt to extract salary \
from the description text.
stack_keywords: extract from the job description — max 4 technologies, \
most relevant first. Use short names (e.g. "Spark" not "Apache Spark").
Omit any job that fails the hard exclusion rules.
Return ONLY the JSON object. No explanation, no markdown, no code fences.\
"""

_HTML_TAG_RE = re.compile(r"<[^>]+>|&[a-z]+;|&#\d+;", re.IGNORECASE)


def _strip_html(text: str) -> str:
    """Remove HTML tags and common entities so description tokens are useful to the LLM."""
    cleaned = _HTML_TAG_RE.sub(" ", text)
    # Collapse multiple spaces
    return " ".join(cleaned.split())


def _build_messages(jobs_batch: list[dict]) -> list[dict]:
    """Build the messages array for one Node 1 API call."""
    slim_batch = [
        {
            "original_id": j.get("original_id", ""),
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            # Pre-extracted salary from scraper — LLM is instructed to copy as-is
            "salary_info": j.get("salary_info"),
            "source_platform": j.get("source_platform", ""),
            # Strip HTML before truncating so 2000 chars = real content, not markup
            "description": _strip_html((j.get("description", "") or ""))[:2000],
        }
        for j in jobs_batch
    ]
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Here are {len(slim_batch)} job postings. "
                f"Filter, categorise, and score them:\n\n"
                f"{json.dumps(slim_batch, indent=2)}"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

def _call_llm_with_retry(client: AzureOpenAI, messages: list[dict]) -> str:
    """
    Call GPT-4.1-mini with exponential backoff retry.

    Returns the raw content string on success.
    Sends CRITICAL alert and raises RuntimeError after all retries exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL_NAME,
                response_format={"type": "json_object"},  # NEVER json_schema
                temperature=LLM_TEMPERATURE,
                messages=messages,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_exc = exc
            wait = LLM_BACKOFF_BASE ** attempt
            logger.warning(
                f"Node 1 LLM call attempt {attempt}/{LLM_MAX_RETRIES} failed: {exc}. "
                f"Retrying in {wait:.0f}s …"
            )
            if attempt < LLM_MAX_RETRIES:
                time.sleep(wait)

    msg = f"Node 1 LLM call failed after {LLM_MAX_RETRIES} attempts: {last_exc}"
    send_alert("CRITICAL", "llm node 1", msg, traceback.format_exc())
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Parse and validate LLM output
# ---------------------------------------------------------------------------

def _parse_and_validate(content: str) -> list[dict]:
    """
    Parse the LLM JSON response and validate each job with Pydantic.

    Jobs that fail validation are logged and skipped (not raised).
    Returns a list of validated job dicts (with description stripped).
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning(f"Node 1: failed to parse LLM JSON response: {exc}")
        return []

    raw_jobs = parsed.get("jobs", [])
    if not isinstance(raw_jobs, list):
        logger.warning("Node 1: LLM response 'jobs' field is not a list.")
        return []

    valid: list[dict] = []
    for raw in raw_jobs:
        try:
            job = FilteredJob(**raw)
            valid.append(job.model_dump())
        except (ValidationError, TypeError) as exc:
            logger.debug(
                f"Node 1: skipping job '{raw.get('title', '?')}' — validation error: {exc}"
            )

    return valid


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_node1(jobs: list[dict]) -> list[dict]:
    """
    Filter, categorise, and score a list of raw job dicts using GPT-4.1-mini.

    Args:
        jobs: Raw job dicts from scrapers (universal schema, pre-dedup).

    Returns:
        Filtered, categorised, scored job dicts (description field removed).
        apply_url and posted_at are re-attached from the original jobs after
        LLM processing (the Pydantic model doesn't include them so they'd
        otherwise be dropped by model_dump()).
    """
    if not jobs:
        logger.info("Node 1: received 0 jobs — nothing to process.")
        return []

    # Build lookup so we can re-attach fields the LLM doesn't handle
    id_to_meta: dict[str, dict] = {
        j["original_id"]: {
            "apply_url": j.get("apply_url", ""),
            "posted_at": j.get("posted_at", ""),
        }
        for j in jobs
        if j.get("original_id")
    }

    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=LLM_API_VERSION,
    )

    batches = [
        jobs[i: i + LLM_BATCH_SIZE]
        for i in range(0, len(jobs), LLM_BATCH_SIZE)
    ]
    logger.info(
        f"Node 1: processing {len(jobs)} jobs in {len(batches)} batch(es) "
        f"of up to {LLM_BATCH_SIZE}."
    )

    all_filtered: list[dict] = []

    for idx, batch in enumerate(batches, start=1):
        logger.debug(f"Node 1: sending batch {idx}/{len(batches)} ({len(batch)} jobs) …")
        messages = _build_messages(batch)
        content = _call_llm_with_retry(client, messages)
        filtered = _parse_and_validate(content)
        # Re-attach apply_url and posted_at that were stripped by the Pydantic model
        for job in filtered:
            meta = id_to_meta.get(job.get("original_id", ""), {})
            job["apply_url"] = meta.get("apply_url", "")
            job["posted_at"] = meta.get("posted_at", "")
        all_filtered.extend(filtered)
        logger.debug(
            f"Node 1: batch {idx} — {len(batch)} in, {len(filtered)} passed filter."
        )

    logger.info(
        f"Node 1 complete: {len(jobs)} raw jobs → {len(all_filtered)} filtered."
    )
    return all_filtered
