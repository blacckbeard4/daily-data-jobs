"""
Tests for scraper modules.

Run with: pytest tests/test_scraper.py -v
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper._utils import (
    extract_salary,
    is_excluded_experience,
    matches_category_keyword,
    slug_to_name,
)
from scraper.deduplicator import (
    compute_hash,
    deduplicate,
    load_seen_jobs,
    purge_old_hashes,
    save_seen_jobs,
)
from scraper.greenhouse import _normalise_greenhouse_job
from scraper.lever import _normalise_lever_job


# ---------------------------------------------------------------------------
# _utils tests
# ---------------------------------------------------------------------------

class TestSlugToName:
    def test_simple_slug(self):
        assert slug_to_name("databricks") == "Databricks"

    def test_hyphenated_slug(self):
        result = slug_to_name("dbt-labs")
        assert result == "dbt Labs"  # override

    def test_openai_override(self):
        assert slug_to_name("openai") == "OpenAI"

    def test_generic_hyphen(self):
        result = slug_to_name("some-company")
        assert result == "Some Company"


class TestExtractSalary:
    def test_dollar_range(self):
        text = "Salary range: $120,000 - $180,000 per year"
        result = extract_salary(text)
        assert result is not None
        assert "$120,000" in result

    def test_dollar_k_format(self):
        text = "Compensation: $150K-$200K annually"
        result = extract_salary(text)
        assert result is not None

    def test_no_salary(self):
        text = "Competitive compensation and benefits"
        assert extract_salary(text) is None

    def test_none_input(self):
        assert extract_salary(None) is None

    def test_empty_string(self):
        assert extract_salary("") is None

    def test_usd_format(self):
        text = "Compensation: 140,000-180,000 USD"
        result = extract_salary(text)
        assert result is not None


class TestMatchesCategoryKeyword:
    def test_data_engineer(self):
        assert matches_category_keyword("Senior Data Engineer") is True

    def test_analytics_engineer(self):
        assert matches_category_keyword("Analytics Engineer at Stripe") is True

    def test_ml_engineer(self):
        assert matches_category_keyword("Machine Learning Engineer") is True

    def test_ai_engineer(self):
        assert matches_category_keyword("Generative AI Engineer") is True

    def test_llm_engineer(self):
        assert matches_category_keyword("LLM Engineer") is True

    def test_unrelated(self):
        assert matches_category_keyword("Software Engineer, iOS") is False

    def test_product_manager(self):
        assert matches_category_keyword("Product Manager") is False

    def test_case_insensitive(self):
        assert matches_category_keyword("DATA SCIENTIST") is True


class TestIsExcludedExperience:
    def test_junior(self):
        assert is_excluded_experience("Junior Data Engineer") is True

    def test_intern(self):
        assert is_excluded_experience("Data Engineering Intern") is True

    def test_entry_level(self):
        assert is_excluded_experience("Entry-Level Data Analyst") is True

    def test_graduate(self):
        assert is_excluded_experience("Graduate Data Scientist") is True

    def test_senior_not_excluded(self):
        assert is_excluded_experience("Senior Data Engineer") is False

    def test_principal_not_excluded(self):
        assert is_excluded_experience("Principal ML Engineer") is False

    def test_case_insensitive(self):
        assert is_excluded_experience("JUNIOR data engineer") is True


# ---------------------------------------------------------------------------
# Normalisation tests
# ---------------------------------------------------------------------------

class TestNormaliseGreenhouseJob:
    def _make_raw(self, **overrides) -> dict:
        base = {
            "id": 12345,
            "title": "Senior Data Engineer",
            "company": {"name": "Databricks"},
            "location": {"name": "Remote"},
            "absolute_url": "https://boards.greenhouse.io/databricks/jobs/12345",
            "content": "We use Apache Spark, Python, and Delta Lake. Salary: $160,000 - $200,000.",
            "updated_at": "2026-04-14T08:00:00Z",
        }
        base.update(overrides)
        return base

    def test_basic_normalisation(self):
        raw = self._make_raw()
        result = _normalise_greenhouse_job(raw, "databricks")
        assert result["original_id"] == "12345"
        assert result["title"] == "Senior Data Engineer"
        assert result["company"] == "Databricks"
        assert result["location"] == "Remote"
        assert result["source_platform"] == "greenhouse"
        assert result["apply_url"] == "https://boards.greenhouse.io/databricks/jobs/12345"

    def test_salary_extracted_from_description(self):
        raw = self._make_raw()
        result = _normalise_greenhouse_job(raw, "databricks")
        assert result["salary_info"] is not None
        assert "$160,000" in result["salary_info"]

    def test_llm_fields_initialised(self):
        raw = self._make_raw()
        result = _normalise_greenhouse_job(raw, "databricks")
        assert result["category"] is None
        assert result["remote_status"] is False
        assert result["stack_keywords"] == []
        assert result["notability_score"] == 0

    def test_slug_fallback_for_company(self):
        raw = self._make_raw(company=None)
        result = _normalise_greenhouse_job(raw, "databricks")
        assert result["company"] == "Databricks"

    def test_all_schema_keys_present(self):
        raw = self._make_raw()
        result = _normalise_greenhouse_job(raw, "databricks")
        expected_keys = {
            "original_id", "title", "company", "location", "apply_url",
            "salary_info", "description", "source_platform", "posted_at",
            "category", "remote_status", "stack_keywords", "notability_score",
        }
        assert set(result.keys()) == expected_keys


class TestNormaliseLeverJob:
    def _make_raw(self, **overrides) -> dict:
        base = {
            "id": "abc-123-lever",
            "text": "Senior ML Engineer",
            "categories": {"location": "San Francisco, CA"},
            "hostedUrl": "https://jobs.lever.co/netflix/abc-123",
            "descriptionPlain": "Build recommendation models. Python, TensorFlow. $180k-$250k.",
            "createdAt": int(
                (datetime.now(timezone.utc) - timedelta(hours=12)).timestamp() * 1000
            ),
        }
        base.update(overrides)
        return base

    def test_basic_normalisation(self):
        raw = self._make_raw()
        result = _normalise_lever_job(raw, "netflix")
        assert result["original_id"] == "abc-123-lever"
        assert result["title"] == "Senior ML Engineer"
        assert result["location"] == "San Francisco, CA"
        assert result["source_platform"] == "lever"

    def test_millisecond_epoch_conversion(self):
        raw = self._make_raw()
        result = _normalise_lever_job(raw, "netflix")
        assert result["posted_at"] != ""
        # Should be a valid ISO string
        datetime.fromisoformat(result["posted_at"])

    def test_salary_extracted(self):
        raw = self._make_raw()
        result = _normalise_lever_job(raw, "netflix")
        assert result["salary_info"] is not None

    def test_all_schema_keys_present(self):
        raw = self._make_raw()
        result = _normalise_lever_job(raw, "netflix")
        expected_keys = {
            "original_id", "title", "company", "location", "apply_url",
            "salary_info", "description", "source_platform", "posted_at",
            "category", "remote_status", "stack_keywords", "notability_score",
        }
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Deduplicator tests
# ---------------------------------------------------------------------------

def _make_job(company="Acme", title="Data Engineer", location="Remote") -> dict:
    return {
        "company": company, "title": title, "location": location,
        "original_id": "1", "apply_url": "", "salary_info": None,
        "description": "", "source_platform": "greenhouse", "posted_at": "",
        "category": None, "remote_status": False, "stack_keywords": [],
        "notability_score": 0,
    }


class TestComputeHash:
    def test_deterministic(self):
        job = _make_job()
        assert compute_hash(job) == compute_hash(job)

    def test_case_insensitive(self):
        j1 = _make_job(company="Acme", title="Data Engineer")
        j2 = _make_job(company="acme", title="data engineer")
        assert compute_hash(j1) == compute_hash(j2)

    def test_different_jobs_different_hashes(self):
        j1 = _make_job(company="Acme")
        j2 = _make_job(company="BetterCo")
        assert compute_hash(j1) != compute_hash(j2)


class TestPurgeOldHashes:
    def test_keeps_recent(self):
        now = datetime.now(timezone.utc)
        seen = {
            "abc": (now - timedelta(days=3)).isoformat(),
            "def": (now - timedelta(days=1)).isoformat(),
        }
        result = purge_old_hashes(seen)
        assert len(result) == 2

    def test_removes_old(self):
        now = datetime.now(timezone.utc)
        seen = {
            "old": (now - timedelta(days=8)).isoformat(),
            "new": (now - timedelta(days=1)).isoformat(),
        }
        result = purge_old_hashes(seen)
        assert "old" not in result
        assert "new" in result

    def test_does_not_mutate_input(self):
        now = datetime.now(timezone.utc)
        seen = {"old": (now - timedelta(days=10)).isoformat()}
        original_len = len(seen)
        purge_old_hashes(seen)
        assert len(seen) == original_len


class TestDeduplicate:
    def test_removes_seen_jobs(self, tmp_path):
        job1 = _make_job(company="Acme")
        job2 = _make_job(company="BetterCo")

        seen_path = str(tmp_path / "seen_jobs.json")
        existing_hash = compute_hash(job1)
        save_seen_jobs(
            {existing_hash: datetime.now(timezone.utc).isoformat()},
            seen_path,
        )

        with patch("scraper.deduplicator.SEEN_JOBS_PATH", seen_path):
            result = deduplicate([job1, job2])

        assert len(result) == 1
        assert result[0]["company"] == "BetterCo"

    def test_saves_new_hashes(self, tmp_path):
        job = _make_job(company="NewCo")
        seen_path = str(tmp_path / "seen_jobs.json")

        with patch("scraper.deduplicator.SEEN_JOBS_PATH", seen_path):
            deduplicate([job])
            seen = load_seen_jobs(seen_path)

        assert compute_hash(job) in seen

    def test_returns_all_on_first_run(self, tmp_path):
        jobs = [_make_job(company=f"Company{i}") for i in range(5)]
        seen_path = str(tmp_path / "seen_jobs.json")

        with patch("scraper.deduplicator.SEEN_JOBS_PATH", seen_path):
            result = deduplicate(jobs)

        assert len(result) == 5
