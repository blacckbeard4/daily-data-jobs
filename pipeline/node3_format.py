"""
LLM Node 3 — Format the ranked jobs into a LinkedIn post string.

Pure Python template formatter — no LLM call needed.
The post structure is deterministic from the CLAUDE.md spec.

Template:
    [HOOK]
    [CATEGORY HEADER]
    [Job block x2 per category]
    [FOOTER]

Rules:
- Salary: show if present, else "Undisclosed"
- Stack: max 4 technologies, joined with " · "
- Character limit: hard stop at 2,800 chars
- Exactly 3 hashtags: #DataJobs #DataEngineering #AIJobs
"""

from config.settings import CATEGORY_HEADERS, CATEGORY_ORDER, LINKEDIN_POST_MAX_CHARS
from utils.logger import get_logger

logger = get_logger()

_FOOTER = (
    "All posted today.\n"
    "🔗 Apply links in first comment below ↓\n"
    "👥 Know someone job hunting? Tag them — you might change their week.\n"
    "🤝 Work at one of these companies and open to referring? "
    "Comment 'referral' + the company name and job seekers can reach out to you directly.\n"
    "#DataJobs #DataEngineering #AIJobs"
)

# ---------------------------------------------------------------------------
# Dynamic hook builder
# ---------------------------------------------------------------------------

def _build_hook(ranked: dict[str, list[dict]]) -> str:
    """
    Build the hook line dynamically from the companies in today's ranked jobs.

    Picks up to 3 unique company names (in category order), joins with " · ",
    and appends "& more" so the hook stays fresh every day.

    Example: "Fresh data roles from Airbnb, Discord & more."
    """
    seen: set[str] = set()
    companies: list[str] = []
    for cat in CATEGORY_ORDER:
        for job in ranked.get(cat, []):
            name = job.get("company", "")
            if name and name not in seen:
                seen.add(name)
                companies.append(name)
            if len(companies) == 3:
                break
        if len(companies) == 3:
            break

    if not companies:
        company_str = "top companies"
    elif len(companies) == 1:
        company_str = companies[0]
    else:
        company_str = ", ".join(companies[:-1]) + " & more"

    return (
        f"Fresh data roles from {company_str}.\n"
        "Salary included. Posted in the last 24 hours."
    )


# ---------------------------------------------------------------------------
# Job block formatter
# ---------------------------------------------------------------------------

def format_job_block(job: dict) -> str:
    """
    Format a single job into its 4-line post block.

    Example output:
        Databricks — Senior Data Engineer
        📍 Remote | 💰 $160,000 - $200,000
        Stack: Spark · Delta Lake · Python · SQL
        🔗 https://boards.greenhouse.io/...
    """
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown Role")
    location = job.get("location", "United States")
    salary = job.get("salary_info") or "Undisclosed"
    apply_url = job.get("apply_url", "")
    stack = job.get("stack_keywords", [])

    lines = [
        f"{company} — {title}",
        f"📍 {location} | 💰 {salary}",
    ]

    if stack:
        lines.append(f"Stack: {' · '.join(stack[:4])}")

    lines.append(f"🔗 {apply_url}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full post assembler
# ---------------------------------------------------------------------------

def format_post(ranked: dict[str, list[dict]]) -> str:
    """
    Assemble the full LinkedIn post from the ranked job dict.

    Args:
        ranked: Output of Node 2 — {category: [job, job]} dict.

    Returns:
        A plain text string ready to POST to LinkedIn,
        guaranteed to be <= LINKEDIN_POST_MAX_CHARS characters.
    """
    sections: list[str] = [_build_hook(ranked)]

    for cat in CATEGORY_ORDER:
        jobs = ranked.get(cat, [])
        if not jobs:
            continue  # Skip categories with no jobs

        header = CATEGORY_HEADERS[cat]
        job_blocks = "\n\n".join(format_job_block(j) for j in jobs)
        sections.append(f"{header}\n{job_blocks}")

    sections.append(_FOOTER)
    post = "\n\n".join(sections)

    char_count = len(post)
    logger.info(f"Node 3: post assembled — {char_count} characters.")

    if char_count > LINKEDIN_POST_MAX_CHARS:
        logger.warning(
            f"Node 3: post is {char_count} chars, over limit of {LINKEDIN_POST_MAX_CHARS}. "
            "Truncating gracefully …"
        )
        post = _truncate_gracefully(post)

    validate_post(post)
    return post


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def _truncate_gracefully(post: str) -> str:
    """
    Trim the post to fit within LINKEDIN_POST_MAX_CHARS.

    Strategy:
    - Always preserve the footer (hashtag line + CTA).
    - Remove complete job blocks from the end, never cut mid-line.
    - Separate footer from body, fit body, then re-attach footer.
    """
    footer_marker = "All posted today."
    footer_idx = post.rfind(footer_marker)

    if footer_idx == -1:
        # Fallback: hard truncate with ellipsis
        return post[: LINKEDIN_POST_MAX_CHARS - 3] + "..."

    body = post[:footer_idx].rstrip()
    footer = post[footer_idx:]

    budget = LINKEDIN_POST_MAX_CHARS - len(footer) - 2  # -2 for "\n\n" separator

    while len(body) > budget:
        # Find the last double-newline boundary and truncate there
        last_break = body.rfind("\n\n")
        if last_break == -1:
            break
        body = body[:last_break].rstrip()

    return f"{body}\n\n{footer}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_post(post: str) -> None:
    """
    Assert the post meets all LinkedIn posting rules.

    Raises ValueError if any rule is violated.
    """
    if len(post) > LINKEDIN_POST_MAX_CHARS:
        raise ValueError(
            f"Post is {len(post)} chars — exceeds limit of {LINKEDIN_POST_MAX_CHARS}."
        )

    # Exactly 3 hashtags
    hashtag_count = post.count("#")
    if hashtag_count != 3:
        raise ValueError(
            f"Post has {hashtag_count} hashtags — must have exactly 3."
        )

    # No code fences or JSON-like content
    for bad in ["```", "```python", "{\"", "**", "__"]:
        if bad in post:
            raise ValueError(f"Post contains forbidden pattern: '{bad}'")

    logger.debug(f"Node 3: post validation passed ({len(post)} chars, 3 hashtags).")
