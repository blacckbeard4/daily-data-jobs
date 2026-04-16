from __future__ import annotations

"""
Tests for the LinkedIn publisher modules.

Run with: pytest tests/test_publisher.py -v

No actual LinkedIn API calls are made — requests.post is fully mocked.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from publisher.linkedin_post import build_ugcposts_payload, post_to_linkedin


_SAMPLE_POST = """\
10 data roles posted in the last 24 hours.
Salary included. Real companies hiring today.

⚙️ DATA ENGINEER
Databricks — Senior Data Engineer
📍 Remote | 💰 $160,000 - $200,000
Stack: Spark · Delta Lake · Python · SQL
🔗 https://boards.greenhouse.io/databricks/jobs/1

Snowflake — Staff Data Engineer
📍 San Francisco, CA | 💰 Undisclosed
Stack: Snowflake · dbt · Airflow
🔗 https://boards.greenhouse.io/snowflake/jobs/2

All posted today. Updated every morning at 8AM ET.
👇 Which company do you want in tomorrow's list?
#DataJobs #DataEngineering #AIJobs"""

_PERSON_URN = "urn:li:person:testuser123"


# ---------------------------------------------------------------------------
# build_ugcposts_payload
# ---------------------------------------------------------------------------

class TestBuildUgcPostsPayload:
    def test_correct_structure(self):
        payload = build_ugcposts_payload(_SAMPLE_POST, _PERSON_URN)
        assert payload["author"] == _PERSON_URN
        assert payload["lifecycleState"] == "PUBLISHED"
        assert (
            "com.linkedin.ugc.ShareContent"
            in payload["specificContent"]
        )

    def test_post_text_in_payload(self):
        payload = build_ugcposts_payload(_SAMPLE_POST, _PERSON_URN)
        content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
        assert content["shareCommentary"]["text"] == _SAMPLE_POST

    def test_share_media_category_none(self):
        payload = build_ugcposts_payload(_SAMPLE_POST, _PERSON_URN)
        content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
        assert content["shareMediaCategory"] == "NONE"

    def test_visibility_public(self):
        payload = build_ugcposts_payload(_SAMPLE_POST, _PERSON_URN)
        vis = payload["visibility"]
        assert "com.linkedin.ugc.MemberNetworkVisibility" in vis
        assert vis["com.linkedin.ugc.MemberNetworkVisibility"] == "PUBLIC"

    def test_over_limit_raises(self):
        long_text = "X" * 3000
        with pytest.raises(ValueError, match="exceeds limit"):
            build_ugcposts_payload(long_text, _PERSON_URN)

    def test_exact_limit_passes(self):
        text = "X" * 2800
        # Should not raise
        payload = build_ugcposts_payload(text, _PERSON_URN)
        assert payload["author"] == _PERSON_URN


# ---------------------------------------------------------------------------
# post_to_linkedin — dry run
# ---------------------------------------------------------------------------

class TestPostToLinkedinDryRun:
    def test_dry_run_returns_correct_dict(self):
        with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
            result = post_to_linkedin(_SAMPLE_POST, dry_run=True)
        assert result["dry_run"] is True
        assert result["char_count"] == len(_SAMPLE_POST)
        assert "payload" in result

    def test_dry_run_makes_no_http_call(self):
        with patch("publisher.linkedin_post.requests.post") as mock_post:
            with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                post_to_linkedin(_SAMPLE_POST, dry_run=True)
        mock_post.assert_not_called()

    def test_dry_run_does_not_call_auth(self):
        with patch("publisher.linkedin_post.get_valid_access_token") as mock_auth:
            with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                post_to_linkedin(_SAMPLE_POST, dry_run=True)
        mock_auth.assert_not_called()


# ---------------------------------------------------------------------------
# post_to_linkedin — live mode
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    """Create a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.headers = {"X-RestLi-Id": "urn:li:ugcPost:9999"}
    if json_body is not None:
        mock.json.return_value = json_body
    else:
        mock.json.side_effect = Exception("no body")
    # Raise for 4xx and 5xx if raise_for_status called
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
        mock.text = f"Error {status_code}"
    else:
        mock.raise_for_status.return_value = None
    return mock


class TestPostToLinkedinLive:
    def test_201_success_returns_urn(self):
        mock_resp = _make_mock_response(201, {"id": "urn:li:ugcPost:9999"})
        with patch("publisher.linkedin_post.get_valid_access_token", return_value="tok"):
            with patch("publisher.linkedin_post.requests.post", return_value=mock_resp):
                with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                    result = post_to_linkedin(_SAMPLE_POST, dry_run=False)
        assert result is not None

    def test_retry_on_503(self):
        """Should retry on 5xx and succeed on 3rd attempt."""
        fail_resp = _make_mock_response(503)
        success_resp = _make_mock_response(201, {"id": "urn:li:ugcPost:9999"})

        with patch("publisher.linkedin_post.get_valid_access_token", return_value="tok"):
            with patch(
                "publisher.linkedin_post.requests.post",
                side_effect=[fail_resp, fail_resp, success_resp],
            ):
                with patch("publisher.linkedin_post.time.sleep"):  # skip actual sleep
                    with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                        result = post_to_linkedin(_SAMPLE_POST, dry_run=False)
        assert result is not None

    def test_no_retry_on_401(self):
        """4xx errors should not be retried."""
        fail_resp = _make_mock_response(401)
        with patch("publisher.linkedin_post.get_valid_access_token", return_value="tok"):
            with patch("publisher.linkedin_post.requests.post", return_value=fail_resp):
                with patch("publisher.linkedin_post.send_alert"):
                    with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                        with pytest.raises(requests.HTTPError):
                            post_to_linkedin(_SAMPLE_POST, dry_run=False)

    def test_required_headers_sent(self):
        mock_resp = _make_mock_response(201, {"id": "urn:li:ugcPost:9999"})
        with patch("publisher.linkedin_post.get_valid_access_token", return_value="mytoken"):
            with patch("publisher.linkedin_post.requests.post", return_value=mock_resp) as mock_post:
                with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                    post_to_linkedin(_SAMPLE_POST, dry_run=False)

        call_kwargs = mock_post.call_args
        headers = call_kwargs[1].get("headers") or call_kwargs[0][1] if call_kwargs[0] else {}
        headers = call_kwargs.kwargs.get("headers", call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert headers.get("X-Restli-Protocol-Version") == "2.0.0"
        assert "Bearer mytoken" in headers.get("Authorization", "")

    def test_missing_person_urn_raises(self):
        env = {k: v for k, v in os.environ.items() if k != "LINKEDIN_PERSON_URN"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="LINKEDIN_PERSON_URN"):
                post_to_linkedin(_SAMPLE_POST, dry_run=False)

    def test_alert_sent_on_all_retries_exhausted(self):
        fail_resp = _make_mock_response(503)
        with patch("publisher.linkedin_post.get_valid_access_token", return_value="tok"):
            with patch(
                "publisher.linkedin_post.requests.post",
                return_value=fail_resp,
            ):
                with patch("publisher.linkedin_post.time.sleep"):
                    with patch("publisher.linkedin_post.send_alert") as mock_alert:
                        with patch.dict(os.environ, {"LINKEDIN_PERSON_URN": _PERSON_URN}):
                            with pytest.raises(RuntimeError):
                                post_to_linkedin(_SAMPLE_POST, dry_run=False)
        mock_alert.assert_called()
