"""CLI tests for Flarecrawl."""

import json

from typer.testing import CliRunner

from flarecrawl.cli import app

runner = CliRunner()


class TestHelp:
    """Test help and version output."""

    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scrape" in result.output
        assert "crawl" in result.output
        assert "map" in result.output
        assert "download" in result.output
        assert "extract" in result.output
        assert "screenshot" in result.output
        assert "pdf" in result.output
        assert "auth" in result.output

    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "flarecrawl 0.1.0" in result.output

    def test_status_flag(self):
        result = runner.invoke(app, ["--status"])
        assert result.exit_code == 0
        assert "flarecrawl" in result.output

    def test_scrape_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output
        assert "--json" in result.output
        assert "--output" in result.output
        assert "--fields" in result.output
        assert "--timeout" in result.output
        assert "--input" in result.output
        assert "--batch" in result.output
        assert "--workers" in result.output

    def test_crawl_help(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert result.exit_code == 0
        assert "--wait" in result.output
        assert "--limit" in result.output
        assert "--progress" in result.output
        assert "--ndjson" in result.output
        assert "--fields" in result.output

    def test_map_help(self):
        result = runner.invoke(app, ["map", "--help"])
        assert result.exit_code == 0
        assert "--include-subdomains" in result.output

    def test_download_help(self):
        result = runner.invoke(app, ["download", "--help"])
        assert result.exit_code == 0
        assert ".flarecrawl/" in result.output

    def test_extract_help(self):
        result = runner.invoke(app, ["extract", "--help"])
        assert result.exit_code == 0
        assert "--urls" in result.output
        assert "--schema" in result.output
        assert "--batch" in result.output
        assert "--workers" in result.output

    def test_screenshot_help(self):
        result = runner.invoke(app, ["screenshot", "--help"])
        assert result.exit_code == 0
        assert "--full-page" in result.output
        assert "--timeout" in result.output

    def test_pdf_help(self):
        result = runner.invoke(app, ["pdf", "--help"])
        assert result.exit_code == 0
        assert "--landscape" in result.output
        assert "--timeout" in result.output


class TestAuth:
    """Test auth commands."""

    def test_auth_status_no_creds(self, no_credentials):
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["authenticated"] is False
        assert "account_id" in data["data"]["missing"]

    def test_auth_status_with_creds(self, mock_credentials):
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["authenticated"] is True
        assert data["data"]["source"] == "environment"

    def test_auth_logout(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.config.get_config_dir", lambda: tmp_path)
        result = runner.invoke(app, ["auth", "logout"])
        assert result.exit_code == 0


class TestAuthRequired:
    """Test that commands require auth."""

    def test_scrape_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["scrape", "https://example.com", "--json"])
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["error"]["code"] == "AUTH_REQUIRED"

    def test_crawl_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["crawl", "https://example.com", "--json"])
        assert result.exit_code == 2

    def test_map_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["map", "https://example.com", "--json"])
        assert result.exit_code == 2

    def test_screenshot_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["screenshot", "https://example.com", "--json"])
        assert result.exit_code == 2

    def test_pdf_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["pdf", "https://example.com", "--json"])
        assert result.exit_code == 2

    def test_extract_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["extract", "test prompt", "--urls", "https://example.com", "--json"])
        assert result.exit_code == 2


class TestValidation:
    """Test input validation."""

    def test_scrape_invalid_url(self, mock_credentials):
        result = runner.invoke(app, ["scrape", "not-a-url", "--json"])
        assert result.exit_code == 4
        data = json.loads(result.output)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    def test_crawl_invalid_url(self, mock_credentials):
        # "not-a-url" is treated as a job ID (non-http), so test with a bad http URL
        result = runner.invoke(app, ["crawl", "http://", "--json"])
        assert result.exit_code == 4

    def test_map_invalid_url(self, mock_credentials):
        result = runner.invoke(app, ["map", "not-a-url", "--json"])
        assert result.exit_code == 4

    def test_extract_no_urls(self, mock_credentials):
        result = runner.invoke(app, ["extract", "test prompt", "--json"])
        assert result.exit_code == 4


class TestHelpers:
    """Test helper functions."""

    def test_filter_fields_list(self):
        from flarecrawl.cli import _filter_fields
        data = [{"id": 1, "name": "a", "extra": "x"}, {"id": 2, "name": "b", "extra": "y"}]
        result = _filter_fields(data, "id,name")
        assert result == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]

    def test_filter_fields_dict(self):
        from flarecrawl.cli import _filter_fields
        data = {"id": 1, "name": "a", "extra": "x"}
        result = _filter_fields(data, "id,name")
        assert result == {"id": 1, "name": "a"}

    def test_filter_fields_none(self):
        from flarecrawl.cli import _filter_fields
        data = {"id": 1, "name": "a"}
        result = _filter_fields(data, None)
        assert result == data

    def test_sanitize_filename(self):
        from flarecrawl.cli import _sanitize_filename
        assert _sanitize_filename("https://example.com/docs/api") == "docs-api"
        assert _sanitize_filename("https://example.com/") == "index"
        assert _sanitize_filename("https://example.com/page.html") == "page.html"

    def test_input_file_scrape(self, mock_credentials, tmp_path):
        """Test --input flag reads URLs from file (auth will fail but validates file reading)."""
        url_file = tmp_path / "urls.txt"
        url_file.write_text("https://example.com\n# comment\nhttps://test.com\n")
        result = runner.invoke(app, ["scrape", "--input", str(url_file), "--json"])
        # Will fail on API call, but should get past URL loading
        assert result.exit_code != 4  # Not a validation error


class TestBatch:
    """Test batch mode flags and validation."""

    def test_scrape_batch_file_not_found(self, mock_credentials):
        result = runner.invoke(app, ["scrape", "--batch", "nonexistent.txt"])
        assert result.exit_code != 0

    def test_scrape_batch_and_input_conflict(self, mock_credentials, tmp_path):
        """Cannot use both --batch and --input."""
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com\n")
        result = runner.invoke(app, [
            "scrape", "--batch", str(f), "--input", str(f),
        ])
        assert result.exit_code == 4

    def test_scrape_batch_no_urls(self, mock_credentials, tmp_path):
        """Batch file with no URLs should error."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = runner.invoke(app, ["scrape", "--batch", str(f)])
        assert result.exit_code == 4

    def test_scrape_batch_reads_urls(self, mock_credentials, tmp_path):
        """Batch mode reads URLs from file (will fail on API but passes validation)."""
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com\nhttps://test.com\n")
        result = runner.invoke(app, ["scrape", "--batch", str(f)])
        # Should get past validation (exit code != 4)
        assert result.exit_code != 4

    def test_extract_batch_reads_urls(self, mock_credentials, tmp_path):
        """Extract batch mode reads URLs from file."""
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com\nhttps://test.com\n")
        result = runner.invoke(app, ["extract", "Get title", "--batch", str(f)])
        # Should get past validation (exit code != 4)
        assert result.exit_code != 4

    def test_extract_no_urls_no_batch(self, mock_credentials):
        """Extract with no --urls and no --batch should error."""
        result = runner.invoke(app, ["extract", "Get title", "--json"])
        assert result.exit_code == 4
