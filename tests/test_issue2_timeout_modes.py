"""Issue #2 — exhaustive coverage of the crawl-timeout recovery path.

`tests/test_issue_regressions.py::TestCrawlTimeoutRecovery` proves the default
`-o out.json` mode flushes completed records on timeout. This module exercises
the *other* output modes and edge cases that share the same recovery code in
`src/flarecrawl/cli/crawl.py` (the `except FlareCrawlError` block at ~L210 that
falls through to the fetch + write path):

  * --ndjson stream mode (records reach stdout after timeout)
  * --json stdout mode, no -o (envelope is {data, meta} with status "timeout")
  * zero completed records (valid empty-records file, status "timeout", no crash)
  * --webhook (fires with the partial results)
  * --fields filtering (applies to the recovered partial records)
  * crawl_status() raising during recovery (tolerated → final_status = {})
  * the recovered status is always "timeout" even if CF still reports "running"
"""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from flarecrawl.cli import app
from flarecrawl.client import FlareCrawlError

runner = CliRunner()


def _timeout_wait(self, job_id, **kwargs):
    raise FlareCrawlError("Crawl timed out after 600s", "TIMEOUT")


def _start(job_id="job-x"):
    def fake_start(self, url, **kwargs):
        return job_id
    return fake_start


class TestTimeoutNdjsonMode:
    """--ndjson on timeout must still stream the completed records to stdout."""

    def test_ndjson_streams_partial_records_on_timeout(self, mock_credentials):
        records = [
            {"url": "https://a.test", "markdown": "A", "status": "completed"},
            {"url": "https://b.test", "markdown": "B", "status": "completed"},
        ]

        def fake_status(self, job_id):
            return {"status": "running", "finished": 2, "total": 5}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-nd")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "--ndjson", "--limit", "5",
            ])

        assert result.exit_code == 0, result.output
        # stdout = one JSON object per line; stderr carried the warning/banner.
        lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
        assert len(lines) == 2, f"expected 2 streamed records, got: {result.stdout!r}"
        urls = {json.loads(ln)["url"] for ln in lines}
        assert urls == {"https://a.test", "https://b.test"}

    def test_ndjson_fields_filter_applies_on_timeout(self, mock_credentials):
        records = [
            {"url": "https://a.test", "markdown": "A", "html": "<a>", "status": "completed"},
        ]

        def fake_status(self, job_id):
            return {"status": "running"}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-ndf")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "--ndjson",
                "--fields", "url,markdown",
            ])

        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert set(rec.keys()) == {"url", "markdown"}, rec


class TestTimeoutJsonStdoutMode:
    """--json without -o must emit the recovered result as a {data, meta} envelope."""

    def test_json_stdout_emits_envelope_on_timeout(self, mock_credentials):
        records = [{"url": "https://a.test", "markdown": "A", "status": "completed"}]

        def fake_status(self, job_id):
            return {"status": "running", "total": 3}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-js")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "--json", "--limit", "3",
            ])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "data" in payload and "meta" in payload
        assert payload["data"]["status"] == "timeout"
        assert payload["data"]["job_id"] == "job-js"
        assert len(payload["data"]["records"]) == 1
        assert payload["meta"]["count"] == 1


class TestTimeoutZeroRecords:
    """Timeout before ANY page completes must write a valid empty file, not crash."""

    def test_zero_records_writes_valid_timeout_file(self, mock_credentials, tmp_path):
        out = tmp_path / "empty.json"

        def fake_status(self, job_id):
            return {"status": "running", "finished": 0, "total": 10}

        def fake_get_all(self, job_id, status=None):
            return iter(())  # nothing completed yet

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-zero")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "--limit", "10", "-o", str(out),
            ])

        assert result.exit_code == 0, result.output
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "timeout"
        assert data["records"] == []


class TestTimeoutWebhook:
    """--webhook must fire with the partial results recovered on timeout."""

    def test_webhook_fires_with_partial_results(self, mock_credentials, tmp_path):
        out = tmp_path / "wh.json"
        records = [
            {"url": "https://a.test", "markdown": "A", "status": "completed"},
            {"url": "https://b.test", "markdown": "B", "status": "completed"},
        ]
        captured = {}

        class _Resp:
            status_code = 200

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return _Resp()

        def fake_status(self, job_id):
            return {"status": "running", "total": 4}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-wh")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all), \
             patch("httpx.post", new=fake_post):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "--limit", "4",
                "-o", str(out), "--webhook", "https://hook.test/cb",
            ])

        assert result.exit_code == 0, result.output
        assert captured["url"] == "https://hook.test/cb"
        body = captured["payload"]
        assert body["data"]["status"] == "timeout"
        assert len(body["data"]["records"]) == 2
        assert body["meta"]["count"] == 2


class TestTimeoutFieldsFilter:
    """--fields must trim the recovered partial records in -o / default mode too."""

    def test_fields_filter_applies_to_partial_records(self, mock_credentials, tmp_path):
        out = tmp_path / "fields.json"
        records = [
            {"url": "https://a.test", "markdown": "A", "html": "<a>", "links": [], "status": "completed"},
        ]

        def fake_status(self, job_id):
            return {"status": "running"}

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-f")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait",
                "-o", str(out), "--fields", "url,markdown",
            ])

        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "timeout"
        assert len(data["records"]) == 1
        assert set(data["records"][0].keys()) == {"url", "markdown"}


class TestTimeoutStatusRecoveryTolerated:
    """crawl_status() raising during recovery must NOT abort — fix uses try/except."""

    def test_status_raising_is_tolerated(self, mock_credentials, tmp_path):
        out = tmp_path / "tol.json"
        records = [{"url": "https://a.test", "markdown": "A", "status": "completed"}]

        def fake_status(self, job_id):
            raise FlareCrawlError("status unavailable", "ERROR")

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-tol")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "-o", str(out),
            ])

        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text(encoding="utf-8"))
        # final_status fell back to {} then got status="timeout"; records still flushed.
        assert data["status"] == "timeout"
        assert len(data["records"]) == 1

    def test_recovered_status_is_timeout_even_if_cf_reports_running(self, mock_credentials, tmp_path):
        """The fix forces status='timeout' regardless of CF's last-polled state."""
        out = tmp_path / "ovr.json"
        records = [{"url": "https://a.test", "markdown": "A", "status": "completed"}]

        def fake_status(self, job_id):
            return {"status": "running", "total": 99}  # CF still thinks it's running

        def fake_get_all(self, job_id, status=None):
            yield from records

        with patch("flarecrawl.client.Client.crawl_start", new=_start("job-ovr")), \
             patch("flarecrawl.client.Client.crawl_wait", new=_timeout_wait), \
             patch("flarecrawl.client.Client.crawl_status", new=fake_status), \
             patch("flarecrawl.client.Client.crawl_get_all", new=fake_get_all):
            result = runner.invoke(app, [
                "crawl", "https://example.com", "--wait", "-o", str(out),
            ])

        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "timeout"  # NOT "running"
        assert data["total"] == 99  # CF total preserved from the recovered status
