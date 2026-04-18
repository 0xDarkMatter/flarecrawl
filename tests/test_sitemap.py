"""Tests for sitemap-first discovery (item 12)."""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import asyncio

import httpx

from flarecrawl.sitemap import (
    SitemapEntry,
    discover_sitemap_urls,
    parse_sitemap_xml,
    sitemap_urls_from_robots,
)


SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/a</loc>
    <lastmod>2025-01-01</lastmod>
  </url>
  <url>
    <loc>https://example.com/b</loc>
  </url>
</urlset>
""".strip()

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sm1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sm2.xml</loc></sitemap>
</sitemapindex>
""".strip()


def test_parse_urlset():
    entries = parse_sitemap_xml(SITEMAP_XML)
    assert entries == [
        SitemapEntry("https://example.com/a", "2025-01-01"),
        SitemapEntry("https://example.com/b", None),
    ]


def test_parse_sitemap_index():
    entries = parse_sitemap_xml(SITEMAP_INDEX)
    urls = {e.url for e in entries}
    assert urls == {"https://example.com/sm1.xml", "https://example.com/sm2.xml"}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sitemap_urls_from_robots():
    robots = "User-agent: *\nSitemap: https://example.com/sitemap.xml\nSitemap: https://example.com/other.xml\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(404)

    async def run():
        async with _client(handler) as c:
            return await sitemap_urls_from_robots("https://example.com/", c)

    urls = asyncio.run(run())
    assert urls == [
        "https://example.com/sitemap.xml",
        "https://example.com/other.xml",
    ]


def test_discover_uses_fallback():
    """With no Sitemap: in robots, /sitemap.xml is tried."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP_XML)
        return httpx.Response(404)

    async def run():
        async with _client(handler) as c:
            return await discover_sitemap_urls("https://example.com/", client=c)

    entries = asyncio.run(run())
    assert {e.url for e in entries} == {
        "https://example.com/a",
        "https://example.com/b",
    }


def test_discover_follows_sitemap_index():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="Sitemap: https://example.com/index.xml\n")
        if path == "/index.xml":
            return httpx.Response(200, text=SITEMAP_INDEX)
        if path == "/sm1.xml":
            return httpx.Response(200, text=SITEMAP_XML)
        if path == "/sm2.xml":
            return httpx.Response(
                200,
                text=SITEMAP_XML.replace(
                    "https://example.com/a", "https://example.com/c"
                ).replace(
                    "https://example.com/b", "https://example.com/d"
                ),
            )
        return httpx.Response(404)

    async def run():
        async with _client(handler) as c:
            return await discover_sitemap_urls("https://example.com/", client=c)

    entries = asyncio.run(run())
    urls = {e.url for e in entries}
    assert urls == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/d",
    }


# ---------------------------------------------------------------------------
# Response size cap (S3)
# ---------------------------------------------------------------------------
def test_sitemap_oversize_content_length_skipped():
    from flarecrawl.sitemap import MAX_SITEMAP_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        # Pretend the sitemap is bigger than the cap.
        return httpx.Response(
            200,
            text=SITEMAP_XML,
            headers={"content-length": str(MAX_SITEMAP_BYTES + 1)},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            entries = await discover_sitemap_urls(
                "https://example.com", client=c, follow_index=False
            )
            assert entries == []

    asyncio.run(run())


def test_sitemap_within_cap_parses_normally():
    from flarecrawl.sitemap import MAX_SITEMAP_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=SITEMAP_XML,
            headers={"content-length": str(MAX_SITEMAP_BYTES - 1)},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            entries = await discover_sitemap_urls(
                "https://example.com", client=c, follow_index=False
            )
            assert len(entries) == 2

    asyncio.run(run())
