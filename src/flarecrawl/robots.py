"""robots.txt handling via protego (item 7).

Per-hostname cache (TTL 1 hour). Graceful fallback to allow-all when
``protego`` is not installed — logs a single warning and lets callers
continue without blocking. Callers that want to bypass entirely pass
``ignore_robots=True`` from the CLI.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from . import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

try:
    from protego import Protego  # type: ignore[import-untyped]

    _PROTEGO_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via tests/test_robots_fallback
    Protego = None  # type: ignore[assignment,misc]
    _PROTEGO_AVAILABLE = False

_DEFAULT_TTL = 3600.0  # 1h
_FETCH_TIMEOUT = 10.0
_FALLBACK_LOGGED = False

#: Hard cap on robots.txt response body. The spec-bound upper limit
#: (Google's reference implementation) is 500 KiB, but real-world
#: servers occasionally return multi-megabyte files. 2 MiB is a
#: generous ceiling that still protects the client from unbounded
#: memory growth on a hostile origin.
MAX_ROBOTS_BYTES = 2 * 1024 * 1024


def _warn_fallback_once() -> None:
    global _FALLBACK_LOGGED
    if not _FALLBACK_LOGGED:
        logger.warning(
            "protego is not installed; robots.txt will allow all URLs. "
            "Install with: pip install 'flarecrawl[perf]'"
        )
        _FALLBACK_LOGGED = True


@dataclass(slots=True)
class _Entry:
    parser: Any  # Protego instance, or None on fetch failure
    fetched_at: float


@dataclass(slots=True)
class RobotsCache:
    """Per-hostname robots.txt cache.

    Parameters
    ----------
    user_agent:
        UA used for conditional 'can_fetch' probes and for the actual
        robots.txt fetch header.
    ttl:
        Seconds to retain a parsed entry before re-fetching.
    """

    user_agent: str = DEFAULT_USER_AGENT
    ttl: float = _DEFAULT_TTL
    _cache: dict[str, _Entry] = field(default_factory=dict)

    async def _fetch_and_parse(
        self, origin: str, client: httpx.AsyncClient
    ) -> Any:
        url = f"{origin}/robots.txt"
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
            )
        except (httpx.HTTPError, httpx.InvalidURL):
            # Network error → treat as no robots.txt (allow-all).
            return None
        if resp.status_code >= 400:
            # 4xx/5xx on robots.txt → allow-all per polite-crawler convention.
            return None
        # Oversized body → allow-all rather than risk an OOM.
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_ROBOTS_BYTES:
                    logger.debug(
                        "robots.txt at %s exceeds cap (%s bytes); skipping",
                        url,
                        cl,
                    )
                    return None
            except ValueError:
                pass
        if not _PROTEGO_AVAILABLE:
            return None
        try:
            return Protego.parse(resp.text)
        except Exception:  # pragma: no cover — defensive, protego is lenient
            return None

    def _origin(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    async def _get(
        self, url: str, client: httpx.AsyncClient | None = None
    ) -> Any:
        origin = self._origin(url)
        now = time.monotonic()
        entry = self._cache.get(origin)
        if entry is not None and (now - entry.fetched_at) < self.ttl:
            return entry.parser
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient()
        try:
            parser = await self._fetch_and_parse(origin, client)
        finally:
            if owns_client:
                await client.aclose()
        self._cache[origin] = _Entry(parser=parser, fetched_at=now)
        return parser

    async def can_fetch(
        self, url: str, user_agent: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> bool:
        """Return True if robots.txt allows ``url`` for ``user_agent``.

        Allow-all when protego is missing, fetch fails, or no robots.txt
        is served.
        """
        if not _PROTEGO_AVAILABLE:
            _warn_fallback_once()
            return True
        parser = await self._get(url, client=client)
        if parser is None:
            return True
        ua = user_agent or self.user_agent
        try:
            return bool(parser.can_fetch(url, ua))
        except Exception:
            return True

    async def get_crawl_delay(
        self, url: str, user_agent: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> float | None:
        """Return the crawl-delay hint for ``url`` / ``user_agent``, if any."""
        if not _PROTEGO_AVAILABLE:
            return None
        parser = await self._get(url, client=client)
        if parser is None:
            return None
        ua = user_agent or self.user_agent
        try:
            delay = parser.crawl_delay(ua)
        except Exception:
            return None
        if delay is None:
            return None
        try:
            return float(delay)
        except (TypeError, ValueError):
            return None


# Convenience top-level functions -----------------------------------------

_DEFAULT_CACHE: RobotsCache | None = None


def _default_cache() -> RobotsCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = RobotsCache()
    return _DEFAULT_CACHE


async def can_fetch(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Module-level convenience wrapper over the default cache."""
    return await _default_cache().can_fetch(url, user_agent, client)


async def get_crawl_delay(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    client: httpx.AsyncClient | None = None,
) -> float | None:
    return await _default_cache().get_crawl_delay(url, user_agent, client)
