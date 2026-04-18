"""Authenticated BFS crawler for Flarecrawl.

Provides a cookie-carrying crawler that does direct HTTP requests
(not CF Browser Rendering) for sites where you already have a session.
Follows the same concurrency pattern as batch.py (asyncio semaphore).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from . import DEFAULT_USER_AGENT
from .extract import extract_main_content, html_to_markdown
from .ratelimit import DomainRateLimiter


@dataclass(slots=True)
class CrawlConfig:
    """Configuration for an authenticated crawl."""
    seed_url: str
    cookies: list[dict] | None = None
    max_depth: int = 3
    max_pages: int = 50
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    format: str = "markdown"
    workers: int = 3
    delay: float = 1.0
    output_dir: str | None = None
    # Per-host request rate in req/sec. ``None`` disables the limiter, which
    # is the pre-item-6 behaviour. Item 6 default is 2.0 req/s/host.
    rate_limit: float | None = 2.0
    # User-Agent header for outbound fetches. ``None`` falls back to the
    # polite FlarecrawlBot default from ``flarecrawl.DEFAULT_USER_AGENT``.
    user_agent: str | None = None


@dataclass(slots=True)
class CrawlResult:
    """Result of crawling a single page."""
    url: str
    depth: int
    content: str | None
    content_type: str
    links_found: list[str]
    elapsed: float
    error: str | None = None


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract all href links from HTML, resolved to absolute URLs."""
    tree = HTMLParser(html)
    links: list[str] = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme in ("http", "https"):
            links.append(abs_url)
    return links


def _same_origin(url: str, seed_url: str) -> bool:
    """Check that url shares the same scheme+netloc as seed_url."""
    p1 = urlparse(url)
    p2 = urlparse(seed_url)
    return p1.scheme == p2.scheme and p1.netloc == p2.netloc


def _matches_patterns(url: str, patterns: list[str] | None) -> bool:
    """Return True if url matches any pattern (regex or substring)."""
    if not patterns:
        return True
    for pat in patterns:
        try:
            if re.search(pat, url):
                return True
        except re.error:
            if pat in url:
                return True
    return False


def _should_crawl(url: str, seed_url: str,
                  include_patterns: list[str] | None,
                  exclude_patterns: list[str] | None) -> bool:
    """Decide whether to crawl a URL."""
    if not _same_origin(url, seed_url):
        return False
    if exclude_patterns and _matches_patterns(url, exclude_patterns):
        return False
    if include_patterns and not _matches_patterns(url, include_patterns):
        return False
    return True


class AuthenticatedCrawler:
    """BFS crawler that carries session cookies through every request.

    Uses a bounded asyncio semaphore for concurrency (same pattern as batch.py).
    Yields CrawlResult objects as pages are visited.
    """

    def __init__(self, config: CrawlConfig):
        self._config = config
        self._session: httpx.AsyncClient | None = None
        # Per-host rate limiter — guards HTTP fetches so one domain cannot
        # starve another and we stay within polite-crawling bounds.
        self._rate_limiter: DomainRateLimiter | None = (
            DomainRateLimiter(rate=config.rate_limit, per=1.0)
            if config.rate_limit and config.rate_limit > 0
            else None
        )

    def _build_session(self) -> httpx.AsyncClient:
        from .cookies import cookies_to_httpx
        jar = cookies_to_httpx(self._config.cookies or [])
        ua = self._config.user_agent or DEFAULT_USER_AGENT
        return httpx.AsyncClient(
            cookies=jar,
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": ua},
        )

    async def crawl(self) -> AsyncIterator[CrawlResult]:
        """BFS crawl from seed_url, yielding CrawlResult per page."""
        cfg = self._config
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(cfg.seed_url, 0)])
        semaphore = asyncio.Semaphore(cfg.workers)
        page_count = 0

        async with self._build_session() as session:
            while queue and page_count < cfg.max_pages:
                batch_items: list[tuple[str, int]] = []
                while queue and len(batch_items) < cfg.workers:
                    url, depth = queue.popleft()
                    if url in visited or depth > cfg.max_depth:
                        continue
                    visited.add(url)
                    batch_items.append((url, depth))

                if not batch_items:
                    break

                tasks = [
                    self._fetch_page(session, url, depth, semaphore)
                    for url, depth in batch_items
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, BaseException):
                        continue
                    if result is None:
                        continue
                    page_count += 1
                    yield result
                    if page_count >= cfg.max_pages:
                        break

                    if result.error is None and result.depth < cfg.max_depth:
                        for link in result.links_found:
                            if link not in visited and _should_crawl(
                                link, cfg.seed_url,
                                cfg.include_patterns, cfg.exclude_patterns,
                            ):
                                queue.append((link, result.depth + 1))

                if cfg.delay > 0:
                    await asyncio.sleep(cfg.delay)

    async def _fetch_page(
        self,
        session: httpx.AsyncClient,
        url: str,
        depth: int,
        semaphore: asyncio.Semaphore,
    ) -> CrawlResult:
        """Fetch a single page and extract content + links."""
        async with semaphore:
            start = time.time()
            try:
                if self._rate_limiter is not None:
                    async with self._rate_limiter.for_url(url):
                        resp = await session.get(url)
                else:
                    resp = await session.get(url)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "text/html").split(";")[0].strip()
                html = resp.text
                elapsed = round(time.time() - start, 2)

                links = _extract_links(html, url) if "html" in ct else []

                if self._config.format == "html":
                    content = html
                elif self._config.format == "markdown":
                    main_html = extract_main_content(html)
                    content = html_to_markdown(main_html)
                else:
                    content = html

                return CrawlResult(
                    url=url,
                    depth=depth,
                    content=content,
                    content_type=ct,
                    links_found=links,
                    elapsed=elapsed,
                )
            except httpx.HTTPError as e:
                return CrawlResult(
                    url=url,
                    depth=depth,
                    content=None,
                    content_type="",
                    links_found=[],
                    elapsed=round(time.time() - start, 2),
                    error=str(e),
                )
