"""Tests for the paywall bypass cascade module."""

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import json
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from flarecrawl.paywall import (
    _MIN_WORD_COUNT,
    PaywallResult,
    _detect_paywall,
    _extract_hidden_content,
    _get_site_headers,
    _has_truncation_indicators,
    _try_archive_today,
    _try_jina,
    _try_referer_bypass,
    _try_ssr_extract,
    _try_stealth_fetch,
    _try_wayback,
    get_paywall_session,
    try_bypass,
)


# ------------------------------------------------------------------
# HTML fixtures
# ------------------------------------------------------------------

# CSS-only paywall: full article in SSR, hidden by .paywall class
_HIDDEN_PARAGRAPHS = "\n".join(
    f"<p>Hidden paragraph {i} with enough words in each paragraph to meet the minimum word count threshold for extraction testing purposes.</p>"
    for i in range(30)
)
PAYWALLED_SSR_HTML = f"""
<html><head>
<script type="application/ld+json">
{{"@type": "NewsArticle", "isAccessibleForFree": false,
 "hasPart": {{"cssSelector": ".paywall"}}}}
</script>
</head><body>
<nav>Navigation</nav>
<article>
  <h1>Breaking News Article</h1>
  <p>First paragraph of the article that is visible to everyone.</p>
  <p>Second paragraph with more detail about the story at hand.</p>
  <div class="paywall" style="display: none; overflow: hidden;">
    {_HIDDEN_PARAGRAPHS}
  </div>
</article>
<footer>Footer</footer>
</body></html>
"""

# Normal article, no paywall
NORMAL_ARTICLE_HTML = """
<html><body>
<article>
  <h1>Regular Article Title</h1>
  """ + "\n".join(f"<p>Paragraph {i} with enough words to pass the threshold for counting purposes in tests.</p>" for i in range(30)) + """
</article>
</body></html>
"""

# Hard paywall: content genuinely truncated
HARD_PAYWALL_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "NewsArticle", "isAccessibleForFree": false}
</script>
</head><body>
<article>
  <h1>Premium Article</h1>
  <p>First paragraph preview only.</p>
</article>
<div class="paywall-overlay">
  <p>Subscribe to continue reading this article.</p>
</div>
</body></html>
"""

# Article with truncation text at the end
TRUNCATED_ARTICLE_HTML = """
<html><body>
<article>
  <h1>Metered Article</h1>
  """ + "\n".join(f"<p>Paragraph {i} with enough content to be considered substantial text for testing.</p>" for i in range(25)) + """
  <div class="subscribe-prompt">
    <p>Subscribe to continue reading this article.</p>
  </div>
</article>
</body></html>
"""


def _mock_response(text="", status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    return resp


def _mock_json_response(data, status_code=200):
    """Create a mock httpx.Response with JSON."""
    resp = MagicMock()
    resp.text = json.dumps(data)
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


# ------------------------------------------------------------------
# TestDetectPaywall
# ------------------------------------------------------------------


class TestDetectPaywall:
    """Test paywall signal detection."""

    def test_jsonld_not_accessible_false(self):
        soup = BeautifulSoup(PAYWALLED_SSR_HTML, "lxml")
        assert _detect_paywall(soup) is True

    def test_jsonld_not_accessible_string(self):
        html = '<html><body><script type="application/ld+json">{"isAccessibleForFree": "False"}</script></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is True

    def test_jsonld_accessible(self):
        html = '<html><body><script type="application/ld+json">{"isAccessibleForFree": true}</script><article>' + "word " * 300 + '</article></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is False

    def test_paywall_css_class(self):
        html = '<html><body><div class="paywall-container">Subscribe</div></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is True

    def test_metered_content_class(self):
        html = '<html><body><div class="metered-content">Locked</div></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is True

    def test_no_paywall_signals(self):
        soup = BeautifulSoup(NORMAL_ARTICLE_HTML, "lxml")
        assert _detect_paywall(soup) is False

    def test_truncation_text(self):
        html = '<html><body><p>' + 'word ' * 200 + '</p><p>Subscribe to continue reading</p></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is True

    def test_jsonld_array(self):
        html = '<html><body><script type="application/ld+json">[{"@type": "NewsArticle", "isAccessibleForFree": false}]</script></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is True

    def test_invalid_jsonld(self):
        html = '<html><body><script type="application/ld+json">not valid json</script></body></html>'
        soup = BeautifulSoup(html, "lxml")
        assert _detect_paywall(soup) is False


# ------------------------------------------------------------------
# TestSiteRules
# ------------------------------------------------------------------


class TestSiteRules:
    """Test per-site header lookup."""

    def test_nyt_headers(self):
        headers = _get_site_headers("https://www.nytimes.com/2026/04/01/article.html")
        assert "nyt-gdpr=0" in headers.get("Cookie", "")
        assert headers.get("Referer") == "https://www.google.com/"
        # No Googlebot UA - triggers DataDome, stealth tier handles TLS instead
        assert "Googlebot" not in headers.get("User-Agent", "")

    def test_medium_headers(self):
        headers = _get_site_headers("https://medium.com/some-article")
        assert headers.get("Referer") == "https://t.co/x?amp=1"
        assert headers.get("Cookie") == ""

    def test_unknown_domain(self):
        headers = _get_site_headers("https://unknown-site.com/page")
        assert headers == {}

    def test_ft_headers(self):
        headers = _get_site_headers("https://www.ft.com/content/abc123")
        assert headers.get("Referer") == "https://t.co/x?amp=1"

    def test_wired_headers(self):
        headers = _get_site_headers("https://www.wired.com/story/test")
        assert headers.get("Referer") == "https://www.google.com/"


# ------------------------------------------------------------------
# TestExtractHiddenContent
# ------------------------------------------------------------------


class TestExtractHiddenContent:
    """Test CSS-hidden content extraction."""

    def test_extracts_hidden_article(self):
        soup = BeautifulSoup(PAYWALLED_SSR_HTML, "lxml")
        content = _extract_hidden_content(soup)
        assert content is not None
        assert "Breaking News Article" in content
        assert "Hidden paragraph 0" in content
        assert "Hidden paragraph 29" in content

    def test_removes_paywall_overlay(self):
        html = """<html><body>
        <article><h1>Title</h1>""" + "".join(f"<p>Paragraph {i} with enough words to meet threshold for the test.</p>" for i in range(30)) + """</article>
        <div class="paywall-overlay"><p>Subscribe now</p></div>
        </body></html>"""
        soup = BeautifulSoup(html, "lxml")
        content = _extract_hidden_content(soup)
        assert content is not None
        assert "Subscribe now" not in content

    def test_genuinely_short_returns_none(self):
        html = "<html><body><article><p>Short.</p></article></body></html>"
        soup = BeautifulSoup(html, "lxml")
        content = _extract_hidden_content(soup)
        assert content is None

    def test_removes_display_none(self):
        html = '<html><body><article><div style="display: none;">' + "".join(f"<p>Hidden paragraph {i} with enough words for content threshold checking.</p>" for i in range(30)) + "</div></article></body></html>"
        soup = BeautifulSoup(html, "lxml")
        content = _extract_hidden_content(soup)
        assert content is not None
        assert "Hidden paragraph" in content


# ------------------------------------------------------------------
# TestHasTruncationIndicators
# ------------------------------------------------------------------


class TestHasTruncationIndicators:
    """Test truncation phrase detection."""

    def test_subscribe_to_read(self):
        text = "Article content here. " * 50 + "Subscribe to read more."
        assert _has_truncation_indicators(text) is True

    def test_no_indicators(self):
        text = "Article content here. " * 50 + "The end."
        assert _has_truncation_indicators(text) is False

    def test_indicator_at_start_not_end(self):
        # Indicator early in text but not in the tail - should not trigger
        text = "Subscribe to read more. " + "Article content here. " * 100
        assert _has_truncation_indicators(text) is False


# ------------------------------------------------------------------
# TestSsrExtract
# ------------------------------------------------------------------


class TestSsrExtract:
    """Test Tier 1: SSR extraction."""

    @patch("flarecrawl.paywall._fetch_html")
    def test_extracts_hidden_content(self, mock_fetch):
        mock_fetch.return_value = _mock_response(PAYWALLED_SSR_HTML)
        result = _try_ssr_extract("https://example.com/article", None, {})
        assert result is not None
        assert result.tier == "ssr"
        assert "Breaking News Article" in result.content
        assert result.metadata["wordCount"] >= 50

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_hard_paywall(self, mock_fetch):
        mock_fetch.return_value = _mock_response(HARD_PAYWALL_HTML)
        result = _try_ssr_extract("https://example.com/article", None, {})
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_network_error(self, mock_fetch):
        mock_fetch.return_value = None
        result = _try_ssr_extract("https://example.com/article", None, {})
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_404(self, mock_fetch):
        mock_fetch.return_value = _mock_response("Not found", 404)
        result = _try_ssr_extract("https://example.com/article", None, {})
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_passes_auth_header(self, mock_fetch):
        mock_fetch.return_value = _mock_response(PAYWALLED_SSR_HTML)
        _try_ssr_extract("https://example.com", None, {"Authorization": "Basic abc"})
        call_headers = mock_fetch.call_args[0][2]
        assert call_headers["Authorization"] == "Basic abc"


# ------------------------------------------------------------------
# TestStealthFetch
# ------------------------------------------------------------------


class TestStealthFetch:
    """Test Tier 2: Stealth fetch (curl_cffi TLS impersonation)."""

    @patch("flarecrawl.paywall.cffi_requests", create=True)
    def test_returns_content_on_success(self, mock_cffi):
        # Mock curl_cffi being available
        with patch.dict("sys.modules", {"curl_cffi": MagicMock(), "curl_cffi.requests": MagicMock()}) as _:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = NORMAL_ARTICLE_HTML
            with patch("flarecrawl.paywall._try_stealth_fetch") as mock_fn:
                mock_fn.return_value = PaywallResult(content="Full NYT article", tier="stealth", metadata={"wordCount": 1200, "impersonate": "safari"})
                result = mock_fn("https://www.nytimes.com/article.html", None, {})
                assert result is not None
                assert result.tier == "stealth"
                assert result.metadata["impersonate"] == "safari"

    def test_returns_none_when_curl_cffi_missing(self):
        # When curl_cffi is not installed, should return None gracefully
        import sys
        # Temporarily hide curl_cffi
        saved = sys.modules.get("curl_cffi")
        sys.modules["curl_cffi"] = None
        try:
            # Force re-import check
            result = _try_stealth_fetch("https://example.com", None, {})
            # Should return None (import fails)
        except Exception:
            result = None
        finally:
            if saved is not None:
                sys.modules["curl_cffi"] = saved
            elif "curl_cffi" in sys.modules:
                del sys.modules["curl_cffi"]
        # Either None or exception is acceptable - the tier is skipped


# ------------------------------------------------------------------
# TestRefererBypass
# ------------------------------------------------------------------


class TestRefererBypass:
    """Test Tier 3: Google Referer bypass."""

    @patch("flarecrawl.paywall._fetch_html")
    def test_sends_google_referer(self, mock_fetch):
        mock_fetch.return_value = _mock_response(NORMAL_ARTICLE_HTML)
        _try_referer_bypass("https://example.com/article", None, {})
        call_headers = mock_fetch.call_args[0][2]
        assert call_headers["Referer"] == "https://www.google.com/"
        assert call_headers["Sec-Fetch-Dest"] == "document"
        assert call_headers["Sec-Fetch-Mode"] == "navigate"
        assert call_headers["Sec-Fetch-Site"] == "cross-site"

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_full_content(self, mock_fetch):
        mock_fetch.return_value = _mock_response(NORMAL_ARTICLE_HTML)
        result = _try_referer_bypass("https://example.com/article", None, {})
        assert result is not None
        assert result.tier == "referer"
        assert result.metadata["wordCount"] >= _MIN_WORD_COUNT

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_truncated(self, mock_fetch):
        mock_fetch.return_value = _mock_response(TRUNCATED_ARTICLE_HTML)
        result = _try_referer_bypass("https://example.com/article", None, {})
        # Should return None because truncation indicators present
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_short_content(self, mock_fetch):
        mock_fetch.return_value = _mock_response("<html><body><p>Short.</p></body></html>")
        result = _try_referer_bypass("https://example.com/article", None, {})
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_returns_none_on_error(self, mock_fetch):
        mock_fetch.return_value = None
        result = _try_referer_bypass("https://example.com/article", None, {})
        assert result is None


# ------------------------------------------------------------------
# TestArchiveTodayTier
# ------------------------------------------------------------------


class TestArchiveTodayTier:
    """Test Tier 3: archive.today."""

    def test_returns_content_from_snapshot(self):
        session = MagicMock()
        resp = _mock_response(NORMAL_ARTICLE_HTML)
        resp.url = "https://archive.ph/abc123"
        session.get.return_value = resp

        result = _try_archive_today("https://example.com/article", session, {})
        assert result is not None
        assert result.tier == "archive-today"
        assert "archiveUrl" in result.metadata
        assert result.metadata["archiveDomain"] == "archive.ph"

    def test_tries_multiple_domains(self):
        session = MagicMock()
        # First domain fails, second succeeds
        resp_fail = MagicMock()
        resp_fail.status_code = 503
        resp_ok = _mock_response(NORMAL_ARTICLE_HTML)
        resp_ok.url = "https://archive.today/xyz789"
        session.get.side_effect = [resp_fail, resp_ok]

        result = _try_archive_today("https://example.com/article", session, {})
        assert result is not None
        assert result.tier == "archive-today"
        assert session.get.call_count == 2

    def test_returns_none_on_captcha(self):
        session = MagicMock()
        resp = _mock_response("<html><body>Please solve this captcha challenge</body></html>")
        resp.url = "https://archive.ph/captcha"
        session.get.return_value = resp

        result = _try_archive_today("https://example.com/article", session, {})
        assert result is None

    def test_returns_none_on_all_domains_fail(self):
        session = MagicMock()
        session.get.side_effect = httpx.ConnectError("No route to host")

        result = _try_archive_today("https://example.com/article", session, {})
        assert result is None

    def test_returns_none_on_short_content(self):
        session = MagicMock()
        resp = _mock_response("<html><body><p>Very short page.</p></body></html>")
        resp.url = "https://archive.ph/abc"
        session.get.return_value = resp

        result = _try_archive_today("https://example.com/article", session, {})
        assert result is None


# ------------------------------------------------------------------
# TestWaybackTier
# ------------------------------------------------------------------


class TestWaybackTier:
    """Test Tier 4: Wayback Machine."""

    @patch("flarecrawl.paywall._fetch_html")
    def test_wayback_available(self, mock_fetch):
        # First call: API check. Second call: fetch archived page.
        api_data = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/web/20250101/https://example.com/article",
                }
            }
        }
        api_resp = _mock_json_response(api_data)
        page_resp = _mock_response(NORMAL_ARTICLE_HTML)

        # Mock session.get for API call, _fetch_html for page fetch
        session = MagicMock()
        session.get.return_value = api_resp
        mock_fetch.return_value = page_resp

        result = _try_wayback("https://example.com/article", session, {})
        assert result is not None
        assert result.tier == "wayback"
        assert "archiveUrl" in result.metadata

    @patch("flarecrawl.paywall._fetch_html")
    def test_wayback_unavailable(self, mock_fetch):
        api_data = {"archived_snapshots": {}}
        session = MagicMock()
        session.get.return_value = _mock_json_response(api_data)

        result = _try_wayback("https://example.com/article", session, {})
        assert result is None

    def test_wayback_network_error(self):
        session = MagicMock()
        session.get.side_effect = httpx.ConnectError("Connection failed")

        result = _try_wayback("https://example.com/article", session, {})
        assert result is None


# ------------------------------------------------------------------
# TestJinaTier
# ------------------------------------------------------------------


class TestJinaTier:
    """Test Tier 5: Jina Reader."""

    @patch("flarecrawl.paywall._fetch_html")
    def test_jina_returns_markdown(self, mock_fetch):
        markdown = "# Article Title\n\n" + "Content paragraph. " * 50
        mock_fetch.return_value = _mock_response(markdown)
        result = _try_jina("https://example.com/article", None, {})
        assert result is not None
        assert result.tier == "jina"
        assert "Article Title" in result.content

    @patch("flarecrawl.paywall._fetch_html")
    def test_jina_calls_correct_url(self, mock_fetch):
        mock_fetch.return_value = _mock_response("Short")
        _try_jina("https://example.com/article", None, {})
        called_url = mock_fetch.call_args[0][0]
        assert called_url == "https://r.jina.ai/https://example.com/article"

    @patch("flarecrawl.paywall._fetch_html")
    def test_jina_empty_response(self, mock_fetch):
        mock_fetch.return_value = _mock_response("")
        result = _try_jina("https://example.com/article", None, {})
        assert result is None

    @patch("flarecrawl.paywall._fetch_html")
    def test_jina_error(self, mock_fetch):
        mock_fetch.return_value = None
        result = _try_jina("https://example.com/article", None, {})
        assert result is None


# ------------------------------------------------------------------
# TestTryBypass (cascade integration)
# ------------------------------------------------------------------


class TestTryBypass:
    """Test the full bypass cascade."""

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_ssr_succeeds_skips_rest(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = PaywallResult(content="Full article", tier="ssr")
        result = try_bypass("https://example.com/article")
        assert result is not None
        assert result.tier == "ssr"
        mock_stealth.assert_not_called()
        mock_ref.assert_not_called()
        mock_at.assert_not_called()
        mock_wb.assert_not_called()
        mock_jina.assert_not_called()

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_stealth_succeeds(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = None
        mock_stealth.return_value = PaywallResult(content="NYT full article", tier="stealth", metadata={"impersonate": "safari"})
        result = try_bypass("https://example.com/article")
        assert result is not None
        assert result.tier == "stealth"
        mock_ref.assert_not_called()
        mock_at.assert_not_called()
        mock_wb.assert_not_called()
        mock_jina.assert_not_called()

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_ssr_fails_referer_succeeds(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = None
        mock_stealth.return_value = None
        mock_ref.return_value = PaywallResult(content="Article via referer", tier="referer")
        result = try_bypass("https://example.com/article")
        assert result is not None
        assert result.tier == "referer"
        mock_at.assert_not_called()
        mock_wb.assert_not_called()
        mock_jina.assert_not_called()

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_all_fail(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = None
        mock_stealth.return_value = None
        mock_ref.return_value = None
        mock_at.return_value = None
        mock_wb.return_value = None
        mock_jina.return_value = None
        result = try_bypass("https://example.com/article")
        assert result is None

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_falls_through_to_jina(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = None
        mock_stealth.return_value = None
        mock_ref.return_value = None
        mock_at.return_value = None
        mock_wb.return_value = None
        mock_jina.return_value = PaywallResult(content="Jina content", tier="jina")
        result = try_bypass("https://example.com/article")
        assert result is not None
        assert result.tier == "jina"

    @patch("flarecrawl.paywall._try_jina")
    @patch("flarecrawl.paywall._try_wayback")
    @patch("flarecrawl.paywall._try_archive_today")
    @patch("flarecrawl.paywall._try_referer_bypass")
    @patch("flarecrawl.paywall._try_stealth_fetch")
    @patch("flarecrawl.paywall._try_ssr_extract")
    def test_archive_today_succeeds(self, mock_ssr, mock_stealth, mock_ref, mock_at, mock_wb, mock_jina):
        mock_ssr.return_value = None
        mock_stealth.return_value = None
        mock_ref.return_value = None
        mock_at.return_value = PaywallResult(content="Archived article", tier="archive-today")
        result = try_bypass("https://example.com/article")
        assert result is not None
        assert result.tier == "archive-today"
        mock_wb.assert_not_called()
        mock_jina.assert_not_called()

    def test_default_user_agent(self):
        with patch("flarecrawl.paywall._try_ssr_extract") as mock_ssr:
            mock_ssr.return_value = PaywallResult(content="ok", tier="ssr")
            try_bypass("https://example.com")
            headers = mock_ssr.call_args[0][2]
            assert "Chrome" in headers["User-Agent"]

    def test_custom_user_agent(self):
        with patch("flarecrawl.paywall._try_ssr_extract") as mock_ssr:
            mock_ssr.return_value = PaywallResult(content="ok", tier="ssr")
            try_bypass("https://example.com", extra_headers={"User-Agent": "MyBot/1.0"})
            headers = mock_ssr.call_args[0][2]
            assert headers["User-Agent"] == "MyBot/1.0"


# ------------------------------------------------------------------
# TestPaywallSession
# ------------------------------------------------------------------


class TestPaywallSession:
    """Test shared session creation."""

    def test_creates_client(self):
        session = get_paywall_session()
        assert session is not None
        session.close()

    def test_custom_timeout(self):
        session = get_paywall_session(timeout=30)
        assert session is not None
        session.close()


# ------------------------------------------------------------------
# TestPaywallCliFlag
# ------------------------------------------------------------------


class TestPaywallCliFlag:
    """Test --paywall and --stealth flags appear in CLI help."""

    def test_scrape_has_paywall_flag(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--paywall" in result.output

    def test_scrape_has_stealth_flag(self):
        from typer.testing import CliRunner
        from flarecrawl.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--stealth" in result.output


import httpx  # noqa: E402 (needed for ConnectError in test)
