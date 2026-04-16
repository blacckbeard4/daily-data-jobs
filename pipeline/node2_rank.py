"""
LLM Node 2 — Rank and select top 2 jobs per category.

Pure Python implementation — no LLM call needed.
The ranking is deterministic given the scoring rubric from Node 1.

Tiebreaker order (descending priority):
  1. salary_info present  (strongest signal for the audience)
  2. remote_status = True (second most valued)
  3. notability_score     (additive score from Node 1 rubric)

Output: dict with exactly 5 keys (one per category), each a list of <= 2 jobs.
"""

from config.settings import CATEGORY_ORDER
from utils.logger import get_logger

logger = get_logger()


def _tiebreaker_key(job: dict) -> tuple[int, int, int]:
    """
    Compute the sort key for a job (higher = better).

    Returns a tuple so Python's lexicographic tuple comparison gives us
    the right priority order when we sort descending.
    """
    salary_present = 1 if job.get("salary_info") else 0
    remote = 1 if job.get("remote_status") else 0
    score = int(job.get("notability_score", 0))
    return (salary_present, remote, score)


def rank_and_select(filtered_jobs: list[dict]) -> dict[str, list[dict]]:
    """
    Group jobs by category, sort by tiebreaker, and take the top 2 per category.

    Args:
        filtered_jobs: Output of Node 1 — filtered, scored job dicts.

    Returns:
        Dict with all 5 category keys, each holding a list of 0–2 job dicts.
        The dict is ordered to match the LinkedIn post template.
    """
    # Initialise with all 5 categories in template order (even if empty)
    grouped: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}

    for job in filtered_jobs:
        cat = job.get("category")
        if cat in grouped:
            grouped[cat].append(job)

    ranked: dict[str, list[dict]] = {}
    total_selected = 0

    for cat in CATEGORY_ORDER:
        candidates = grouped[cat]
        candidates.sort(key=_tiebreaker_key, reverse=True)
        top2 = candidates[:2]
        ranked[cat] = top2
        total_selected += len(top2)
        logger.debug(
            f"Node 2: {cat} — {len(candidates)} candidates → {len(top2)} selected."
        )

    logger.info(
        f"Node 2 complete: {len(filtered_jobs)} filtered → {total_selected} selected "
        f"across {len(CATEGORY_ORDER)} categories."
    )
    return ranked


def get_top_jobs_flat(ranked: dict[str, list[dict]]) -> list[dict]:
    """
    Flatten the ranked dict to a single ordered list.

    Preserves the category order defined in CATEGORY_ORDER.
    Useful for logging or passing to downstream consumers that expect a flat list.
    """
    return [job for cat in CATEGORY_ORDER for job in ranked.get(cat, [])]
