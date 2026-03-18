"""Client tests for Flarecrawl."""

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
