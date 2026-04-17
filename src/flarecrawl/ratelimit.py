"""Per-domain rate limiting.

Thin wrapper around :class:`aiolimiter.AsyncLimiter` that partitions limits by
URL hostname. If ``aiolimiter`` is not installed, falls back to a
functionally-equivalent pure-asyncio token-bucket implementation so the module
works in the base install.

Typical use inside a crawler::

    limiter = DomainRateLimiter(rate=2, per=1.0)   # 2 req/s per host
    async with limiter.for_url(url):
        response = await client.get(url)

The per-host limiter is created lazily on first touch and cached. Call
:meth:`set_rate` to adjust a single host's rate at runtime (e.g. after reading
``Crawl-delay`` from ``robots.txt``).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

try:
    from aiolimiter import AsyncLimiter as _AioLimiter  # type: ignore[import-not-found]

    _HAS_AIOLIMITER = True
except ImportError:  # pragma: no cover - exercised when aiolimiter missing
    _AioLimiter = None  # type: ignore[assignment]
    _HAS_AIOLIMITER = False


class _FallbackLimiter:
    """Minimal token-bucket limiter used when aiolimiter is not installed.

    API-compatible with ``aiolimiter.AsyncLimiter`` for the subset we use:
    it is an async context manager whose body will block until a token is
    available, regenerating tokens at ``max_rate / time_period`` per second.
    """

    __slots__ = ("_capacity", "_refill_rate", "_tokens", "_last", "_lock")

    def __init__(self, max_rate: float, time_period: float = 1.0) -> None:
        self._capacity = float(max_rate)
        self._refill_rate = float(max_rate) / float(time_period)
        self._tokens = float(max_rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._refill_rate,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._refill_rate
            await asyncio.sleep(wait)

    async def __aenter__(self) -> "_FallbackLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _host_of(url: str) -> str:
    """Extract lowercased hostname from a URL. Empty string on parse failure."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


class DomainRateLimiter:
    """Keyed-by-hostname rate limiter.

    Each host gets an independent limiter so one slow domain cannot starve
    another. Safe to share across tasks; individual limiters are created under
    a lock to avoid a thundering-herd on first touch.
    """

    def __init__(self, rate: float = 2.0, per: float = 1.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if per <= 0:
            raise ValueError("per must be positive")
        self._default_rate = rate
        self._default_per = per
        self._limiters: dict[str, object] = {}
        self._rates: dict[str, tuple[float, float]] = defaultdict(
            lambda: (rate, per)
        )
        self._lock = asyncio.Lock()

    def _make_limiter(self, rate: float, per: float) -> object:
        if _HAS_AIOLIMITER:
            return _AioLimiter(rate, time_period=per)  # type: ignore[misc]
        return _FallbackLimiter(rate, per)

    async def _get(self, host: str) -> object:
        lim = self._limiters.get(host)
        if lim is not None:
            return lim
        async with self._lock:
            lim = self._limiters.get(host)
            if lim is None:
                rate, per = self._rates.get(host, (self._default_rate, self._default_per))
                lim = self._make_limiter(rate, per)
                self._limiters[host] = lim
            return lim

    def set_rate(self, host: str, rate: float, per: float = 1.0) -> None:
        """Override the rate for ``host``. Takes effect on the next limiter creation.

        If the limiter already exists, it is dropped so the next acquire builds
        a fresh one with the new rate. This is intentionally simple — rate
        changes are expected to be rare (e.g. one-time robots.txt read).
        """
        host = host.lower()
        self._rates[host] = (rate, per)
        self._limiters.pop(host, None)

    @asynccontextmanager
    async def for_url(self, url: str) -> AsyncIterator[None]:
        """Async context manager that gates entry on ``url``'s host limiter."""
        host = _host_of(url)
        limiter = await self._get(host)
        async with limiter:  # type: ignore[attr-defined]
            yield


__all__ = ["DomainRateLimiter"]
