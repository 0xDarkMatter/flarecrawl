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
        assert "flarecrawl 0.6.1" in result.output

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
