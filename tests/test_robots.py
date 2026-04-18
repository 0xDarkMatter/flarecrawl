"""Tests for robots.txt cache (item 7)."""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import asyncio

import httpx
import pytest

from flarecrawl.robots import RobotsCache, _PROTEGO_AVAILABLE

SAMPLE_ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 5

User-agent: FlarecrawlBot
Disallow: /bot-only/
""".strip()


def _handler(
    counter: dict[str, int] | None = None,
    body: str = SAMPLE_ROBOTS,
    status: int = 200,
):
    def h(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if counter is not None:
                counter["n"] = counter.get("n", 0) + 1
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="<html></html>")

    return h


def _client(h) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_allow_and_deny():
    async def run():
        async with _client(_handler()) as c:
            cache = RobotsCache(user_agent="TestBot")
            assert await cache.can_fetch("https://example.com/public", client=c)
            assert not await cache.can_fetch(
                "https://example.com/private/foo", client=c
            )

    asyncio.run(run())


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_per_host_cache_single_fetch():
    counter: dict[str, int] = {}

    async def run():
        async with _client(_handler(counter)) as c:
            cache = RobotsCache()
            await cache.can_fetch("https://example.com/a", client=c)
            await cache.can_fetch("https://example.com/b", client=c)
            await cache.can_fetch("https://example.com/c", client=c)

    asyncio.run(run())
    assert counter["n"] == 1


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_crawl_delay():
    async def run():
        async with _client(_handler()) as c:
            cache = RobotsCache(user_agent="*")
            delay = await cache.get_crawl_delay("https://example.com/", "*", client=c)
            assert delay == 5.0

    asyncio.run(run())


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_user_agent_specific_rule():
    async def run():
        async with _client(_handler()) as c:
            cache = RobotsCache()
            # '*' rules don't block /bot-only, but the FlarecrawlBot rule does.
            assert await cache.can_fetch(
                "https://example.com/bot-only/x", "OtherBot", client=c
            )
            assert not await cache.can_fetch(
                "https://example.com/bot-only/x", "FlarecrawlBot", client=c
            )

    asyncio.run(run())


def test_robots_404_allows_all():
    async def run():
        async with _client(_handler(status=404, body="not found")) as c:
            cache = RobotsCache()
            assert await cache.can_fetch("https://example.com/anything", client=c)

    asyncio.run(run())


def test_robots_network_error_allows_all():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
            cache = RobotsCache()
            assert await cache.can_fetch("https://example.com/anything", client=c)

    asyncio.run(run())


def test_fallback_when_protego_missing(monkeypatch):
    """When protego is unavailable, can_fetch always returns True."""
    import flarecrawl.robots as r

    monkeypatch.setattr(r, "_PROTEGO_AVAILABLE", False)
    monkeypatch.setattr(r, "_FALLBACK_LOGGED", False, raising=False)

    async def run():
        cache = r.RobotsCache()
        # No client provided; fallback short-circuits before any fetch.
        assert await cache.can_fetch("https://example.com/private/")

    asyncio.run(run())


def test_authcrawl_constructs_robots_by_default():
    from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig

    crawler = AuthenticatedCrawler(CrawlConfig(seed_url="https://example.com"))
    assert crawler._robots is not None


def test_authcrawl_skips_robots_when_ignore_flag_set():
    from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig

    crawler = AuthenticatedCrawler(
        CrawlConfig(seed_url="https://example.com", ignore_robots=True)
    )
    assert crawler._robots is None


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_authcrawl_crawl_loop_skips_denied_urls():
    from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig
    from flarecrawl.robots import RobotsCache

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        return httpx.Response(200, text="<html><body>ok</body></html>")

    async def run() -> list[str]:
        robots = RobotsCache()
        cfg = CrawlConfig(
            seed_url="https://example.com/private/page",
            max_pages=5,
            max_depth=0,
            delay=0,
        )
        crawler = AuthenticatedCrawler(cfg, robots=robots)
        crawler._build_session = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
            transport=httpx.MockTransport(handler),
        )
        urls = []
        async for r in crawler.crawl():
            urls.append(r.url)
        return urls

    urls = asyncio.run(run())
    # Seed URL blocked by robots — nothing yielded.
    assert urls == []


# ---------------------------------------------------------------------------
# Response size cap (S3)
# ---------------------------------------------------------------------------
def _sized_handler(body: str, content_length: int, status: int = 200):
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            text=body,
            headers={"content-length": str(content_length)},
        )
    return h


def test_robots_oversize_content_length_skipped():
    from flarecrawl.robots import MAX_ROBOTS_BYTES

    async def run():
        # Claim 10 MiB via content-length header; body itself is tiny
        # but the cap check should short-circuit before parsing.
        async with _client(_sized_handler(
            body=SAMPLE_ROBOTS, content_length=MAX_ROBOTS_BYTES + 1
        )) as c:
            cache = RobotsCache(user_agent="TestBot")
            # Allow-all: nothing is restricted when fetch was skipped.
            assert await cache.can_fetch(
                "https://example.com/private/foo", client=c
            )

    asyncio.run(run())


def test_robots_within_cap_parses_normally():
    from flarecrawl.robots import MAX_ROBOTS_BYTES

    async def run():
        async with _client(_sized_handler(
            body=SAMPLE_ROBOTS, content_length=MAX_ROBOTS_BYTES - 1
        )) as c:
            cache = RobotsCache(user_agent="TestBot")
            if _PROTEGO_AVAILABLE:
                # Normal parse → /private/ disallowed.
                assert not await cache.can_fetch(
                    "https://example.com/private/foo", client=c
                )
            else:
                assert await cache.can_fetch(
                    "https://example.com/private/foo", client=c
                )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Narrowed exception handling (Q1/Q2)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_can_fetch_allows_on_parser_exception(monkeypatch):
    """When parser.can_fetch raises a narrowable error, allow (default-allow)."""
    async def run():
        async with _client(_handler()) as c:
            cache = RobotsCache(user_agent="TestBot")
            # Prime the cache.
            await cache.can_fetch("https://example.com/a", client=c)
            # Force the parser to raise an AttributeError.
            entry = list(cache._cache.values())[0]
            class _BadParser:
                def can_fetch(self, *_a, **_k):
                    raise AttributeError("boom")
            entry.parser = _BadParser()
            assert await cache.can_fetch("https://example.com/a", client=c)

    asyncio.run(run())


@pytest.mark.skipif(not _PROTEGO_AVAILABLE, reason="protego not installed")
def test_crawl_delay_none_on_parser_exception():
    async def run():
        async with _client(_handler()) as c:
            cache = RobotsCache(user_agent="TestBot")
            await cache.can_fetch("https://example.com/a", client=c)
            entry = list(cache._cache.values())[0]
            class _BadParser:
                def crawl_delay(self, *_a, **_k):
                    raise ValueError("boom")
            entry.parser = _BadParser()
            assert await cache.get_crawl_delay(
                "https://example.com/a", "TestBot", client=c
            ) is None

    asyncio.run(run())
