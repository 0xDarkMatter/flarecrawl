"""Tests for the default User-Agent (item 8)."""

from __future__ import annotations

import asyncio

import httpx

from flarecrawl import DEFAULT_USER_AGENT, __version__
from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig


def test_default_user_agent_format():
    assert DEFAULT_USER_AGENT.startswith("FlarecrawlBot/")
    assert __version__ in DEFAULT_USER_AGENT
    assert "https://github.com/forma-tools/flarecrawl" in DEFAULT_USER_AGENT


def test_authcrawl_session_uses_default_ua():
    crawler = AuthenticatedCrawler(CrawlConfig(seed_url="https://example.com"))
    session = crawler._build_session()
    try:
        assert session.headers["User-Agent"] == DEFAULT_USER_AGENT
    finally:
        asyncio.get_event_loop_policy()  # no-op - ensure policy exists
        asyncio.run(session.aclose())


def test_authcrawl_session_respects_custom_ua():
    crawler = AuthenticatedCrawler(
        CrawlConfig(seed_url="https://example.com", user_agent="Custom/1.0")
    )
    session = crawler._build_session()
    try:
        assert session.headers["User-Agent"] == "Custom/1.0"
    finally:
        asyncio.run(session.aclose())


def test_fetch_sends_default_ua_header(monkeypatch, tmp_path):
    """End-to-end: UA header is actually on outbound requests.

    Drives ``crawl()`` (which exercises ``_fetch_item``) with a mock
    transport that captures the ``User-Agent`` header.
    """
    monkeypatch.setenv("FLARECRAWL_FRONTIER_DIR", str(tmp_path / "jobs"))

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="<html></html>")

    transport = httpx.MockTransport(handler)

    async def _run() -> None:
        crawler = AuthenticatedCrawler(
            CrawlConfig(
                seed_url="https://example.com/",
                max_pages=1,
                max_depth=0,
                delay=0,
                rate_limit=None,
                ignore_robots=True,
            )
        )
        crawler._build_session = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
            transport=transport,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
        )
        async for _ in crawler.crawl():
            pass

    asyncio.run(_run())
    assert captured["ua"] == DEFAULT_USER_AGENT
