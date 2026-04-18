"""Tests for delta-crawl helpers (item 11).

Re-pointed at ``flarecrawl.frontier_v2.FrontierItem`` as part of the
frontier v2 migration.
"""

from __future__ import annotations

import asyncio

import httpx

from flarecrawl.delta import conditional_headers, is_unchanged
from flarecrawl.frontier_v2 import FrontierItem


def _item(url: str, etag: str | None = None, last_modified: str | None = None) -> FrontierItem:
    return FrontierItem(
        fp=b"\x00" * 16,
        url=url,
        hostname="example",
        method="GET",
        depth=0,
        attempts=0,
        etag=etag,
        last_modified=last_modified,
    )


def test_no_prior_state_no_headers():
    assert conditional_headers(_item("https://a/")) == {}


def test_etag_only():
    assert conditional_headers(_item("https://a/", etag='"abc"')) == {
        "If-None-Match": '"abc"'
    }


def test_last_modified_only():
    assert conditional_headers(_item("https://a/", last_modified="Wed, 21 Oct")) == {
        "If-Modified-Since": "Wed, 21 Oct"
    }


def test_both_headers():
    headers = conditional_headers(
        _item("https://a/", etag='"e"', last_modified="Wed")
    )
    assert headers == {"If-None-Match": '"e"', "If-Modified-Since": "Wed"}


def test_is_unchanged_304():
    assert is_unchanged(httpx.Response(304))


def test_is_unchanged_200_false():
    assert not is_unchanged(httpx.Response(200, text="fresh"))


def test_conditional_request_returns_304():
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
            resp1 = await client.get("https://example.com/a")
            assert resp1.status_code == 200
            etag = resp1.headers["etag"]
            item = _item("https://example.com/a", etag=etag)
            resp2 = await client.get(
                "https://example.com/a", headers=conditional_headers(item)
            )
            assert is_unchanged(resp2)

    asyncio.run(run())
    assert calls == ["", '"v1"']
