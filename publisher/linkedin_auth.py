from __future__ import annotations

"""
LinkedIn token management.

Public API:
    get_valid_access_token() -> str

This is the only function the rest of the codebase should call.
It loads tokens, checks expiry, auto-refreshes if needed, and returns
a ready-to-use access token string.
"""

import os
import traceback

import requests
from dateutil import parser as dateutil_parser
from datetime import datetime, timedelta, timezone

from config.settings import (
    ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS,
    LINKEDIN_TOKEN_REFRESH_URL,
    REFRESH_TOKEN_WARNING_DAYS,
    TOKENS_PATH,
)
from setup_oauth import decrypt_tokens, encrypt_and_save_tokens
from utils.alerting import send_alert
from utils.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _days_until(iso_datetime_str: str) -> float:
    """Return number of days between now (UTC) and the given ISO-8601 datetime."""
    expiry = dateutil_parser.parse(iso_datetime_str)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    delta = expiry - datetime.now(timezone.utc)
    return delta.total_seconds() / 86400


def _do_refresh(tokens: dict, client_id: str, client_secret: str) -> dict:
    """
    POST to LinkedIn token refresh endpoint.

    On success: computes new access_expires_at, saves updated tokens, returns them.
    On invalid_grant: sends CRITICAL alert and raises RuntimeError.

    Note: LinkedIn does NOT issue a new refresh_token on refresh —
    refresh_expires_at remains unchanged from the original OAuth flow.
    """
    logger.info("Access token expiring soon — refreshing via LinkedIn API …")
    try:
        resp = requests.post(
            LINKEDIN_TOKEN_REFRESH_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        if "invalid_grant" in body.lower():
            msg = "LinkedIn refresh token is invalid or expired (invalid_grant)."
            send_alert(
                "CRITICAL",
                "invalid_grant",
                msg,
                traceback.format_exc(),
            )
            raise RuntimeError(msg) from exc
        send_alert(
            "CRITICAL",
            "token refresh",
            f"Token refresh HTTP error: {exc}",
            traceback.format_exc(),
        )
        raise

    raw = resp.json()
    encryption_key = os.environ["TOKEN_ENCRYPTION_KEY"]

    # Access token is new; refresh token and its expiry remain the same
    from datetime import timedelta
    from datetime import timezone as tz
    now = datetime.now(timezone.utc)
    access_ttl = int(raw.get("expires_in", 5184000))

    tokens["access_token"] = raw["access_token"]
    tokens["access_expires_at"] = (now + timedelta(seconds=access_ttl)).isoformat()
    # refresh_token and refresh_expires_at are NOT updated (LinkedIn doesn't roll them)

    encrypt_and_save_tokens(tokens, encryption_key, TOKENS_PATH)
    logger.info(
        "Access token refreshed successfully. "
        f"New expiry: {tokens['access_expires_at']}"
    )
    return tokens


def _handle_refresh_token_expiry(days_remaining: float) -> None:
    """Send appropriate alert based on how many days are left on the refresh token."""
    if days_remaining <= 0:
        msg = (
            "LinkedIn refresh token has EXPIRED. "
            "Run `python setup_oauth.py` immediately to re-authenticate."
        )
        logger.critical(msg)
        send_alert("CRITICAL", "invalid_grant", msg)
        raise RuntimeError(msg)

    if days_remaining < REFRESH_TOKEN_WARNING_DAYS:
        msg = (
            f"LinkedIn refresh token expires in {days_remaining:.0f} days. "
            f"Run `python setup_oauth.py` before day 365 to avoid disruption."
        )
        logger.warning(msg)
        send_alert("WARNING", "refresh token expiring", msg)


def load_and_refresh_tokens() -> dict:
    """
    Load tokens from disk, check expiry, refresh access token if needed.
    Returns the (potentially updated) token dict.
    """
    encryption_key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not encryption_key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY not set in environment.")

    tokens = decrypt_tokens(encryption_key, TOKENS_PATH)

    # --- Check refresh token first (most critical) ---
    refresh_days = _days_until(tokens["refresh_expires_at"])
    logger.debug(f"Refresh token expires in {refresh_days:.1f} days")
    _handle_refresh_token_expiry(refresh_days)

    # --- Check access token ---
    access_days = _days_until(tokens["access_expires_at"])
    logger.debug(f"Access token expires in {access_days:.1f} days")

    if access_days < ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS:
        client_id = os.environ["LINKEDIN_CLIENT_ID"]
        client_secret = os.environ["LINKEDIN_CLIENT_SECRET"]
        tokens = _do_refresh(tokens, client_id, client_secret)

    return tokens


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_valid_access_token() -> str:
    """
    Return a valid LinkedIn access token, refreshing it automatically if needed.

    This is the only function other modules should import from this file.
    """
    tokens = load_and_refresh_tokens()
    return tokens["access_token"]
