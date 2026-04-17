"""Tests for flarecrawl.ratelimit.DomainRateLimiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from flarecrawl.ratelimit import DomainRateLimiter, _host_of


def test_host_of_extracts_lowercase_hostname() -> None:
    assert _host_of("https://Example.COM/path") == "example.com"
    assert _host_of("http://a.b.c:8080/x") == "a.b.c"
    assert _host_of("not a url") == ""


def test_constructor_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        DomainRateLimiter(rate=0)
    with pytest.raises(ValueError):
        DomainRateLimiter(rate=1, per=0)


def test_single_domain_serialises_under_rate_one() -> None:
    """Two consecutive acquires on same host must be spaced by ~1s."""

    async def runner() -> float:
        limiter = DomainRateLimiter(rate=1, per=1.0)
        start = time.monotonic()
        async with limiter.for_url("https://example.com/a"):
            pass
        async with limiter.for_url("https://example.com/b"):
            pass
        return time.monotonic() - start

    elapsed = asyncio.run(runner())
    assert elapsed >= 0.8, f"second acquire should wait; elapsed={elapsed}"


def test_different_domains_do_not_block_each_other() -> None:
    async def runner() -> float:
        limiter = DomainRateLimiter(rate=1, per=1.0)

        async def acquire(url: str) -> None:
            async with limiter.for_url(url):
                pass

        start = time.monotonic()
        await asyncio.gather(
            acquire("https://a.example/"),
            acquire("https://b.example/"),
        )
        return time.monotonic() - start

    elapsed = asyncio.run(runner())
    assert elapsed < 0.5, f"different hosts should not serialise; elapsed={elapsed}"


def test_set_rate_replaces_limiter() -> None:
    async def runner() -> float:
        limiter = DomainRateLimiter(rate=100, per=1.0)
        async with limiter.for_url("https://slow.example/"):
            pass
        limiter.set_rate("slow.example", rate=1, per=1.0)
        start = time.monotonic()
        async with limiter.for_url("https://slow.example/"):
            pass
        async with limiter.for_url("https://slow.example/"):
            pass
        return time.monotonic() - start

    elapsed = asyncio.run(runner())
    assert elapsed >= 0.8, f"post set_rate should throttle; elapsed={elapsed}"
