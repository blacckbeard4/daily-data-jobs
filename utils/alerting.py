"""
Telegram bot failure alerting.

Replaces Gmail SMTP. Uses the Telegram Bot API to send alert messages
to a personal chat whenever the pipeline encounters an error.

Required .env variables:
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — your personal chat ID (run get_telegram_chat_id() to find it)

All alert calls are fire-and-forget — exceptions are swallowed so that
a Telegram API failure never crashes the main pipeline.
"""

import os
import sys
import traceback as tb
from datetime import datetime, timezone
from typing import Literal

import requests

AlertLevel = Literal["INFO", "WARNING", "CRITICAL"]

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Suggested actions keyed on error_type substrings (first match wins)
_ACTION_MAP: dict[str, str] = {
    "invalid_grant": (
        "LinkedIn refresh token expired.\n"
        "👉 Run: <code>python setup_oauth.py</code>"
    ),
    "0 new jobs": (
        "No new jobs found across all scrapers.\n"
        "👉 Check Greenhouse/Lever API connectivity and scraper logs."
    ),
    "token refresh": (
        "Access token refresh failed.\n"
        "👉 Verify LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env"
    ),
    "llm": (
        "Azure OpenAI API call failed after all retries.\n"
        "👉 Check AZURE_OPENAI_API_KEY, endpoint, and account quota."
    ),
    "linkedin post": (
        "LinkedIn ugcPosts API call failed.\n"
        "👉 Check access token and w_member_social scope."
    ),
    "refresh token expiring": (
        "LinkedIn refresh token expires soon.\n"
        "👉 Schedule time to run <code>python setup_oauth.py</code> before it expires."
    ),
}

_DEFAULT_ACTION = "👉 Review logs at <code>logs/pipeline.log</code>"

_LEVEL_EMOJI: dict[str, str] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "CRITICAL": "🚨",
}


def _suggest_action(error_type: str) -> str:
    lower = error_type.lower()
    for key, action in _ACTION_MAP.items():
        if key in lower:
            return action
    return _DEFAULT_ACTION


def send_alert(
    level: AlertLevel,
    error_type: str,
    message: str,
    traceback_str: str = "",
) -> None:
    """
    Send a failure alert to Telegram.

    This function never raises — all exceptions are caught and written to stderr.
    The pipeline must not be crashed by a Telegram API failure.

    Args:
        level:         "INFO", "WARNING", or "CRITICAL"
        error_type:    Short descriptor used in the message header
        message:       Human-readable error summary
        traceback_str: Full Python traceback string (optional; truncated to 800 chars)
    """
    try:
        _send(level, error_type, message, traceback_str)
    except Exception:
        print(
            f"[ALERTING] Failed to send Telegram alert: {tb.format_exc()}",
            file=sys.stderr,
        )


def _send(
    level: AlertLevel,
    error_type: str,
    message: str,
    traceback_str: str,
) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        print(
            "[ALERTING] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured — "
            "skipping alert.",
            file=sys.stderr,
        )
        return

    emoji = _LEVEL_EMOJI.get(level, "⚠️")
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build message with HTML formatting (Telegram parse_mode=HTML)
    parts = [
        f"{emoji} <b>[DAILY DATA JOBS] {level}</b>",
        f"<b>Error:</b> {error_type}",
        f"<b>Time:</b> {now_utc}",
        f"<b>Detail:</b> {message}",
        "",
        _suggest_action(error_type),
    ]

    if traceback_str:
        # Truncate to keep message under Telegram's 4096 char limit
        tb_excerpt = traceback_str.strip()[-800:]
        parts += ["", f"<pre>{tb_excerpt}</pre>"]

    text = "\n".join(parts)

    resp = requests.post(
        _TELEGRAM_API.format(token=bot_token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Helper: find your chat ID (run this once after messaging your bot)
# ---------------------------------------------------------------------------

def get_telegram_chat_id() -> None:
    """
    Print the chat ID for the first user who has messaged the bot.

    Usage:
        python -c "from utils.alerting import get_telegram_chat_id; get_telegram_chat_id()"

    Before running:
        1. Open Telegram and search for @Daily_jobs_justin_bot
        2. Send any message (e.g. /start)
        3. Then run this function
    """
    from dotenv import load_dotenv
    load_dotenv()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        print("TELEGRAM_BOT_TOKEN not set in .env", file=sys.stderr)
        return

    resp = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        timeout=10,
    )
    data = resp.json()

    updates = data.get("result", [])
    if not updates:
        print(
            "No messages found. Make sure you have sent at least one message "
            "to @Daily_jobs_justin_bot on Telegram first, then re-run this."
        )
        return

    for update in updates:
        chat = (
            update.get("message", {}).get("chat", {})
            or update.get("my_chat_member", {}).get("chat", {})
        )
        if chat:
            print(f"Chat ID : {chat['id']}")
            print(f"Name    : {chat.get('first_name', '')} {chat.get('last_name', '')}".strip())
            print(f"\nAdd this to your .env:\n  TELEGRAM_CHAT_ID={chat['id']}")
            return

    print("Could not extract chat ID from updates. Full response:")
    import json
    print(json.dumps(data, indent=2))
