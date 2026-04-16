"""
Tests for the LLM pipeline nodes.

Run with: pytest tests/test_pipeline.py -v
"""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import CATEGORY_ORDER, LINKEDIN_POST_MAX_CHARS
from pipeline.node1_filter import FilteredJob
from pipeline.node2_rank import _tiebreaker_key, get_top_jobs_flat, rank_and_select
from pipeline.node3_format import (
    _truncate_gracefully,
    format_job_block,
    format_post,
    validate_post,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_filtered_job(**kwargs) -> dict:
    """Create a valid filtered job dict (post-Node 1)."""
    base = {
        "original_id": "1",
        "category": "Data Engineer",
        "title": "Senior Data Engineer",
        "company": "Databricks",
        "location": "Remote",
        "salary_info": "$160,000 - $200,000",
        "remote_status": True,
        "stack_keywords": ["Spark", "Python", "Delta Lake", "SQL"],
        "notability_score": 7,
        "source_platform": "greenhouse",
    }
    base.update(kwargs)
    return base


def _make_ranked(jobs_per_cat: int = 2) -> dict[str, list[dict]]:
    """Create a full ranked dict with jobs_per_cat jobs per category."""
    categories = {
        "Data Engineer": ("Senior Data Engineer", "Staff Data Engineer"),
        "Data Analyst": ("Senior Data Analyst", "Lead BI Analyst"),
        "ML Engineer": ("ML Engineer", "Senior MLOps Engineer"),
        "Data Scientist": ("Senior Data Scientist", "Applied Scientist"),
        "AI Engineer": ("AI Engineer", "GenAI Engineer"),
    }
    ranked = {}
    for cat, titles in categories.items():
        jobs = []
        for i, title in enumerate(titles[:jobs_per_cat]):
            jobs.append(_make_filtered_job(
                category=cat,
                title=title,
                company=f"Company{i+1}",
                original_id=str(i),
            ))
        ranked[cat] = jobs
    return ranked


# ---------------------------------------------------------------------------
# Node 1: FilteredJob Pydantic validation
# ---------------------------------------------------------------------------

class TestFilteredJobValidation:
    def test_valid_job_passes(self):
        job = _make_filtered_job()
        validated = FilteredJob(**job)
        assert validated.category == "Data Engineer"

    def test_invalid_category_raises(self):
        job = _make_filtered_job(category="DevOps Engineer")
        with pytest.raises(ValidationError):
            FilteredJob(**job)

    def test_stack_keywords_capped_at_4(self):
        job = _make_filtered_job(stack_keywords=["A", "B", "C", "D", "E", "F"])
        validated = FilteredJob(**job)
        assert len(validated.stack_keywords) == 4

    def test_notability_score_clamped_low(self):
        job = _make_filtered_job(notability_score=-5)
        validated = FilteredJob(**job)
        assert validated.notability_score == 0

    def test_notability_score_clamped_high(self):
        job = _make_filtered_job(notability_score=999)
        validated = FilteredJob(**job)
        assert validated.notability_score == 10

    def test_all_five_categories_valid(self):
        for cat in CATEGORY_ORDER:
            job = _make_filtered_job(category=cat)
            validated = FilteredJob(**job)
            assert validated.category == cat

    def test_salary_info_nullable(self):
        job = _make_filtered_job(salary_info=None)
        validated = FilteredJob(**job)
        assert validated.salary_info is None


# ---------------------------------------------------------------------------
# Node 2: Ranking logic
# ---------------------------------------------------------------------------

class TestTiebreakerKey:
    def test_salary_wins_over_no_salary(self):
        with_salary = _make_filtered_job(salary_info="$100k", remote_status=False, notability_score=0)
        no_salary = _make_filtered_job(salary_info=None, remote_status=True, notability_score=10)
        assert _tiebreaker_key(with_salary) > _tiebreaker_key(no_salary)

    def test_remote_beats_score_when_salary_equal(self):
        remote = _make_filtered_job(salary_info=None, remote_status=True, notability_score=3)
        on_site = _make_filtered_job(salary_info=None, remote_status=False, notability_score=10)
        assert _tiebreaker_key(remote) > _tiebreaker_key(on_site)

    def test_score_is_final_tiebreaker(self):
        high = _make_filtered_job(salary_info=None, remote_status=False, notability_score=9)
        low = _make_filtered_job(salary_info=None, remote_status=False, notability_score=2)
        assert _tiebreaker_key(high) > _tiebreaker_key(low)


class TestRankAndSelect:
    def test_returns_all_5_category_keys(self):
        jobs = [_make_filtered_job(category=cat) for cat in CATEGORY_ORDER]
        result = rank_and_select(jobs)
        assert set(result.keys()) == set(CATEGORY_ORDER)

    def test_selects_exactly_top_2(self):
        jobs = []
        for cat in CATEGORY_ORDER:
            for i in range(5):
                jobs.append(_make_filtered_job(category=cat, original_id=f"{cat}-{i}"))
        result = rank_and_select(jobs)
        for cat in CATEGORY_ORDER:
            assert len(result[cat]) <= 2

    def test_empty_category_is_empty_list(self):
        # Only Data Engineer jobs, rest are empty
        jobs = [_make_filtered_job(category="Data Engineer")]
        result = rank_and_select(jobs)
        assert result["Data Analyst"] == []
        assert result["ML Engineer"] == []

    def test_top_salary_job_selected_first(self):
        high = _make_filtered_job(salary_info="$200k", notability_score=0)
        low = _make_filtered_job(salary_info=None, notability_score=10)
        result = rank_and_select([high, low])
        # With salary should rank first
        assert result["Data Engineer"][0]["salary_info"] == "$200k"

    def test_category_order_preserved(self):
        ranked = _make_ranked()
        flat = get_top_jobs_flat(ranked)
        # First 2 should be Data Engineer
        assert flat[0]["category"] == "Data Engineer"
        assert flat[1]["category"] == "Data Engineer"


# ---------------------------------------------------------------------------
# Node 3: Format and validate
# ---------------------------------------------------------------------------

class TestFormatJobBlock:
    def test_contains_required_elements(self):
        job = _make_filtered_job()
        block = format_job_block(job)
        assert "Databricks" in block
        assert "Senior Data Engineer" in block
        assert "📍" in block
        assert "💰" in block
        assert "🔗" in block

    def test_undisclosed_when_no_salary(self):
        job = _make_filtered_job(salary_info=None)
        block = format_job_block(job)
        assert "Undisclosed" in block

    def test_stack_line_omitted_when_empty(self):
        job = _make_filtered_job(stack_keywords=[])
        block = format_job_block(job)
        assert "Stack:" not in block

    def test_stack_max_4_technologies(self):
        job = _make_filtered_job(stack_keywords=["A", "B", "C", "D"])
        block = format_job_block(job)
        assert "Stack: A · B · C · D" in block


class TestFormatPost:
    def test_post_under_char_limit(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        assert len(post) <= LINKEDIN_POST_MAX_CHARS

    def test_post_contains_hook(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        assert "10 data roles posted in the last 24 hours" in post

    def test_post_contains_footer_hashtags(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        assert "#DataJobs" in post
        assert "#DataEngineering" in post
        assert "#AIJobs" in post

    def test_exactly_3_hashtags(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        assert post.count("#") == 3

    def test_category_headers_present(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        assert "DATA ENGINEER" in post
        assert "DATA ANALYST" in post
        assert "ML ENGINEER" in post

    def test_empty_category_skipped(self):
        ranked = _make_ranked()
        ranked["Data Scientist"] = []   # empty this category
        post = format_post(ranked)
        assert "DATA SCIENTIST" not in post


class TestTruncateGracefully:
    def test_truncation_preserves_footer(self):
        # Build an overlong post manually
        long_body = "X" * 3000
        ranked = _make_ranked()
        post = format_post(ranked)   # This will be valid; test _truncate directly
        truncated = _truncate_gracefully(post + "Y" * 500)
        assert len(truncated) <= LINKEDIN_POST_MAX_CHARS
        assert "#DataJobs" in truncated

    def test_output_within_limit(self):
        ranked = _make_ranked()
        over_limit = format_post(ranked) + ("Z" * 1000)
        truncated = _truncate_gracefully(over_limit)
        assert len(truncated) <= LINKEDIN_POST_MAX_CHARS


class TestValidatePost:
    def test_valid_post_passes(self):
        ranked = _make_ranked()
        post = format_post(ranked)
        validate_post(post)  # Should not raise

    def test_too_long_raises(self):
        ranked = _make_ranked()
        post = format_post(ranked) + "X" * 3000
        with pytest.raises(ValueError, match="exceeds limit"):
            validate_post(post)

    def test_wrong_hashtag_count_raises(self):
        ranked = _make_ranked()
        post = format_post(ranked).replace("#DataJobs", "DataJobs")
        with pytest.raises(ValueError, match="hashtag"):
            validate_post(post)

    def test_code_fence_raises(self):
        ranked = _make_ranked()
        base_post = format_post(ranked)
        # Inject a code fence mid-post without changing hashtag count
        bad_post = base_post.replace("All posted today", "```\nAll posted today")
        with pytest.raises(ValueError, match="forbidden"):
            validate_post(bad_post)
