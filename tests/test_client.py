"""Client tests for Flarecrawl."""

import warnings
from unittest.mock import patch

from flarecrawl.client import Client


class TestBodyBuilder:
    """Test the body builder converts flat kwargs to nested API JSON."""

    def test_basic_url(self):
        body = Client._build_body(url="https://example.com")
        assert body == {"url": "https://example.com"}

    def test_goto_options(self):
        body = Client._build_body(
            url="https://example.com",
            wait_until="networkidle0",
            timeout=30000,
        )
        assert body["gotoOptions"]["waitUntil"] == "networkidle0"
        assert body["gotoOptions"]["timeout"] == 30000

    def test_wait_for_selector(self):
        body = Client._build_body(url="https://example.com", wait_for=".content")
        assert body["waitForSelector"]["selector"] == ".content"

    def test_screenshot_options(self):
        body = Client._build_body(
            url="https://example.com",
            full_page=True,
            image_type="jpeg",
            quality=80,
        )
        assert body["screenshotOptions"]["fullPage"] is True
        assert body["screenshotOptions"]["type"] == "jpeg"
        assert body["screenshotOptions"]["quality"] == 80

    def test_viewport(self):
        body = Client._build_body(url="https://example.com", width=1280, height=720)
        assert body["viewport"]["width"] == 1280
        assert body["viewport"]["height"] == 720

    def test_pdf_options(self):
        body = Client._build_body(
            url="https://example.com",
            landscape=True,
            paper_format="a4",
            print_background=True,
        )
        assert body["pdfOptions"]["landscape"] is True
        assert body["pdfOptions"]["format"] == "a4"

    def test_crawl_options(self):
        body = Client._build_body(
            url="https://example.com",
            limit=50,
            depth=3,
            formats=["markdown"],
            render=True,
            include_external=True,
            include_subdomains=True,
            include_patterns=["/docs/*"],
            exclude_patterns=["/blog/*"],
        )
        assert body["limit"] == 50
        assert body["depth"] == 3
        assert body["formats"] == ["markdown"]
        assert body["options"]["includeExternalLinks"] is True
        assert body["options"]["includePatterns"] == ["/docs/*"]
        assert body["options"]["excludePatterns"] == ["/blog/*"]

    def test_links_options(self):
        body = Client._build_body(
            url="https://example.com",
            visible_only=True,
            internal_only=True,
        )
        assert body["visibleLinksOnly"] is True
        assert body["excludeExternalLinks"] is True

    def test_elements(self):
        body = Client._build_body(
            url="https://example.com",
            elements=[{"selector": "h1"}, {"selector": "a"}],
        )
        assert body["elements"] == [{"selector": "h1"}, {"selector": "a"}]

    def test_extract_options(self):
        body = Client._build_body(
            url="https://example.com",
            prompt="Extract product info",
            response_format={"type": "json_schema", "schema": {"type": "object"}},
        )
        assert body["prompt"] == "Extract product info"
        assert body["response_format"]["type"] == "json_schema"

    def test_user_agent(self):
        body = Client._build_body(url="https://example.com", user_agent="CustomBot/1.0")
        assert body["userAgent"] == "CustomBot/1.0"

    def test_reject_resources(self):
        body = Client._build_body(
            url="https://example.com",
            reject_resources=["image", "media"],
        )
        assert body["rejectResourceTypes"] == ["image", "media"]

    def test_html_instead_of_url(self):
        body = Client._build_body(html="<h1>Hello</h1>")
        assert body == {"html": "<h1>Hello</h1>"}
        assert "url" not in body

    def test_max_depth_alias(self):
        """Python-API callers can pass max_depth; CF expects 'depth'."""
        body = Client._build_body(url="https://example.com", max_depth=4)
        assert body["depth"] == 4
        assert "max_depth" not in body

    def test_format_singular_coerces_to_formats_list(self):
        """Python-API callers can pass format='markdown'; CF expects formats=['markdown']."""
        body = Client._build_body(url="https://example.com", format="markdown")
        assert body["formats"] == ["markdown"]
        assert "format" not in body

    def test_depth_takes_precedence_over_max_depth(self):
        body = Client._build_body(url="https://example.com", depth=2, max_depth=9)
        assert body["depth"] == 2
        assert "max_depth" not in body


class TestCrawlStartUnsupportedKwargs:
    """CF /crawl rejects user_agent / rate_limit; crawl_start must strip them."""

    def _client(self):
        return Client(account_id="test-id", api_token="test-token", cache_ttl=0)

    def test_crawl_start_strips_user_agent_and_rate_limit(self):
        client = self._client()
        captured = {}

        def fake_post_json(endpoint, body):
            captured["endpoint"] = endpoint
            captured["body"] = body
            return {"result": "job-123"}

        with patch.object(client, "_post_json", side_effect=fake_post_json):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                job_id = client.crawl_start(
                    "https://example.com",
                    limit=50,
                    max_depth=4,
                    format="markdown",
                    user_agent="Foo/1.0",
                    rate_limit=1.0,
                )

        assert job_id == "job-123"
        body = captured["body"]
        # Rejected keys must not appear in the outgoing body
        assert "userAgent" not in body
        assert "user_agent" not in body
        assert "rate_limit" not in body
        assert "rateLimit" not in body
        # Aliases must be translated
        assert body["depth"] == 4
        assert body["formats"] == ["markdown"]
        assert body["limit"] == 50
        # And a warning was raised for each stripped key
        messages = [str(w.message) for w in caught]
        assert any("user_agent" in m for m in messages)
        assert any("rate_limit" in m for m in messages)

    def test_crawl_start_clean_kwargs_emits_no_warnings(self):
        """A well-formed call must not produce spurious warnings."""
        client = self._client()
        with patch.object(client, "_post_json", return_value={"result": "j1"}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client.crawl_start("https://example.com", limit=10, depth=2, formats=["markdown"])
        # Filter to UserWarning-ish only — pytest/httpx may emit unrelated
        relevant = [w for w in caught if "user_agent" in str(w.message) or "rate_limit" in str(w.message)]
        assert relevant == []

    def test_crawl_start_explicit_none_does_not_warn(self):
        """Passing user_agent=None / rate_limit=None should be a silent no-op."""
        client = self._client()
        with patch.object(client, "_post_json", return_value={"result": "j1"}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                client.crawl_start("https://example.com", user_agent=None, rate_limit=None)
        relevant = [w for w in caught if "user_agent" in str(w.message) or "rate_limit" in str(w.message)]
        assert relevant == []

    def test_crawl_start_with_kwargs_matching_roamcrawler_repro(self):
        """The exact reproducer from pmail #203 must not produce CF-rejected keys."""
        client = self._client()
        captured = {}

        def fake_post_json(endpoint, body):
            captured["body"] = body
            return {"result": "job-abc"}

        with patch.object(client, "_post_json", side_effect=fake_post_json):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                client.crawl_start(
                    "https://example.com",
                    limit=50,
                    max_depth=4,
                    format="markdown",
                    user_agent="Foo/1.0",
                    rate_limit=1.0,
                )

        body = captured["body"]
        # The keys CF rejected in the original 400 must all be absent
        for rejected in ("userAgent", "user_agent", "rate_limit", "rateLimit", "max_depth", "format"):
            assert rejected not in body, f"{rejected} leaked into body"
        # And the body must contain only CF-recognised keys
        allowed = {"url", "limit", "depth", "formats", "options", "source", "render",
                   "maxAge", "modifiedSince"}
        leaked = set(body.keys()) - allowed
        assert leaked == set(), f"unexpected keys in body: {leaked}"


class TestBuildBodyFormatAliasEdgeCases:
    """Edge cases around format/formats kwarg coercion."""

    def test_format_as_list_passes_through(self):
        body = Client._build_body(url="https://example.com", format=["markdown", "html"])
        assert body["formats"] == ["markdown", "html"]

    def test_formats_takes_precedence_over_format(self):
        body = Client._build_body(
            url="https://example.com",
            formats=["markdown"],
            format="html",
        )
        assert body["formats"] == ["markdown"]
        assert "format" not in body

    def test_format_alone_does_not_leak_to_passthrough(self):
        """The pass-through loop must not see the alias key."""
        body = Client._build_body(url="https://example.com", format="html")
        assert "format" not in body
        assert body["formats"] == ["html"]


class TestRejectResourcesDefaults:
    """Test that text-extraction methods add rejectResourceTypes by default."""

    def test_get_markdown_rejects_resources(self):
        """get_markdown should add rejectResourceTypes by default."""
        client = Client(account_id="test-id", api_token="test-token", cache_ttl=0)
        # We can't call the method without a real API, but we can check
        # the class has the default list
        assert "image" in client._REJECT_RESOURCES_DEFAULT
        assert "stylesheet" in client._REJECT_RESOURCES_DEFAULT
        assert "font" in client._REJECT_RESOURCES_DEFAULT
        assert "media" in client._REJECT_RESOURCES_DEFAULT

    def test_reject_resources_list_length(self):
        assert len(Client._REJECT_RESOURCES_DEFAULT) == 4


class TestConnectionPooling:
    """Test httpx.Client session configuration."""

    def test_session_created(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert client._session is not None

    def test_session_has_http2(self):
        client = Client(account_id="test-id", api_token="test-token")
        # httpx.Client with http2=True should have HTTP/2 support
        assert client._session._transport is not None

    def test_context_manager(self):
        with Client(account_id="test-id", api_token="test-token") as client:
            assert client._session is not None

    def test_cache_ttl_default(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert client.cache_ttl == 3600

    def test_cache_ttl_custom(self):
        client = Client(account_id="test-id", api_token="test-token", cache_ttl=0)
        assert client.cache_ttl == 0


class TestClientUrls:
    """Test URL construction."""

    def test_base_url(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert client._base == "https://api.cloudflare.com/client/v4/accounts/test-id/browser-rendering"

    def test_headers(self):
        client = Client(account_id="test-id", api_token="test-token")
        # Headers are now on the persistent session
        headers = client._session.headers
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"


class TestMobilePreset:
    """Test mobile device preset."""

    def test_mobile_preset_exists(self):
        from flarecrawl.client import MOBILE_PRESET
        assert "width" in MOBILE_PRESET
        assert "height" in MOBILE_PRESET
        assert "user_agent" in MOBILE_PRESET
        assert "device_scale_factor" in MOBILE_PRESET

    def test_mobile_preset_values(self):
        from flarecrawl.client import MOBILE_PRESET
        assert MOBILE_PRESET["width"] == 390
        assert MOBILE_PRESET["height"] == 844
        assert MOBILE_PRESET["device_scale_factor"] == 3
        assert "iPhone" in MOBILE_PRESET["user_agent"]

    def test_mobile_in_body(self):
        from flarecrawl.client import MOBILE_PRESET
        body = Client._build_body(url="https://example.com", **MOBILE_PRESET)
        assert body["viewport"]["width"] == 390
        assert body["viewport"]["height"] == 844
        assert body["viewport"]["deviceScaleFactor"] == 3
        assert "iPhone" in body["userAgent"]

    def test_mobile_flag_in_scrape_help(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        for cmd in ["scrape", "screenshot", "pdf"]:
            result = runner.invoke(app, [cmd, "--help"])
            assert "--mobile" in result.output, f"--mobile missing from {cmd} help"


class TestBrowserTimeTracking:
    """Test browser time accumulation."""

    def test_initial_browser_time_zero(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert client.browser_ms_used == 0

    def test_retry_codes(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert 429 in client.RETRY_CODES
        assert 503 in client.RETRY_CODES
        assert 502 in client.RETRY_CODES
        assert 400 not in client.RETRY_CODES

    def test_max_retries(self):
        client = Client(account_id="test-id", api_token="test-token")
        assert client.MAX_RETRIES == 3


class TestHandleError:
    """Test error handling and enrichment."""

    def test_network_error_422_suggests_auth(self):
        """CF 422 'Network error' should hint about --auth."""
        from unittest.mock import MagicMock

        from flarecrawl.client import FlareCrawlError

        client = Client(account_id="test-id", api_token="test-token")
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {
            "errors": [{"message": "Network error when attempting to load page"}]
        }
        try:
            client._handle_error(response)
            assert False, "Should have raised"
        except FlareCrawlError as e:
            assert "--auth user:password" in str(e)
            assert "--session cookies.json" in str(e)
            assert e.status_code == 422

    def test_network_error_non_422_no_hint(self):
        """Non-422 network errors should not get the auth hint."""
        from unittest.mock import MagicMock

        from flarecrawl.client import FlareCrawlError

        client = Client(account_id="test-id", api_token="test-token")
        response = MagicMock()
        response.status_code = 500
        response.json.return_value = {
            "errors": [{"message": "Network error when attempting to load page"}]
        }
        try:
            client._handle_error(response)
            assert False, "Should have raised"
        except FlareCrawlError as e:
            assert "--auth" not in str(e)

    def test_non_network_422_no_hint(self):
        """422 with a different message should not get the auth hint."""
        from unittest.mock import MagicMock

        from flarecrawl.client import FlareCrawlError

        client = Client(account_id="test-id", api_token="test-token")
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {
            "errors": [{"message": "Invalid URL format"}]
        }
        try:
            client._handle_error(response)
            assert False, "Should have raised"
        except FlareCrawlError as e:
            assert "--auth" not in str(e)
            assert "Invalid URL format" in str(e)
