from __future__ import annotations

"""
LinkedIn ugcPosts publisher.

Usage:
    from publisher.linkedin_post import post_to_linkedin
    result = post_to_linkedin(post_text, dry_run=False)

Dry-run mode (prints payload, makes no HTTP call):
    python publisher/linkedin_post.py --dry-run
"""

import argparse
import json
import os
import time
import traceback

import requests

from config.settings import (
    LINKEDIN_POST_MAX_CHARS,
    LINKEDIN_POST_RETRIES,
    LINKEDIN_UGCPOSTS_URL,
)
from publisher.linkedin_auth import get_valid_access_token
from utils.alerting import send_alert
from utils.logger import get_logger

logger = get_logger()

_REQUIRED_HEADERS = {
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def build_ugcposts_payload(post_text: str, person_urn: str) -> dict:
    """
    Build the ugcPosts JSON payload.

    Raises:
        ValueError: If post_text exceeds the character limit.
    """
    if len(post_text) > LINKEDIN_POST_MAX_CHARS:
        raise ValueError(
            f"Post text is {len(post_text)} chars — exceeds limit of {LINKEDIN_POST_MAX_CHARS}."
        )
    return {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_text},
                "shareMediaCategory": "NONE",
            },
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }


# ---------------------------------------------------------------------------
# HTTP with retry
# ---------------------------------------------------------------------------

def _send_with_retry(payload: dict, access_token: str) -> dict:
    """
    POST payload to LinkedIn ugcPosts endpoint with retry logic.

    Retry policy:
      - 5xx: retry up to LINKEDIN_POST_RETRIES times with 5s wait
      - 4xx: do NOT retry; send CRITICAL alert and raise
      - 201: success

    Returns the API response dict on success.
    """
    headers = {**_REQUIRED_HEADERS, "Authorization": f"Bearer {access_token}"}

    last_exc: Exception | None = None
    for attempt in range(1, LINKEDIN_POST_RETRIES + 2):  # +2: initial attempt + retries
        try:
            resp = requests.post(
                LINKEDIN_UGCPOSTS_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(f"LinkedIn POST attempt {attempt} — network error: {exc}")
            if attempt <= LINKEDIN_POST_RETRIES:
                time.sleep(5)
            continue

        if resp.status_code == 201:
            post_urn = resp.headers.get("X-RestLi-Id", "unknown")
            logger.info(f"LinkedIn post published successfully. URN: {post_urn}")
            try:
                result = resp.json()
            except Exception:
                result = {}
            result["urn"] = post_urn
            return result

        if 400 <= resp.status_code < 500:
            # Client errors — don't retry
            msg = (
                f"LinkedIn API returned {resp.status_code}: {resp.text[:500]}"
            )
            logger.error(msg)
            send_alert(
                "CRITICAL",
                "linkedin post",
                msg,
                f"Payload:\n{json.dumps(payload, indent=2)}",
            )
            resp.raise_for_status()

        # 5xx — server error, retry
        logger.warning(
            f"LinkedIn POST attempt {attempt} — server error {resp.status_code}. "
            f"Retrying in 5s …"
        )
        last_exc = requests.HTTPError(response=resp)
        if attempt <= LINKEDIN_POST_RETRIES:
            time.sleep(5)

    # All attempts exhausted
    msg = (
        f"LinkedIn POST failed after {LINKEDIN_POST_RETRIES + 1} attempts. "
        f"Last error: {last_exc}"
    )
    logger.error(msg)
    send_alert("CRITICAL", "linkedin post", msg, traceback.format_exc())
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_to_linkedin(post_text: str, dry_run: bool = False) -> dict:
    """
    Publish a post to LinkedIn.

    Returns:
        On success: API response dict with post URN.
        On dry_run: {"dry_run": True, "char_count": int, "payload": dict}
    """
    person_urn = os.environ.get("LINKEDIN_PERSON_URN", "")

    if dry_run:
        eff_urn = person_urn or "urn:li:person:DRY_RUN"
        payload = build_ugcposts_payload(post_text, eff_urn)
        logger.info("[DRY RUN] LinkedIn post payload:")
        print("\n" + "=" * 60)
        print("LINKEDIN POST — DRY RUN")
        print("=" * 60)
        print(f"Character count: {len(post_text)} / {LINKEDIN_POST_MAX_CHARS}")
        print("-" * 60)
        print(post_text)
        print("-" * 60)
        print("ugcPosts payload:")
        print(json.dumps(payload, indent=2))
        print("=" * 60 + "\n")
        return {"dry_run": True, "char_count": len(post_text), "payload": payload}

    if not person_urn:
        raise RuntimeError("LINKEDIN_PERSON_URN not set in environment.")

    access_token = get_valid_access_token()
    payload = build_ugcposts_payload(post_text, person_urn)
    return _send_with_retry(payload, access_token)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test LinkedIn publisher")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without posting")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    _SAMPLE_POST = """\
Fresh data roles from Databricks, Snowflake & more.
Posted in the last 24 hours.

⚙️ DATA ENGINEER
Databricks — Senior Data Engineer
📍 Remote | 💰 $160,000 - $200,000
Stack: Apache Spark · Delta Lake · Python · SQL
🔗 https://boards.greenhouse.io/databricks/jobs/12345

Snowflake — Staff Data Engineer
📍 San Francisco, CA | 💰 Undisclosed
Stack: Snowflake · dbt · Airflow · Python
🔗 https://boards.greenhouse.io/snowflake/jobs/67890

📊 DATA ANALYST
Example Corp — Senior Data Analyst
📍 New York, NY | 💰 $120,000 - $150,000
Stack: SQL · Tableau · Python · dbt
🔗 https://example.com/jobs/1

Another Co — Lead BI Analyst
📍 Remote | 💰 Undisclosed
Stack: Looker · SQL · BigQuery
🔗 https://example.com/jobs/2

All posted today. Updated every morning at 8AM ET.
👥 Know someone job hunting? Tag them — you might change their week.
🤝 Work at one of these companies and open to referring? Comment 'referral' + the company name and job seekers can reach out to you directly.
🔔 Follow for fresh data roles every morning at 8AM ET.
#DataJobs #DataEngineering #AIJobs"""

    result = post_to_linkedin(_SAMPLE_POST, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"Published: {result}")
