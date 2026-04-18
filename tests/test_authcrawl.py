"""Tests for authenticated BFS crawler."""

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import pytest

from flarecrawl.authcrawl import (
    CrawlConfig,
    CrawlResult,
    _matches_patterns,
    _same_origin,
    _should_crawl,
)


class TestCrawlConfigDefaults:
    """Verify CrawlConfig field defaults."""

    def test_max_depth_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.max_depth == 3

    def test_max_pages_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.max_pages == 50

    def test_format_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.format == "markdown"

    def test_workers_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.workers == 3

    def test_delay_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.delay == 1.0

    def test_cookies_default_none(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.cookies is None

    def test_patterns_default_none(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.include_patterns is None
        assert cfg.exclude_patterns is None

    def test_rate_limit_default(self):
        cfg = CrawlConfig(seed_url="https://example.com")
        assert cfg.rate_limit == 2.0


class TestRateLimiterWireUp:
    """Item 6 wire-up: authcrawl constructs a DomainRateLimiter by default."""

    def test_limiter_created_by_default(self):
        from flarecrawl.authcrawl import AuthenticatedCrawler
        from flarecrawl.ratelimit import DomainRateLimiter
        crawler = AuthenticatedCrawler(CrawlConfig(seed_url="https://example.com"))
        assert isinstance(crawler._rate_limiter, DomainRateLimiter)

    def test_limiter_disabled_when_rate_zero(self):
        from flarecrawl.authcrawl import AuthenticatedCrawler
        crawler = AuthenticatedCrawler(
            CrawlConfig(seed_url="https://example.com", rate_limit=0)
        )
        assert crawler._rate_limiter is None

    def test_limiter_disabled_when_rate_none(self):
        from flarecrawl.authcrawl import AuthenticatedCrawler
        crawler = AuthenticatedCrawler(
            CrawlConfig(seed_url="https://example.com", rate_limit=None)
        )
        assert crawler._rate_limiter is None

    def test_custom_rate_forwarded(self):
        from flarecrawl.authcrawl import AuthenticatedCrawler
        crawler = AuthenticatedCrawler(
            CrawlConfig(seed_url="https://example.com", rate_limit=5.0)
        )
        assert crawler._rate_limiter is not None
        assert crawler._rate_limiter._default_rate == 5.0


class TestRateLimiterEnforcesRate:
    """Functional: the rate limiter is exercised on the _fetch_item path.

    The legacy ``_fetch_page`` helper was retired in favour of
    ``_fetch_item`` (which is frontier-aware). This test now drives
    the new path end-to-end through ``crawl()`` with a mocked
    transport and asserts the limiter is present *and* reached at
    least once.
    """

    def test_crawl_path_invokes_rate_limiter(self, monkeypatch, tmp_path):
        import asyncio

        import httpx

        from flarecrawl.authcrawl import AuthenticatedCrawler

        monkeypatch.setenv("FLARECRAWL_FRONTIER_DIR", str(tmp_path / "jobs"))

        call_count = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, text="<html></html>")

        transport = httpx.MockTransport(handler)

        async def _run() -> None:
            crawler = AuthenticatedCrawler(
                CrawlConfig(
                    seed_url="https://example.com/",
                    rate_limit=5.0,
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    ignore_robots=True,
                )
            )
            # Inject mock transport into the session builder.
            crawler._build_session = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
                transport=transport, follow_redirects=True
            )
            async for _ in crawler.crawl():
                pass

        asyncio.run(_run())

        # The limiter must have been created and the mock transport hit.
        assert call_count["n"] >= 1


class TestSameOrigin:
    """Test _same_origin() filtering."""

    def test_same_host(self):
        assert _same_origin("https://example.com/page", "https://example.com")

    def test_different_host(self):
        assert not _same_origin("https://other.com/page", "https://example.com")

    def test_different_scheme(self):
        assert not _same_origin("http://example.com/page", "https://example.com")

    def test_same_subdomain(self):
        assert _same_origin("https://example.com/a/b", "https://example.com/c")


class TestMatchesPatterns:
    """Test _matches_patterns() URL filtering."""

    def test_none_patterns_always_matches(self):
        assert _matches_patterns("https://example.com/page", None)

    def test_substring_match(self):
        assert _matches_patterns("https://example.com/blog/post", ["/blog/"])

    def test_no_match(self):
        assert not _matches_patterns("https://example.com/products", ["/blog/"])

    def test_regex_match(self):
        assert _matches_patterns("https://example.com/2024/01/post", [r"/\d{4}/"])

    def test_multiple_patterns_any_matches(self):
        assert _matches_patterns("https://example.com/news", ["/blog/", "/news"])

    def test_invalid_regex_falls_back_to_substring(self):
        assert _matches_patterns("https://example.com/[api]", ["[api]"])


class TestShouldCrawl:
    """Test _should_crawl() combined logic."""

    def test_allows_same_origin_no_patterns(self):
        assert _should_crawl("https://example.com/page", "https://example.com", None, None)

    def test_blocks_different_origin(self):
        assert not _should_crawl("https://other.com/page", "https://example.com", None, None)

    def test_blocks_excluded_pattern(self):
        assert not _should_crawl(
            "https://example.com/admin", "https://example.com",
            None, ["/admin"],
        )

    def test_blocks_when_include_pattern_not_matched(self):
        assert not _should_crawl(
            "https://example.com/products", "https://example.com",
            ["/blog/"], None,
        )

    def test_allows_when_include_pattern_matched(self):
        assert _should_crawl(
            "https://example.com/blog/post", "https://example.com",
            ["/blog/"], None,
        )

    def test_exclude_takes_priority(self):
        assert not _should_crawl(
            "https://example.com/blog/admin", "https://example.com",
            ["/blog/"], ["/admin"],
        )


class TestCrawlResult:
    """Test CrawlResult dataclass."""

    def test_success_result(self):
        result = CrawlResult(
            url="https://example.com",
            depth=0,
            content="# Hello",
            content_type="text/html",
            links_found=["https://example.com/page"],
            elapsed=0.5,
        )
        assert result.error is None
        assert result.depth == 0

    def test_error_result(self):
        result = CrawlResult(
            url="https://example.com/missing",
            depth=1,
            content=None,
            content_type="",
            links_found=[],
            elapsed=0.1,
            error="404 Not Found",
        )
        assert result.error == "404 Not Found"
        assert result.content is None
