"""Unit tests for CDP error enrichment helper."""

from flarecrawl.client import FlareCrawlError
from flarecrawl.cli import _enrich_cdp_error


class TestEnrichCdpError:
    """Tests for _enrich_cdp_error()."""

    def test_bot_detection(self):
        e = FlareCrawlError("execution context was destroyed", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "Suggestions:" in str(enriched)
        assert "--stealth" in str(enriched)
        assert "--paywall" in str(enriched)

    def test_navigation_error(self):
        e = FlareCrawlError("Navigation failed: frame detached", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--stealth" in str(enriched)

    def test_detached_frame(self):
        e = FlareCrawlError("Target detached from page", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--stealth" in str(enriched)

    def test_timeout(self):
        e = FlareCrawlError("Page timed out after 30s", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--timeout 60000" in str(enriched)

    def test_timeout_variant(self):
        e = FlareCrawlError("Timeout waiting for selector", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--timeout 60000" in str(enriched)

    def test_redirect(self):
        e = FlareCrawlError("Too many redirect hops", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--browser-cookies" in str(enriched)

    def test_network_error(self):
        e = FlareCrawlError("Network error: net::ERR_CONNECTION_REFUSED", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--proxy" in str(enriched)

    def test_connection_error(self):
        e = FlareCrawlError("Connection reset by peer", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--proxy" in str(enriched)

    def test_websocket_error(self):
        e = FlareCrawlError("WebSocket connection closed", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "flarecrawl cdp sessions" in str(enriched)

    def test_auth_401(self):
        e = FlareCrawlError("Server returned 401 Unauthorized", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--interactive" in str(enriched)

    def test_auth_403(self):
        e = FlareCrawlError("Server returned 403 Forbidden", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--interactive" in str(enriched)

    def test_cookies_error(self):
        e = FlareCrawlError("Cookies expired or invalid", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "--browser-cookies" in str(enriched)

    def test_no_match_returns_unchanged(self):
        e = FlareCrawlError("Something completely unknown", code="CDP_ERROR")
        result = _enrich_cdp_error(e)
        assert result is e  # exact same object
        assert "Suggestions:" not in str(result)

    def test_preserves_code(self):
        e = FlareCrawlError("execution context was destroyed", code="CUSTOM_CODE")
        enriched = _enrich_cdp_error(e)
        assert enriched.code == "CUSTOM_CODE"

    def test_preserves_code_on_timeout(self):
        e = FlareCrawlError("Timeout connecting", code="TIMEOUT")
        enriched = _enrich_cdp_error(e)
        assert enriched.code == "TIMEOUT"

    def test_multiple_matches(self):
        e = FlareCrawlError(
            "Network error: connection timed out at auth endpoint",
            code="CDP_ERROR",
        )
        enriched = _enrich_cdp_error(e)
        msg = str(enriched)
        assert "--timeout 60000" in msg
        assert "--proxy" in msg
        assert "--interactive" in msg

    def test_case_insensitive(self):
        e = FlareCrawlError("EXECUTION CONTEXT was DESTROYED", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e)
        assert "Suggestions:" in str(enriched)

    def test_url_parameter_accepted(self):
        e = FlareCrawlError("Timeout waiting for page", code="CDP_ERROR")
        enriched = _enrich_cdp_error(e, url="https://example.com")
        assert "--timeout 60000" in str(enriched)
        assert enriched.code == "CDP_ERROR"
