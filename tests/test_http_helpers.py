"""Tests for :mod:`flarecrawl._http`."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from flarecrawl._http import ensure_client, origin, polite_get


# ---------------------------------------------------------------------------
# origin()
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/a?b=1", "https://example.com"),
        ("http://example.com:8080/x", "http://example.com:8080"),
        ("https://example.com:443/", "https://example.com:443"),
        ("http://user:pw@example.com/x", "http://user:pw@example.com"),
    ],
)
def test_origin_roundtrip(url: str, expected: str) -> None:
    assert origin(url) == expected


# ---------------------------------------------------------------------------
# polite_get()
# ---------------------------------------------------------------------------
def test_polite_get_returns_none_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            r = await polite_get(
                "https://example.com/", client=c, user_agent="TestBot"
            )
            assert r is None

    asyncio.run(run())


def test_polite_get_returns_response_on_5xx() -> None:
    # 500 is NOT a None return — caller inspects status_code.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="err")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            r = await polite_get(
                "https://example.com/", client=c, user_agent="TestBot"
            )
            assert r is not None
            assert r.status_code == 500

    asyncio.run(run())


def test_polite_get_honours_content_length_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="payload", headers={"content-length": "999999"}
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            r = await polite_get(
                "https://example.com/",
                client=c,
                user_agent="TestBot",
                max_bytes=1000,
            )
            assert r is None

    asyncio.run(run())


def test_polite_get_sets_user_agent_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="ok")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as c:
            await polite_get(
                "https://example.com/", client=c, user_agent="MyBot/1.0"
            )
            assert seen["ua"] == "MyBot/1.0"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# ensure_client()
# ---------------------------------------------------------------------------
def test_ensure_client_owns_when_passed_none() -> None:
    async def run():
        async with ensure_client(None) as (c, owns):
            assert owns is True
            assert isinstance(c, httpx.AsyncClient)
            assert not c.is_closed
        # Closed on exit.
        assert c.is_closed

    asyncio.run(run())


def test_ensure_client_does_not_own_when_passed_client() -> None:
    async def run():
        outer = httpx.AsyncClient()
        try:
            async with ensure_client(outer) as (c, owns):
                assert owns is False
                assert c is outer
            # Outer client still open after context exit.
            assert not outer.is_closed
        finally:
            await outer.aclose()

    asyncio.run(run())
