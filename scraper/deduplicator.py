"""
SHA-256 job deduplication using a rolling 7-day hash store.

Hash key: SHA-256 of '{company}|{title}|{location}'.lower().strip()
Store format (seen_jobs.json): {"<hex_hash>": "<ISO-8601 UTC datetime>", ...}
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateutil_parser

from config.settings import DEDUP_HASH_TTL_DAYS, SEEN_JOBS_PATH
from utils.logger import get_logger

logger = get_logger()


def compute_hash(job: dict) -> str:
    """
    Compute a SHA-256 fingerprint for a job based on company, title, and location.

    Normalises all three fields to lowercase and strips whitespace before hashing
    so minor formatting differences don't create false duplicates.
    """
    key = (
        f"{job.get('company', '')}|"
        f"{job.get('title', '')}|"
        f"{job.get('location', '')}"
    ).lower().strip()
    return hashlib.sha256(key.encode()).hexdigest()


def load_seen_jobs(path: str = SEEN_JOBS_PATH) -> dict:
    """
    Load the seen-jobs hash store from disk.

    Returns an empty dict if the file is missing, empty, or malformed.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Could not read seen_jobs file at '{path}' — treating as empty.")
        return {}


def save_seen_jobs(seen: dict, path: str = SEEN_JOBS_PATH) -> None:
    """Persist the hash store to disk as pretty-printed JSON."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(seen, f, indent=2)


def purge_old_hashes(seen: dict) -> dict:
    """
    Remove hashes that were first seen more than DEDUP_HASH_TTL_DAYS ago.

    Returns a new dict — does not mutate the input.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_HASH_TTL_DAYS)
    kept = {}
    purged = 0
    for hash_hex, ts_str in seen.items():
        try:
            seen_at = dateutil_parser.parse(ts_str)
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            if seen_at >= cutoff:
                kept[hash_hex] = ts_str
            else:
                purged += 1
        except Exception:
            # Malformed timestamp — drop the entry
            purged += 1

    if purged:
        logger.debug(f"Deduplicator: purged {purged} hashes older than {DEDUP_HASH_TTL_DAYS} days.")
    return kept


def deduplicate(jobs: list[dict]) -> list[dict]:
    """
    Filter a list of raw job dicts to only those not seen in the last 7 days.

    Side effects:
    - Loads seen_jobs.json from disk
    - Purges old hashes
    - Saves updated hash store (with new jobs' hashes) back to disk

    Returns the filtered list of new-only jobs.
    """
    seen = load_seen_jobs()
    seen = purge_old_hashes(seen)

    now_str = datetime.now(timezone.utc).isoformat()
    new_jobs: list[dict] = []

    for job in jobs:
        h = compute_hash(job)
        if h not in seen:
            new_jobs.append(job)
            seen[h] = now_str

    total = len(jobs)
    skipped = total - len(new_jobs)
    logger.info(
        f"Deduplication: {total} jobs in → {len(new_jobs)} new, {skipped} duplicates skipped."
    )

    save_seen_jobs(seen)
    return new_jobs
