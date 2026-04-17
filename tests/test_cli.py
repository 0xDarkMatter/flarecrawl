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
        assert "flarecrawl 0.16.0" in result.output

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

    def test_favicon_help(self):
        result = runner.invoke(app, ["favicon", "--help"])
        assert result.exit_code == 0
        assert "--all" in result.output
        assert "--json" in result.output

    def test_scrape_has_only_main_content(self):
        """--only-main-content extracts main article content via BeautifulSoup."""
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--only-main-content" in result.output

    def test_scrape_has_js_flag(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--js" in result.output

    def test_scrape_has_no_cache_flag(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--no-cache" in result.output

    def test_scrape_has_wait_until_flag(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--wait-until" in result.output


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

    def test_favicon_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["favicon", "https://example.com", "--json"])
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


class TestFavicon:
    """Test favicon extraction helper."""

    def test_extract_favicons_basic(self):
        from flarecrawl.cli import _extract_favicons
        html = '<html><head><link rel="icon" href="/favicon.ico"></head></html>'
        result = _extract_favicons(html, "https://example.com")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/favicon.ico"
        assert result[0]["rel"] == "icon"

    def test_extract_favicons_multiple(self):
        from flarecrawl.cli import _extract_favicons
        html = """<html><head>
        <link rel="icon" href="/favicon.ico">
        <link rel="icon" href="/icon-32.png" sizes="32x32">
        <link rel="apple-touch-icon" href="/apple-180.png" sizes="180x180">
        </head></html>"""
        result = _extract_favicons(html, "https://example.com")
        assert len(result) == 3
        # Largest first
        assert result[0]["url"] == "https://example.com/apple-180.png"
        assert result[0]["sizes"] == "180x180"

    def test_extract_favicons_relative_urls(self):
        from flarecrawl.cli import _extract_favicons
        html = '<link rel="icon" href="assets/icon.png">'
        result = _extract_favicons(html, "https://example.com/page/")
        assert result[0]["url"] == "https://example.com/page/assets/icon.png"

    def test_extract_favicons_absolute_urls(self):
        from flarecrawl.cli import _extract_favicons
        html = '<link rel="icon" href="https://cdn.example.com/icon.png">'
        result = _extract_favicons(html, "https://example.com")
        assert result[0]["url"] == "https://cdn.example.com/icon.png"

    def test_extract_favicons_ignores_non_icon_links(self):
        from flarecrawl.cli import _extract_favicons
        html = """<head>
        <link rel="stylesheet" href="/style.css">
        <link rel="canonical" href="https://example.com">
        <link rel="icon" href="/favicon.ico">
        </head>"""
        result = _extract_favicons(html, "https://example.com")
        assert len(result) == 1
        assert result[0]["rel"] == "icon"

    def test_extract_favicons_empty_html(self):
        from flarecrawl.cli import _extract_favicons
        result = _extract_favicons("<html><head></head></html>", "https://example.com")
        assert result == []


class TestParseAuth:
    """Test HTTP Basic Auth parsing — dual authenticate + setExtraHTTPHeaders."""

    def test_parse_auth_valid(self):
        import base64
        from flarecrawl.cli import _parse_auth
        result = _parse_auth("admin:secret")
        assert result["authenticate"] == {"username": "admin", "password": "secret"}
        expected_b64 = base64.b64encode(b"admin:secret").decode()
        assert result["extra_headers"] == {"Authorization": f"Basic {expected_b64}"}

    def test_parse_auth_password_with_colon(self):
        import base64
        from flarecrawl.cli import _parse_auth
        result = _parse_auth("user:pass:with:colons")
        assert result["authenticate"] == {"username": "user", "password": "pass:with:colons"}
        expected_b64 = base64.b64encode(b"user:pass:with:colons").decode()
        assert result["extra_headers"] == {"Authorization": f"Basic {expected_b64}"}

    def test_parse_auth_none(self):
        from flarecrawl.cli import _parse_auth
        assert _parse_auth(None) is None

    def test_parse_auth_invalid_no_colon(self):
        from flarecrawl.cli import _parse_auth
        import typer
        import pytest
        with pytest.raises(typer.Exit):
            _parse_auth("no-colon-here")

    def test_auth_flag_in_all_commands(self):
        """Verify --auth flag appears in help for all data commands."""
        commands = ["scrape", "crawl", "map", "download", "extract",
                    "screenshot", "pdf", "favicon"]
        for cmd in commands:
            result = runner.invoke(app, [cmd, "--help"])
            assert "--auth" in result.output, f"--auth missing from {cmd} help"


class TestAuthBodyBuilder:
    """Test that auth flows through _build_body with both mechanisms."""

    def test_extra_headers_in_body(self):
        from flarecrawl.client import Client
        headers = {"Authorization": "Basic YWRtaW46c2VjcmV0"}
        body = Client._build_body(
            url="https://example.com",
            extra_headers=headers,
        )
        assert body["setExtraHTTPHeaders"] == {"Authorization": "Basic YWRtaW46c2VjcmV0"}
        assert body["url"] == "https://example.com"

    def test_authenticate_in_body(self):
        from flarecrawl.client import Client
        body = Client._build_body(
            url="https://example.com",
            authenticate={"username": "admin", "password": "secret"},
        )
        assert body["authenticate"] == {"username": "admin", "password": "secret"}

    def test_both_auth_mechanisms(self):
        """Both authenticate and setExtraHTTPHeaders can coexist."""
        from flarecrawl.client import Client
        body = Client._build_body(
            url="https://example.com",
            authenticate={"username": "admin", "password": "secret"},
            extra_headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
        )
        assert body["authenticate"] == {"username": "admin", "password": "secret"}
        assert body["setExtraHTTPHeaders"] == {"Authorization": "Basic YWRtaW46c2VjcmV0"}


class TestParseHeaders:
    """Test custom HTTP headers parsing."""

    def test_parse_key_value(self):
        from flarecrawl.cli import _parse_headers
        result = _parse_headers(["Accept-Language: en-US"])
        assert result == {"Accept-Language": "en-US"}

    def test_parse_multiple(self):
        from flarecrawl.cli import _parse_headers
        result = _parse_headers(["X-Custom: foo", "Accept: text/html"])
        assert result == {"X-Custom": "foo", "Accept": "text/html"}

    def test_parse_json(self):
        from flarecrawl.cli import _parse_headers
        result = _parse_headers(['{"X-Api-Key": "abc123", "Accept": "application/json"}'])
        assert result == {"X-Api-Key": "abc123", "Accept": "application/json"}

    def test_parse_none(self):
        from flarecrawl.cli import _parse_headers
        assert _parse_headers(None) is None

    def test_parse_empty_list(self):
        from flarecrawl.cli import _parse_headers
        assert _parse_headers([]) is None

    def test_invalid_no_colon(self):
        from flarecrawl.cli import _parse_headers
        import typer
        import pytest
        with pytest.raises(typer.Exit):
            _parse_headers(["no-colon-here"])

    def test_invalid_json(self):
        from flarecrawl.cli import _parse_headers
        import typer
        import pytest
        with pytest.raises(typer.Exit):
            _parse_headers(["{bad json}"])

    def test_headers_flag_in_all_commands(self):
        commands = ["scrape", "crawl", "map", "download", "extract",
                    "screenshot", "pdf", "favicon"]
        for cmd in commands:
            result = runner.invoke(app, [cmd, "--help"])
            assert "--headers" in result.output, f"--headers missing from {cmd} help"


class TestNewScrapeFlags:
    """Test new v0.5.0 scrape flags."""

    def test_scrape_has_include_tags(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--include-tags" in result.output

    def test_scrape_has_exclude_tags(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--exclude-tags" in result.output

    def test_scrape_has_diff(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--diff" in result.output

    def test_include_exclude_mutually_exclusive(self):
        """--include-tags and --exclude-tags cannot be used together."""
        result = runner.invoke(app, [
            "scrape", "https://example.com",
            "--include-tags", "main",
            "--exclude-tags", "nav",
            "--json",
        ])
        assert result.exit_code == 4
        assert "Cannot use both" in result.output or "Cannot use both" in (result.stderr or "")

    def test_scrape_format_images_in_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "images" in result.output

    def test_scrape_format_summary_in_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "summary" in result.output

    def test_scrape_format_schema_in_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "schema" in result.output


class TestSchemaCommand:
    """Test the schema command."""

    def test_schema_help(self):
        result = runner.invoke(app, ["schema", "--help"])
        assert result.exit_code == 0
        assert "--type" in result.output
        assert "ld-json" in result.output

    def test_schema_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["schema", "https://example.com", "--json"])
        assert result.exit_code == 2


class TestCrawlContentFiltering:
    """Test content filtering flags on crawl command."""

    def test_crawl_has_only_main_content(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--only-main-content" in result.output

    def test_crawl_has_exclude_tags(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--exclude-tags" in result.output

    def test_crawl_has_include_tags(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--include-tags" in result.output


class TestDownloadContentFiltering:
    """Test content filtering flags on download command."""

    def test_download_has_only_main_content(self):
        result = runner.invoke(app, ["download", "--help"])
        assert "--only-main-content" in result.output

    def test_download_has_exclude_tags(self):
        result = runner.invoke(app, ["download", "--help"])
        assert "--exclude-tags" in result.output

    def test_download_has_include_tags(self):
        result = runner.invoke(app, ["download", "--help"])
        assert "--include-tags" in result.output


class TestWebhook:
    """Test webhook flag on crawl command."""

    def test_crawl_has_webhook(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--webhook" in result.output

    def test_crawl_has_webhook_headers(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--webhook-headers" in result.output


class TestFilterRecordContent:
    """Test _filter_record_content helper."""

    def test_no_filters_returns_unchanged(self):
        from flarecrawl.cli import _filter_record_content
        record = {"url": "https://example.com", "markdown": "# Title\nContent"}
        result = _filter_record_content(record)
        assert result["markdown"] == "# Title\nContent"

    def test_only_main_content_filters_html(self):
        from flarecrawl.cli import _filter_record_content
        html = "<html><body><nav>Nav</nav><main><p>Main content long enough to pass threshold test easily here.</p></main></body></html>"
        record = {"url": "https://example.com", "html": html}
        result = _filter_record_content(record, only_main_content=True)
        assert "Main content" in result["html"]
        assert "Nav" not in result["html"]

    def test_exclude_tags_filters_html(self):
        from flarecrawl.cli import _filter_record_content
        html = "<html><body><p>Content</p><nav>Nav</nav></body></html>"
        record = {"url": "https://example.com", "html": html}
        result = _filter_record_content(record, exclude_tags=["nav"])
        assert "Content" in result["html"]
        assert "Nav" not in result["html"]


class TestUserAgent:
    """Test --user-agent flag."""

    def test_user_agent_in_scrape_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--user-agent" in result.output

    def test_user_agent_in_all_commands(self):
        commands = ["scrape", "crawl", "map", "download", "extract",
                    "screenshot", "pdf", "favicon", "schema"]
        for cmd in commands:
            result = runner.invoke(app, [cmd, "--help"])
            assert "--user-agent" in result.output, f"--user-agent missing from {cmd} help"

    def test_user_agent_in_body(self):
        from flarecrawl.client import Client
        body = Client._build_body(url="https://example.com", user_agent="Googlebot/2.1")
        assert body["userAgent"] == "Googlebot/2.1"

    def test_user_agent_overrides_mobile(self):
        """In _scrape_single, mobile preset is applied first, then user_agent overwrites."""
        from flarecrawl.client import Client, MOBILE_PRESET
        preset = {k: v for k, v in MOBILE_PRESET.items() if k != "user_agent"}
        body = Client._build_body(url="https://example.com",
                                  user_agent="CustomBot/1.0", **preset)
        assert body["userAgent"] == "CustomBot/1.0"


class TestNewV060Features:
    """Test v0.6.0 features: wait-for-selector, selector, js-eval, stdin, discover, har."""

    def test_scrape_has_wait_for_selector(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--wait-for-selector" in result.output

    def test_scrape_has_selector(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--selector" in result.output

    def test_scrape_has_js_eval(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--js-eval" in result.output

    def test_scrape_has_stdin(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--stdin" in result.output

    def test_scrape_has_har(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--har" in result.output

    def test_discover_help(self):
        result = runner.invoke(app, ["discover", "--help"])
        assert result.exit_code == 0
        assert "--sitemap" in result.output
        assert "--feed" in result.output
        assert "--links" in result.output
        assert "--limit" in result.output

    def test_discover_requires_auth(self, no_credentials):
        result = runner.invoke(app, ["discover", "https://example.com", "--json"])
        assert result.exit_code == 2

    def test_wait_for_selector_in_body(self):
        from flarecrawl.client import Client
        body = Client._build_body(url="https://example.com", wait_for=".content")
        assert body["waitForSelector"] == {"selector": ".content"}

    def test_stdin_processes_html(self):
        html = "<html><body><h1>Test Title</h1><p>Content here.</p></body></html>"
        result = runner.invoke(app, ["scrape", "--stdin", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Test Title" in data["data"]["content"]

    def test_stdin_with_only_main_content(self):
        html = "<html><body><nav>Nav</nav><main><p>Main content that is long enough to pass the fifty char threshold easily here.</p></main></body></html>"
        result = runner.invoke(app, ["scrape", "--stdin", "--only-main-content", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "Main content" in data["data"]["content"]
        assert "Nav" not in data["data"]["content"]

    def test_stdin_format_images(self):
        html = '<html><body><img src="https://example.com/photo.jpg" alt="Photo"></body></html>'
        result = runner.invoke(app, ["scrape", "--stdin", "--format", "images", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]["content"]) == 1
        assert data["data"]["content"][0]["url"] == "https://example.com/photo.jpg"

    def test_stdin_format_schema(self):
        html = '''<html><head><script type="application/ld+json">{"@type":"Organization","name":"Test"}</script></head><body></body></html>'''
        result = runner.invoke(app, ["scrape", "--stdin", "--format", "schema", "--json"], input=html)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["content"]["ld_json"][0]["@type"] == "Organization"


class TestBackupDir:
    """Test --backup-dir flag."""

    def test_scrape_has_backup_dir(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--backup-dir" in result.output

    def test_download_has_backup_dir(self):
        result = runner.invoke(app, ["download", "--help"])
        assert "--backup-dir" in result.output


class TestSanitizeFilename:
    """Test URL to filename conversion edge cases."""

    def test_basic_path(self):
        from flarecrawl.cli import _sanitize_filename
        assert _sanitize_filename("https://example.com/about") == "about"

    def test_index(self):
        from flarecrawl.cli import _sanitize_filename
        assert _sanitize_filename("https://example.com/") == "index"
        assert _sanitize_filename("https://example.com") == "index"

    def test_query_params_preserved(self):
        from flarecrawl.cli import _sanitize_filename
        name = _sanitize_filename("https://example.com/search?q=test&page=2")
        assert "search" in name
        assert "q-test" in name
        assert "page-2" in name

    def test_different_queries_different_names(self):
        from flarecrawl.cli import _sanitize_filename
        name1 = _sanitize_filename("https://example.com/search?q=cats")
        name2 = _sanitize_filename("https://example.com/search?q=dogs")
        assert name1 != name2

    def test_long_url_truncated(self):
        from flarecrawl.cli import _sanitize_filename
        long_url = "https://example.com/" + "a" * 300
        name = _sanitize_filename(long_url)
        assert len(name) <= 210  # 200 + hash suffix

    def test_nested_path(self):
        from flarecrawl.cli import _sanitize_filename
        name = _sanitize_filename("https://example.com/blog/2024/my-post")
        assert "blog" in name
        assert "2024" in name
        assert "my-post" in name

    def test_special_chars(self):
        from flarecrawl.cli import _sanitize_filename
        name = _sanitize_filename("https://example.com/page?id=1&lang=en&sort=date")
        assert "id-1" in name
        assert "lang-en" in name


class TestArchivedFlag:
    """Test --archived Internet Archive fallback."""

    def test_scrape_has_archived(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--archived" in result.output


class TestLanguageFlag:
    """Test --language flag."""

    def test_scrape_has_language(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--language" in result.output


class TestMagicFlag:
    """Test --magic cookie banner removal."""

    def test_scrape_has_magic(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--magic" in result.output


class TestV080Features:
    """Test v0.8.0 features."""

    def test_scrape_has_scroll(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--scroll" in result.output

    def test_scrape_has_query(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--query" in result.output

    def test_scrape_has_precision(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--precision" in result.output

    def test_scrape_has_recall(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--recall" in result.output

    def test_scrape_has_session(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--session" in result.output

    def test_precision_recall_mutually_exclusive(self):
        result = runner.invoke(app, [
            "scrape", "https://example.com",
            "--precision", "--recall", "--json",
        ])
        assert result.exit_code == 4

    def test_crawl_has_deduplicate(self):
        result = runner.invoke(app, ["crawl", "--help"])
        assert "--deduplicate" in result.output

    def test_batch_help(self):
        result = runner.invoke(app, ["batch", "--help"])
        assert result.exit_code == 0
        assert "YAML" in result.output

    def test_format_accessibility_in_help(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "accessibility" in result.output


class TestFilterByQuery:
    """Test relevance filtering."""

    def test_filter_keeps_relevant(self):
        from flarecrawl.extract import filter_by_query
        text = "## Python\n\nPython is a programming language.\n\n## Java\n\nJava is also a language.\n\n## Weather\n\nThe weather is sunny today."
        result = filter_by_query(text, "Python programming")
        assert "Python" in result
        assert "programming" in result

    def test_filter_empty_query(self):
        from flarecrawl.extract import filter_by_query
        text = "Some text here."
        assert filter_by_query(text, "") == text

    def test_filter_no_matches_returns_all(self):
        from flarecrawl.extract import filter_by_query
        text = "First paragraph.\n\nSecond paragraph."
        result = filter_by_query(text, "zzzznonexistent")
        assert result == text


class TestPrecisionRecall:
    """Test precision/recall extraction modes."""

    def test_precision_strips_more(self):
        from flarecrawl.extract import extract_main_content_precision
        html = "<html><body><nav>Nav</nav><article><p>Article content that is long enough to pass the threshold test here easily.</p></article><aside>Side</aside></body></html>"
        result = extract_main_content_precision(html)
        assert "Article content" in result
        assert "Nav" not in result
        assert "Side" not in result

    def test_recall_keeps_more(self):
        from flarecrawl.extract import extract_main_content_recall
        html = "<html><body><div class='content'><p>Content text that is absolutely long enough.</p><p>More content.</p></div><aside>Side</aside></body></html>"
        result = extract_main_content_recall(html)
        assert "Content text" in result


class TestAccessibilityTree:
    """Test accessibility tree extraction."""

    def test_extracts_headings(self):
        from flarecrawl.extract import extract_accessibility_tree
        html = "<html><body><h1>Title</h1><h2>Subtitle</h2></body></html>"
        tree = extract_accessibility_tree(html)
        headings = [n for n in tree if n.get("role") == "heading"]
        assert len(headings) == 2
        assert headings[0]["name"] == "Title"
        assert headings[0]["level"] == 1

    def test_extracts_links(self):
        from flarecrawl.extract import extract_accessibility_tree
        html = '<html><body><a href="https://example.com">Example</a></body></html>'
        tree = extract_accessibility_tree(html)
        links = [n for n in tree if n.get("role") == "link"]
        assert len(links) == 1
        assert links[0]["name"] == "Example"
        assert links[0]["href"] == "https://example.com"

    def test_extracts_landmarks(self):
        from flarecrawl.extract import extract_accessibility_tree
        html = "<html><body><nav>Nav</nav><main>Main</main><footer>Footer</footer></body></html>"
        tree = extract_accessibility_tree(html)
        roles = {n["role"] for n in tree}
        assert "navigation" in roles
        assert "main" in roles
        assert "contentinfo" in roles

    def test_extracts_form_controls(self):
        from flarecrawl.extract import extract_accessibility_tree
        html = '<html><body><input type="text" placeholder="Name"><button>Submit</button></body></html>'
        tree = extract_accessibility_tree(html)
        assert any(n.get("role") == "textbox" for n in tree)
        assert any(n.get("role") == "button" for n in tree)


class TestNegotiateCommands:
    """Test negotiate subcommand group."""

    def test_negotiate_status_help(self):
        result = runner.invoke(app, ["negotiate", "status", "--help"])
        assert result.exit_code == 0
        assert "domain cache" in result.output.lower() or "negotiate" in result.output.lower()

    def test_negotiate_clear_help(self):
        result = runner.invoke(app, ["negotiate", "clear", "--help"])
        assert result.exit_code == 0

    def test_negotiate_status_runs(self):
        result = runner.invoke(app, ["negotiate", "status"])
        assert result.exit_code == 0
        assert "Domains cached" in result.output or "domains" in result.output.lower()

    def test_negotiate_clear_runs(self):
        result = runner.invoke(app, ["negotiate", "clear"])
        assert result.exit_code == 0
        assert "Cleared" in result.output

    def test_negotiate_status_json(self):
        result = runner.invoke(app, ["negotiate", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "data" in data
        assert "total" in data["data"]
        assert "supporting" in data["data"]


class TestNegotiateFlag:
    """Test --no-negotiate flag and negotiate CLI integration."""

    def test_scrape_has_no_negotiate(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "--no-negotiate" in result.output

    def test_no_negotiate_in_help_text(self):
        result = runner.invoke(app, ["scrape", "--help"])
        assert "content negotiation" in result.output.lower() or "browser rendering" in result.output.lower()

    def test_negotiate_skipped_for_html_format(self):
        """When format=html, negotiation should not be attempted."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_content.return_value = "<html><body>Hello</body></html>"
        client.browser_ms_used = 100
        # Should NOT call try_negotiate since format is html
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            # This will fail on client mock, but we're checking negotiate wasn't called
            try:
                _scrape_single(client, "https://example.com", "html",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None)
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_skipped_with_js_flag(self):
        """When wait_until is set (--js), negotiation should not be attempted."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_markdown.return_value = "# Hello"
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None, wait_until="networkidle0")
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_skipped_with_no_negotiate(self):
        """When --no-negotiate is set, negotiation should not be attempted."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_markdown.return_value = "# Hello"
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None, no_negotiate=True)
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_attempted_for_default_scrape(self):
        """Default markdown scrape should attempt negotiation."""
        from flarecrawl.cli import _scrape_single
        from flarecrawl.negotiate import NegotiationResult
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.browser_ms_used = 0
        neg_result = NegotiationResult(
            content="# Negotiated",
            tokens=50,
            content_signal={"ai-train": "yes"},
            elapsed=0.1,
        )
        with patch("flarecrawl.negotiate.try_negotiate", return_value=neg_result) as mock_neg:
            result = _scrape_single(client, "https://example.com", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None)
            mock_neg.assert_called_once()
            assert result["content"] == "# Negotiated"
            assert result["metadata"]["source"] == "content-negotiation"
            assert result["metadata"]["markdownTokens"] == 50
            assert result["metadata"]["browserTimeMs"] == 0

    def test_negotiate_fallback_to_browser(self):
        """When negotiation returns None, should fall back to browser."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_markdown.return_value = "# Browser rendered"
        client.browser_ms_used = 150
        with patch("flarecrawl.negotiate.try_negotiate", return_value=None):
            result = _scrape_single(client, "https://example.com", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None)
            assert result["content"] == "# Browser rendered"
            client.get_markdown.assert_called_once()

    def test_negotiate_with_query_filter(self):
        """Negotiated content should still be filtered by --query."""
        from flarecrawl.cli import _scrape_single
        from flarecrawl.negotiate import NegotiationResult
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.browser_ms_used = 0
        long_content = (
            "# Article About Pricing\n\n"
            "The pricing model is based on usage tiers with volume discounts for enterprise customers.\n\n"
            "## Unrelated Section\n\n"
            "This section talks about something completely different and irrelevant to pricing."
        )
        neg_result = NegotiationResult(content=long_content, elapsed=0.1)
        with patch("flarecrawl.negotiate.try_negotiate", return_value=neg_result):
            result = _scrape_single(client, "https://example.com", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None, query="pricing")
            assert "pricing" in result["content"].lower()
            assert result["metadata"]["source"] == "content-negotiation"

    def test_negotiate_metadata_fields(self):
        """Negotiated results should have all expected metadata."""
        from flarecrawl.cli import _scrape_single
        from flarecrawl.negotiate import NegotiationResult
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.browser_ms_used = 0
        neg_result = NegotiationResult(
            content="# Test Title\n\nSome description text here that is long enough.",
            tokens=100,
            content_signal={"ai-train": "yes", "search": "yes"},
            elapsed=0.05,
        )
        with patch("flarecrawl.negotiate.try_negotiate", return_value=neg_result):
            result = _scrape_single(client, "https://example.com/page", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None)
            meta = result["metadata"]
            assert meta["source"] == "content-negotiation"
            assert meta["browserTimeMs"] == 0
            assert meta["markdownTokens"] == 100
            assert meta["contentSignal"]["ai-train"] == "yes"
            assert meta["format"] == "markdown"
            assert meta["title"] == "Test Title"
            assert "description" in meta
            assert meta["wordCount"] > 0
            assert meta["headingCount"] == 1
            assert meta["cacheHit"] is False
            assert meta["sourceURL"] == "https://example.com/page"

    def test_negotiate_skipped_for_scroll(self):
        """--scroll needs browser, should skip negotiation."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.browser_ms_used = 100
        client._build_body.return_value = {"url": "https://example.com"}
        client._post_json.return_value = {"result": "# Scrolled"}
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None, scroll=True)
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_skipped_for_magic(self):
        """--magic needs CSS injection, should skip negotiation."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_markdown.return_value = "# Magic"
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None, magic=True)
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_browser_rendering_has_source_metadata(self):
        """Browser-rendered results should have source: browser-rendering."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.get_markdown.return_value = "# Browser"
        client.browser_ms_used = 200
        with patch("flarecrawl.negotiate.try_negotiate", return_value=None):
            result = _scrape_single(client, "https://example.com", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None)
            assert result["metadata"]["source"] == "browser-rendering"
            assert result["metadata"]["browserTimeMs"] == 200

    def test_negotiate_skipped_for_screenshot(self):
        """Screenshots need browser, should skip negotiation."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.take_screenshot.return_value = b"PNG"
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=True,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None)
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_session_passed(self):
        """negotiate_session should be forwarded to try_negotiate."""
        from flarecrawl.cli import _scrape_single
        from flarecrawl.negotiate import NegotiationResult
        from unittest.mock import MagicMock, patch
        import httpx
        client = MagicMock()
        client.browser_ms_used = 0
        fake_session = MagicMock(spec=httpx.Client)
        neg_result = NegotiationResult(content="# Shared session", elapsed=0.05)
        with patch("flarecrawl.negotiate.try_negotiate", return_value=neg_result) as mock_neg:
            result = _scrape_single(client, "https://example.com", "markdown",
                                   wait_for=None, screenshot=False,
                                   full_page_screenshot=False, raw_body=None,
                                   timeout_ms=None, negotiate_session=fake_session)
            # Verify session was passed through
            call_kwargs = mock_neg.call_args
            assert call_kwargs.kwargs.get("session") is fake_session

    def test_negotiate_skipped_for_selector(self):
        """--selector needs CF /scrape endpoint, should skip negotiation."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.scrape.return_value = [{"text": "selected"}]
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False, raw_body=None,
                              timeout_ms=None, css_selector="main")
            except Exception:
                pass
            mock_neg.assert_not_called()

    def test_negotiate_skipped_for_raw_body(self):
        """--body passthrough should skip negotiation."""
        from flarecrawl.cli import _scrape_single
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        client.post_raw.return_value = {"result": "# Raw"}
        client.browser_ms_used = 100
        with patch("flarecrawl.negotiate.try_negotiate") as mock_neg:
            try:
                _scrape_single(client, "https://example.com", "markdown",
                              wait_for=None, screenshot=False,
                              full_page_screenshot=False,
                              raw_body={"url": "https://example.com"},
                              timeout_ms=None)
            except Exception:
                pass
            mock_neg.assert_not_called()
