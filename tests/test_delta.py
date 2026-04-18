"""Tests for delta-crawl helpers (item 11)."""

from __future__ import annotations

import asyncio

import httpx

from flarecrawl.delta import conditional_headers, is_unchanged
from flarecrawl.frontier import FrontierItem


def test_no_prior_state_no_headers():
    assert conditional_headers(FrontierItem("https://a/", 0)) == {}


def test_etag_only():
    item = FrontierItem("https://a/", 0, etag='"abc"')
    assert conditional_headers(item) == {"If-None-Match": '"abc"'}


def test_last_modified_only():
    item = FrontierItem("https://a/", 0, last_modified="Wed, 21 Oct")
    assert conditional_headers(item) == {"If-Modified-Since": "Wed, 21 Oct"}


def test_both_headers():
    item = FrontierItem("https://a/", 0, etag='"e"', last_modified="Wed")
    headers = conditional_headers(item)
    assert headers == {"If-None-Match": '"e"', "If-Modified-Since": "Wed"}


def test_is_unchanged_304():
    resp = httpx.Response(304)
    assert is_unchanged(resp)


def test_is_unchanged_200_false():
    resp = httpx.Response(200, text="fresh")
    assert not is_unchanged(resp)


def test_conditional_request_returns_304(tmp_path):
    """End-to-end: a server honouring If-None-Match replies 304."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inm = request.headers.get("if-none-match")
        calls.append(inm or "")
        if inm == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200, text="<html>fresh</html>", headers={"ETag": '"v1"'}
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # First fetch: no headers, get 200 + etag.
            resp1 = await client.get("https://example.com/a")
            assert resp1.status_code == 200
            etag = resp1.headers["etag"]
            # Second fetch with If-None-Match.
            item = FrontierItem("https://example.com/a", 0, etag=etag)
            resp2 = await client.get(
                "https://example.com/a", headers=conditional_headers(item)
            )
            assert is_unchanged(resp2)

    asyncio.run(run())
    assert calls == ["", '"v1"']
