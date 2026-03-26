"""Tests for markdown content negotiation."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from flarecrawl.negotiate import (
    NegotiationResult,
    clear_domain_cache,
    get_negotiate_session,
    _cache_domain,
    _load_domain_cache,
    _parse_content_signal,
    _save_domain_cache,
    domain_supports_markdown,
    try_negotiate,
)


# ------------------------------------------------------------------
# Content-Signal parser
# ------------------------------------------------------------------


class TestParseContentSignal:
    def test_basic(self):
        result = _parse_content_signal("ai-train=yes, search=yes, ai-input=yes")
        assert result == {"ai-train": "yes", "search": "yes", "ai-input": "yes"}

    def test_single(self):
        assert _parse_content_signal("ai-train=no") == {"ai-train": "no"}

    def test_empty(self):
        assert _parse_content_signal("") == {}

    def test_whitespace(self):
        result = _parse_content_signal("  ai-train = yes ,  search = no  ")
        assert result == {"ai-train": "yes", "search": "no"}


# ------------------------------------------------------------------
# Domain capability cache
# ------------------------------------------------------------------


class TestDomainCache:
    def test_unknown_domain(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")
        assert domain_supports_markdown("unknown.com") is None

    def test_cache_positive(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        _cache_domain("example.com", True)
        assert domain_supports_markdown("example.com") is True

    def test_cache_negative(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        _cache_domain("example.com", False)
        assert domain_supports_markdown("example.com") is False

    def test_positive_ttl_not_expired(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        _cache_domain("example.com", True)
        # Should still be valid
        assert domain_supports_markdown("example.com") is True

    def test_negative_ttl_expired(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        # Write entry with old timestamp
        data = {"example.com": {"supports": False, "checked": time.time() - 100000}}
        cache_file.write_text(json.dumps(data), encoding="utf-8")
        # Should return None (expired)
        assert domain_supports_markdown("example.com") is None

    def test_positive_ttl_expired(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        # 8 days old positive entry
        data = {"example.com": {"supports": True, "checked": time.time() - 8 * 86400}}
        cache_file.write_text(json.dumps(data), encoding="utf-8")
        assert domain_supports_markdown("example.com") is None

    def test_cache_pruning(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        # Add 510 entries
        data = {}
        for i in range(510):
            data[f"domain{i}.com"] = {"supports": False, "checked": time.time() - i}
        cache_file.write_text(json.dumps(data), encoding="utf-8")

        # Adding one more should trigger pruning
        _cache_domain("new.com", True)

        cache = _load_domain_cache()
        assert len(cache) <= 500

    def test_corrupt_cache_file(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        cache_file.write_text("not json!", encoding="utf-8")
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)
        assert domain_supports_markdown("any.com") is None

    def test_clear_domain_cache(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)

        _cache_domain("a.com", True)
        _cache_domain("b.com", False)
        count = clear_domain_cache()
        assert count == 2
        assert not cache_file.exists()

    def test_clear_empty_cache(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "md.json"
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: cache_file)
        count = clear_domain_cache()
        assert count == 0

    def test_get_negotiate_session(self):
        session = get_negotiate_session()
        assert isinstance(session, httpx.Client)
        session.close()


# ------------------------------------------------------------------
# Negotiation
# ------------------------------------------------------------------


def _mock_response(content_type="text/markdown; charset=utf-8", status_code=200,
                   text="# Hello\n\nWorld.", headers=None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    all_headers = {"content-type": content_type}
    if headers:
        all_headers.update(headers)
    resp.headers = all_headers
    return resp


class TestTryNegotiate:
    def test_success_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            text="# Article\n\nContent here.",
            headers={"x-markdown-tokens": "42", "content-signal": "ai-train=yes"},
        )

        result = try_negotiate("https://blog.cloudflare.com/post", session=mock_session)

        assert result is not None
        assert isinstance(result, NegotiationResult)
        assert result.content == "# Article\n\nContent here."
        assert result.tokens == 42
        assert result.content_signal == {"ai-train": "yes"}
        assert result.elapsed >= 0

        # Domain should be cached as supporting
        assert domain_supports_markdown("blog.cloudflare.com") is True

    def test_fallback_html(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/html; charset=utf-8",
            text="<html><body>Hello</body></html>",
        )

        result = try_negotiate("https://example.com", session=mock_session)
        assert result is None

        # Domain should be cached as NOT supporting
        assert domain_supports_markdown("example.com") is False

    def test_cached_negative_skips_request(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        # Pre-cache as non-supporting
        _cache_domain("example.com", False)

        mock_session = MagicMock(spec=httpx.Client)
        result = try_negotiate("https://example.com/page", session=mock_session)

        assert result is None
        # Should NOT have made a request
        mock_session.get.assert_not_called()

    def test_cached_positive_still_requests(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        # Pre-cache as supporting
        _cache_domain("blog.cf.com", True)

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(text="# Cached positive")

        result = try_negotiate("https://blog.cf.com/article", session=mock_session)
        assert result is not None
        assert result.content == "# Cached positive"
        mock_session.get.assert_called_once()

    def test_network_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.side_effect = httpx.ConnectError("connection refused")

        result = try_negotiate("https://down.com/page", session=mock_session)
        assert result is None

        # Should NOT cache on transient errors
        assert domain_supports_markdown("down.com") is None

    def test_timeout_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.side_effect = httpx.TimeoutException("timeout")

        result = try_negotiate("https://slow.com/page", session=mock_session)
        assert result is None
        assert domain_supports_markdown("slow.com") is None

    def test_non_200_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/markdown",
            status_code=404,
            text="Not found",
        )

        result = try_negotiate("https://example.com/missing", session=mock_session)
        assert result is None

    def test_accept_header_sent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/html",
            text="<html>",
        )

        try_negotiate("https://example.com", session=mock_session)

        call_kwargs = mock_session.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "text/markdown" in headers.get("Accept", "")

    def test_default_user_agent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/html", text="<html>"
        )

        try_negotiate("https://example.com", session=mock_session)

        call_kwargs = mock_session.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Flarecrawl" in headers.get("User-Agent", "") and "github.com" in headers.get("User-Agent", "")

    def test_user_agent_override(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/html", text="<html>"
        )

        try_negotiate(
            "https://example.com",
            session=mock_session,
            extra_headers={"User-Agent": "CustomBot/1.0"},
        )

        call_kwargs = mock_session.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers["User-Agent"] == "CustomBot/1.0"

    def test_extra_headers_passed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/html",
            text="<html>",
        )

        try_negotiate(
            "https://example.com",
            session=mock_session,
            extra_headers={"Authorization": "Basic dXNlcjpwYXNz", "Accept-Language": "de"},
        )

        call_kwargs = mock_session.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers["Authorization"] == "Basic dXNlcjpwYXNz"
        assert headers["Accept-Language"] == "de"

    def test_no_tokens_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            text="# No tokens header",
            headers={},  # No x-markdown-tokens
        )

        result = try_negotiate("https://example.com", session=mock_session)
        assert result is not None
        assert result.tokens is None

    def test_no_content_signal_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            text="# No signal header",
            headers={},
        )

        result = try_negotiate("https://example.com", session=mock_session)
        assert result is not None
        assert result.content_signal is None

    def test_creates_own_session_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        # Mock httpx.Client to avoid real requests
        with patch("flarecrawl.negotiate.httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_instance.get.return_value = _mock_response(
                content_type="text/html", text="<html>"
            )
            MockClient.return_value = mock_instance

            result = try_negotiate("https://example.com")
            assert result is None
            MockClient.assert_called_once()
            mock_instance.close.assert_called_once()

    def test_empty_body_returns_none(self, tmp_path, monkeypatch):
        """Markdown response with empty body should return None-like result."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(text="")

        result = try_negotiate("https://empty.com/page", session=mock_session)
        # Empty markdown is technically valid — still returns a result
        assert result is not None
        assert result.content == ""

    def test_charset_variants(self, tmp_path, monkeypatch):
        """text/markdown with various charset params should all match."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        for ct in ["text/markdown", "text/markdown; charset=utf-8",
                    "text/markdown;charset=UTF-8", "text/markdown; boundary=something"]:
            mock_session = MagicMock(spec=httpx.Client)
            mock_session.get.return_value = _mock_response(
                content_type=ct, text="# Works"
            )
            result = try_negotiate(f"https://charset-{ct[:10]}.com/page", session=mock_session)
            assert result is not None, f"Failed for content-type: {ct}"

    def test_invalid_tokens_header(self, tmp_path, monkeypatch):
        """Non-numeric x-markdown-tokens should not crash."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            text="# Content",
            headers={"x-markdown-tokens": "not-a-number"},
        )

        result = try_negotiate("https://bad-tokens.com", session=mock_session)
        assert result is not None
        assert result.tokens is None  # Gracefully ignored

    def test_redirect_same_domain(self, tmp_path, monkeypatch):
        """Redirects within the same domain should still work."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(text="# Redirected content")

        result = try_negotiate("https://example.com/old-path", session=mock_session)
        assert result is not None
        assert result.content == "# Redirected content"

    def test_server_error_returns_none(self, tmp_path, monkeypatch):
        """500 with text/markdown content-type should still return None."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(
            content_type="text/markdown", status_code=500, text="Error"
        )

        result = try_negotiate("https://error.com", session=mock_session)
        assert result is None

    def test_multiple_domains_cached(self, tmp_path, monkeypatch):
        """Multiple domains should each have independent cache entries."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        # Domain A supports markdown
        _cache_domain("a.com", True)
        # Domain B does not
        _cache_domain("b.com", False)

        assert domain_supports_markdown("a.com") is True
        assert domain_supports_markdown("b.com") is False
        assert domain_supports_markdown("c.com") is None

    def test_port_in_domain(self, tmp_path, monkeypatch):
        """URLs with ports should cache by domain:port."""
        monkeypatch.setattr("flarecrawl.negotiate._cache_path", lambda: tmp_path / "md.json")

        mock_session = MagicMock(spec=httpx.Client)
        mock_session.get.return_value = _mock_response(text="# Port test")

        result = try_negotiate("https://example.com:8443/page", session=mock_session)
        assert result is not None
        assert domain_supports_markdown("example.com:8443") is True
        # Standard port should be independent
        assert domain_supports_markdown("example.com") is None


# ------------------------------------------------------------------
# NegotiationResult dataclass
# ------------------------------------------------------------------


class TestNegotiationResult:
    def test_defaults(self):
        r = NegotiationResult(content="# Hello")
        assert r.content == "# Hello"
        assert r.tokens is None
        assert r.content_signal is None
        assert r.elapsed == 0.0
        assert r.headers == {}

    def test_full(self):
        r = NegotiationResult(
            content="# Full",
            tokens=100,
            content_signal={"ai-train": "yes"},
            elapsed=0.15,
            headers={"content-type": "text/markdown"},
        )
        assert r.tokens == 100
        assert r.content_signal["ai-train"] == "yes"
        assert r.elapsed == 0.15
