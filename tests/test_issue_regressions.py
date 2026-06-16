"""Regression tests for reported GitHub issues.

- Issue #2: `crawl --wait -o out.json` must flush completed records on timeout.
- Issue #3: CDP commands must report a missing-`websockets` dependency clearly,
  not a misleading "Not authenticated", and must do so before the auth check.
"""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from flarecrawl.cli import app
from flarecrawl.client import FlareCrawlError

runner = CliRunner()


class TestCrawlTimeoutRecovery:
    """Issue #2 — a crawl that times out mid-job must still save what completed."""

    def test_timeout_saves_partial_records_to_output(self, mock_credentials, tmp_path):
        out = tmp_path / "out.json"
        records = [
            {"url": "https://a.test", "markdown": "A", "status": "completed"},
            {"url": "https://b.test", "markdown": "B", "status": "completed"},
        ]

        def fake_start(self, url, **kwargs):
            return "job-timeout-1"

        def fake_wait(self, job_id, **kwargs):
            raise FlareCrawlError("Crawl timed out after 600s", "TIMEOUT")

        def fake_status(self, job_id):
            return {"status": "running", "finished": 2, "total": 3}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=fake_start), \
             patch("flarecrawl.client.Client.crawl_wait", new=fake_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com",
                "--wait", "--limit", "3",
                "-o", str(out),
            ])

        assert result.exit_code == 0, result.output
        assert out.exists(), "output file should be written even on timeout"
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "timeout"
        assert len(data["records"]) == 2
        assert {r["url"] for r in data["records"]} == {"https://a.test", "https://b.test"}

    def test_non_timeout_error_still_aborts(self, mock_credentials, tmp_path):
        """A genuine error (not TIMEOUT) must NOT silently write a partial file."""
        out = tmp_path / "out.json"

        def fake_start(self, url, **kwargs):
            return "job-err-1"

        def fake_wait(self, job_id, **kwargs):
            raise FlareCrawlError("boom", "ERROR")

        with patch("flarecrawl.client.Client.crawl_start", new=fake_start), \
             patch("flarecrawl.client.Client.crawl_wait", new=fake_wait):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "-o", str(out),
            ])

        assert result.exit_code != 0
        assert not out.exists(), "non-timeout errors should not produce an output file"


class TestCdpWebsocketsGuard:
    """Issue #3 — missing `websockets` must surface as a dependency error, early."""

    def test_missing_websockets_reports_dependency_not_auth(self, no_credentials):
        # websockets absent AND no creds: the old code reported "Not authenticated"
        # because the auth check ran first. The guard must now fire before auth.
        with patch("flarecrawl.cdp.websockets", None):
            result = runner.invoke(app, ["design", "extract", "https://example.com"])

        assert result.exit_code == 1, result.output  # EXIT_ERROR, not EXIT_AUTH_REQUIRED (2)
        assert "websockets" in result.output.lower()
        assert "not authenticated" not in result.output.lower()

    def test_missing_websockets_guarded_even_with_creds(self, mock_credentials):
        with patch("flarecrawl.cdp.websockets", None):
            result = runner.invoke(app, ["design", "extract", "https://example.com"])

        assert result.exit_code == 1, result.output
        assert "websockets" in result.output.lower()
