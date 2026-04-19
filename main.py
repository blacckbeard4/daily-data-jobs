"""
Daily Data Jobs — Main Orchestrator

Called by cron every weekday (Tue–Fri) at 8AM ET (13:00 UTC):
    0 13 * * 2-5 /home/ubuntu/daily-data-jobs/venv/bin/python \
      /home/ubuntu/daily-data-jobs/main.py >> \
      /home/ubuntu/daily-data-jobs/logs/cron.log 2>&1

Usage:
    python main.py            # run full pipeline and post to LinkedIn
    python main.py --dry-run  # run full pipeline, print post, skip LinkedIn POST
"""

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

# load_dotenv() must be called BEFORE any module that reads os.environ
from dotenv import load_dotenv
load_dotenv()

from config.settings import COMPANIES_PATH
from pipeline.node1_filter import run_node1
from pipeline.node2_rank import rank_and_select
from pipeline.node3_format import format_post
from publisher.linkedin_post import post_comment_to_linkedin, post_to_linkedin
from scraper._utils import filter_non_us
from scraper.deduplicator import deduplicate
from scraper.greenhouse import scrape_all_greenhouse
from scraper.lever import scrape_all_lever
from scraper.ashby import scrape_all_ashby
from scraper.himalayas import scrape_himalayas
from scraper.aijobs import scrape_aijobs
from utils.alerting import send_alert
from utils.logger import get_logger, setup_logger

# Initialise logger early so all module-level loggers use the same instance
setup_logger()
logger = get_logger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily Data Jobs pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but skip the actual LinkedIn POST",
    )
    return parser.parse_args()


def load_company_slugs() -> dict:
    """Load config/companies.json and return the full dict."""
    with open(COMPANIES_PATH) as f:
        return json.load(f)


def _elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

async def run_scrapers(slugs: dict) -> list[dict]:
    """
    Run all five scrapers concurrently and return a combined flat job list.

    Greenhouse, Lever, and Ashby scrape company-specific ATS boards.
    Himalayas and AIJobs scrape broad job board feeds.
    All run concurrently; individual failures are logged and skipped.
    """
    greenhouse_slugs: list[str] = slugs.get("greenhouse", [])
    lever_slugs: list[str] = slugs.get("lever", [])
    ashby_slugs: list[str] = slugs.get("ashby", [])

    t = time.time()
    logger.info(
        f"Starting all scrapers concurrently — "
        f"Greenhouse ({len(greenhouse_slugs)} slugs), "
        f"Lever ({len(lever_slugs)} slugs), "
        f"Ashby ({len(ashby_slugs)} slugs), "
        f"Himalayas, AIJobs …"
    )

    results = await asyncio.gather(
        scrape_all_greenhouse(greenhouse_slugs),
        scrape_all_lever(lever_slugs),
        scrape_all_ashby(ashby_slugs),
        scrape_himalayas(),
        scrape_aijobs(),
        return_exceptions=True,
    )

    _NAMES = ["Greenhouse", "Lever", "Ashby", "Himalayas", "AIJobs"]
    all_jobs: list[dict] = []
    for name, result in zip(_NAMES, results):
        if isinstance(result, Exception):
            logger.error(f"{name} scraper raised an unhandled exception: {result}")
        else:
            logger.info(f"{name}: {len(result)} jobs")
            all_jobs.extend(result)

    logger.info(f"All scrapers finished in {_elapsed(t)}. Total raw jobs: {len(all_jobs)}")

    # Pre-LLM location filter: drop clearly non-US jobs before dedup/Node1
    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if not filter_non_us(j)]
    removed = before - len(all_jobs)
    if removed:
        logger.info(f"Non-US pre-filter: removed {removed} jobs, {len(all_jobs)} remaining.")

    return all_jobs


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(raw_jobs: list[dict], dry_run: bool) -> tuple[str, dict]:
    """
    Run the full processing pipeline: dedup → Node 1 → Node 2 → Node 3.

    Returns the formatted post text.
    Sends an alert and exits cleanly if no new jobs are found after dedup.
    """
    # Deduplication
    t = time.time()
    new_jobs = deduplicate(raw_jobs)
    logger.info(f"Dedup: {len(raw_jobs)} raw → {len(new_jobs)} new [{_elapsed(t)}]")

    if not new_jobs:
        msg = "0 new jobs found after deduplication. Skipping today's post."
        logger.warning(msg)
        send_alert("WARNING", "0 new jobs", msg)
        sys.exit(0)

    # Node 1: Filter + Categorise + Score
    t = time.time()
    logger.info("Node 1: filtering, categorising, and scoring …")
    filtered_jobs = run_node1(new_jobs)
    logger.info(
        f"Node 1: {len(new_jobs)} new → {len(filtered_jobs)} filtered [{_elapsed(t)}]"
    )

    if not filtered_jobs:
        msg = "Node 1 filtered out all jobs — no valid US data roles found today."
        logger.warning(msg)
        send_alert("WARNING", "0 new jobs", msg)
        sys.exit(0)

    # Node 2: Rank + select top 2 per category
    t = time.time()
    logger.info("Node 2: ranking and selecting top 2 per category …")
    ranked = rank_and_select(filtered_jobs)
    logger.info(f"Node 2: selection complete [{_elapsed(t)}]")

    # Node 3: Format LinkedIn post
    t = time.time()
    logger.info("Node 3: formatting LinkedIn post …")
    post_text = format_post(ranked)
    logger.info(
        f"Node 3: post formatted — {len(post_text)} chars [{_elapsed(t)}]"
    )

    return post_text, ranked


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    run_start = time.time()
    args = parse_args()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"=== Daily Data Jobs starting [{mode}] at {now_utc} ===")

    try:
        slugs = load_company_slugs()
        logger.info(
            f"Loaded {len(slugs.get('greenhouse', []))} Greenhouse slugs, "
            f"{len(slugs.get('lever', []))} Lever slugs, "
            f"{len(slugs.get('ashby', []))} Ashby slugs."
        )

        # --- Scrape ---
        raw_jobs = asyncio.run(run_scrapers(slugs))

        if not raw_jobs:
            msg = "All scrapers returned 0 jobs. Check network connectivity and ATS APIs."
            logger.error(msg)
            send_alert("CRITICAL", "0 new jobs", msg)
            sys.exit(1)

        # --- Pipeline ---
        post_text, ranked = run_pipeline(raw_jobs, args.dry_run)

        # --- Publish ---
        logger.info("Publishing to LinkedIn …")
        result = post_to_linkedin(post_text, ranked=ranked, dry_run=args.dry_run)

        if args.dry_run:
            logger.info("DRY RUN complete — no post was published.")
            post_comment_to_linkedin("urn:li:ugcPost:DRY_RUN", ranked, dry_run=True)
        else:
            post_urn = result.get("urn", "unknown")
            logger.info(f"LinkedIn post published: {result}")
            post_comment_to_linkedin(post_urn, ranked, dry_run=False)

    except SystemExit:
        raise  # Allow clean sys.exit() calls from run_pipeline to propagate
    except Exception:
        tb_str = traceback.format_exc()
        logger.critical(f"Unhandled exception in main.py:\n{tb_str}")
        send_alert(
            "CRITICAL",
            "unhandled exception",
            "Daily Data Jobs pipeline crashed unexpectedly.",
            tb_str,
        )
        sys.exit(1)

    elapsed = time.time() - run_start
    logger.info(f"=== Run complete in {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()
