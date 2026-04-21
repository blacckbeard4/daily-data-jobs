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
import sys
import time
import traceback
import urllib.parse

import pyshorteners
import requests

from config.settings import (
    CATEGORY_ORDER,
    LINKEDIN_POST_MAX_CHARS,
    LINKEDIN_POST_RETRIES,
    LINKEDIN_UGCPOSTS_URL,
)
from publisher.image_generator import generate_image
from publisher.linkedin_auth import get_valid_access_token
from utils.alerting import send_alert
from utils.logger import get_logger

logger = get_logger()

_REQUIRED_HEADERS = {
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}

LINKEDIN_ASSETS_URL = "https://api.linkedin.com/v2/assets"


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

def build_ugcposts_payload(
    post_text: str,
    person_urn: str,
    asset_urn: str | None = None,
) -> dict:
    """
    Build the ugcPosts JSON payload.

    Args:
        post_text:  Formatted post text (<= LINKEDIN_POST_MAX_CHARS).
        person_urn: LinkedIn person URN.
        asset_urn:  LinkedIn media asset URN; if set, attaches image.

    Raises:
        ValueError: If post_text exceeds the character limit.
    """
    if len(post_text) > LINKEDIN_POST_MAX_CHARS:
        raise ValueError(
            f"Post text is {len(post_text)} chars — exceeds limit of {LINKEDIN_POST_MAX_CHARS}."
        )
    share_content: dict = {
        "shareCommentary": {"text": post_text},
        "shareMediaCategory": "IMAGE" if asset_urn else "NONE",
    }
    if asset_urn:
        share_content["media"] = [{
            "status": "READY",
            "media": asset_urn,
            "title": {"text": "Today's top data roles"},
        }]
    return {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content,
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
# Image upload helpers
# ---------------------------------------------------------------------------

def _register_image_upload(person_urn: str, access_token: str) -> tuple[str, str] | None:
    """
    Register an image upload with LinkedIn.
    Returns (upload_url, asset_urn) on success, None on failure.
    """
    headers = {**_REQUIRED_HEADERS, "Authorization": f"Bearer {access_token}"}
    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent",
            }],
        }
    }
    try:
        resp = requests.post(
            f"{LINKEDIN_ASSETS_URL}?action=registerUpload",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"registerUpload returned {resp.status_code}: {resp.text[:300]}")
            return None
        value = resp.json().get("value", {})
        upload_url = (
            value
            .get("uploadMechanism", {})
            .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
            .get("uploadUrl")
        )
        asset_urn = value.get("asset")
        if not upload_url or not asset_urn:
            logger.error(f"registerUpload: missing uploadUrl or asset in response: {value}")
            return None
        return upload_url, asset_urn
    except Exception as exc:
        logger.error(f"registerUpload request failed: {exc}")
        return None


def _upload_image_bytes(upload_url: str, image_path: str, access_token: str) -> bool:
    """PUT image bytes to LinkedIn's upload URL. Returns True on success."""
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        resp = requests.put(
            upload_url,
            data=image_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
            },
            timeout=60,
        )
        if resp.status_code in (200, 201):
            logger.info("Image uploaded to LinkedIn successfully.")
            return True
        logger.error(f"Image PUT returned {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as exc:
        logger.error(f"Image upload request failed: {exc}")
        return False


def upload_image_to_linkedin(image_path: str, person_urn: str, access_token: str) -> str | None:
    """
    Full image upload: register → PUT bytes.
    Returns asset URN on success, or None on failure (non-fatal).
    """
    result = _register_image_upload(person_urn, access_token)
    if not result:
        send_alert("WARNING", "linkedin image upload", "registerUpload failed — posting text-only.")
        return None
    upload_url, asset_urn = result
    if not _upload_image_bytes(upload_url, image_path, access_token):
        send_alert(
            "WARNING",
            "linkedin image upload",
            f"Image PUT failed — posting text-only. Asset: {asset_urn}",
        )
        return None
    return asset_urn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_to_linkedin(post_text: str, ranked: dict | None = None, dry_run: bool = False) -> dict:
    """
    Publish a post to LinkedIn, optionally with a generated header image.

    Args:
        post_text: The fully formatted post string.
        ranked:    Ranked jobs dict used to generate the header image. Optional.
        dry_run:   If True, generate image but skip all HTTP calls.

    Returns:
        On success: API response dict with post URN.
        On dry_run: {"dry_run": True, "char_count": int, "payload": dict}
    """
    person_urn = os.environ.get("LINKEDIN_PERSON_URN", "")

    # --- Image generation (always attempt when ranked is provided) ---
    image_path: str | None = None
    asset_urn: str | None = None

    if ranked:
        image_path = generate_image(ranked, dry_run=dry_run)
        if image_path:
            if dry_run:
                print(f"Image saved to {image_path}")
                asset_urn = "urn:li:digitalmediaAsset:DRY_RUN"
            # Live upload happens below after we have the access token

    if dry_run:
        eff_urn = person_urn or "urn:li:person:DRY_RUN"
        payload = build_ugcposts_payload(post_text, eff_urn, asset_urn)
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

    if image_path:
        asset_urn = upload_image_to_linkedin(image_path, person_urn, access_token)

    payload = build_ugcposts_payload(post_text, person_urn, asset_urn)
    return _send_with_retry(payload, access_token)


# ---------------------------------------------------------------------------
# First-comment apply links
# ---------------------------------------------------------------------------

_SOCIAL_ACTIONS_BASE = "https://api.linkedin.com/v2/socialActions"


def _shorten_url(url: str) -> str:
    """Shorten a URL via TinyURL. Falls back to the original URL on any error."""
    if not url:
        return url
    try:
        s = pyshorteners.Shortener()
        return s.tinyurl.short(url)
    except Exception as exc:
        logger.warning(f"URL shortening failed for {url[:80]} — using full URL. Error: {exc}")
        return url


def build_comment_text(ranked: dict) -> str:
    """Build the apply-links comment text from ranked jobs, with TinyURL-shortened links."""
    lines = ["🔗 Apply links:", ""]
    for cat in CATEGORY_ORDER:
        for job in ranked.get(cat, []):
            title = job.get("title", "Role")
            company = job.get("company", "Company")
            url = _shorten_url(job.get("apply_url", ""))
            lines.append(f"{title} @ {company} → {url}")
    return "\n".join(lines)


def post_comment_to_linkedin(
    post_urn: str,
    ranked: dict,
    dry_run: bool = False,
) -> None:
    """
    Post apply links as the first comment on the published post.

    Non-fatal: logs error and sends alert on failure; never raises.
    """
    comment_text = build_comment_text(ranked)
    person_urn = os.environ.get("LINKEDIN_PERSON_URN", "")

    if dry_run:
        print("\n" + "=" * 60)
        print("LINKEDIN COMMENT — DRY RUN")
        print("=" * 60)
        print(f"Post URN: {post_urn}")
        print("-" * 60)
        print(comment_text)
        print("=" * 60 + "\n")
        return

    if not person_urn:
        logger.error("LINKEDIN_PERSON_URN not set — cannot post comment.")
        return

    encoded_urn = urllib.parse.quote(post_urn, safe="")
    comment_url = f"{_SOCIAL_ACTIONS_BASE}/{encoded_urn}/comments"
    payload = {
        "actor": person_urn,
        "message": {"text": comment_text},
    }
    access_token = get_valid_access_token()
    headers = {**_REQUIRED_HEADERS, "Authorization": f"Bearer {access_token}"}

    try:
        resp = requests.post(comment_url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 201:
            logger.info("Apply-links comment posted successfully.")
        else:
            msg = f"Comment API returned {resp.status_code}: {resp.text[:500]}"
            logger.error(msg)
            send_alert("WARNING", "linkedin comment", msg, f"Post URN: {post_urn}")
    except requests.RequestException as exc:
        msg = f"Comment API request failed: {exc}"
        logger.error(msg)
        send_alert("WARNING", "linkedin comment", msg, traceback.format_exc())


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
10 data roles posted in the last 24 hours.
Salary included. Real companies hiring today.

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
👇 Which company do you want in tomorrow's list?
#DataJobs #DataEngineering #AIJobs"""

    result = post_to_linkedin(_SAMPLE_POST, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"Published: {result}")
