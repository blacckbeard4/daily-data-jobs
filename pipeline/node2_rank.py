"""
LLM Node 2 — Rank and select top 2 jobs per category.

Pure Python implementation — no LLM call needed.
The ranking is deterministic given the scoring rubric from Node 1.

Selection strategy (salary-first):
  1. Take up to 2 jobs WITH salary per category (sorted by remote, then score).
  2. Only fall back to no-salary jobs if a category would otherwise have 0 jobs
     — never use them just to pad a category that already has 1 salary job.

This keeps "Undisclosed" out of the post body except as a last resort.

Tiebreaker within salary/no-salary groups (descending priority):
  1. remote_status = True
  2. notability_score
"""

from config.settings import CATEGORY_ORDER
from utils.logger import get_logger

logger = get_logger()


def _rank_key(job: dict) -> tuple[int, int]:
    """Sort key within a salary/no-salary group. Higher = better."""
    remote = 1 if job.get("remote_status") else 0
    score = int(job.get("notability_score", 0))
    return (remote, score)


def rank_and_select(filtered_jobs: list[dict]) -> dict[str, list[dict]]:
    """
    Group jobs by category, select up to 2 salary jobs per category.
    Fall back to no-salary jobs only if a category would otherwise be empty.

    Args:
        filtered_jobs: Output of Node 1 — filtered, scored job dicts.

    Returns:
        Dict with all 5 category keys, each holding a list of 0–2 job dicts.
        The dict is ordered to match the LinkedIn post template.
    """
    grouped: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_ORDER}

    for job in filtered_jobs:
        cat = job.get("category")
        if cat in grouped:
            grouped[cat].append(job)

    ranked: dict[str, list[dict]] = {}
    total_selected = 0

    for cat in CATEGORY_ORDER:
        candidates = grouped[cat]

        with_salary = sorted(
            [j for j in candidates if j.get("salary_info")],
            key=_rank_key,
            reverse=True,
        )
        no_salary = sorted(
            [j for j in candidates if not j.get("salary_info")],
            key=_rank_key,
            reverse=True,
        )

        if with_salary:
            # Always prefer salary jobs; never mix in no-salary to pad to 2
            selected = with_salary[:2]
        else:
            # Category would be empty — fall back to no-salary as last resort
            selected = no_salary[:2]

        ranked[cat] = selected
        total_selected += len(selected)

        salary_count = sum(1 for j in selected if j.get("salary_info"))
        logger.debug(
            f"Node 2: {cat} — {len(candidates)} candidates "
            f"({len(with_salary)} w/ salary) → {len(selected)} selected "
            f"({salary_count} with salary)."
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
