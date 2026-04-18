"""Authenticated BFS crawler for Flarecrawl.

Provides a cookie-carrying crawler that does direct HTTP requests
(not CF Browser Rendering) for sites where you already have a session.
Follows the same concurrency pattern as batch.py (asyncio semaphore).
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from . import DEFAULT_USER_AGENT
from .extract import extract_main_content, html_to_markdown
from .frontier_v2 import RETRY_CODES, Frontier, FrontierItem
from .ratelimit import DomainRateLimiter
from .robots import RobotsCache
from . import shutdown as _shutdown
from .journal import emit_event
from .telemetry import start_span

logger = logging.getLogger(__name__)


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
    # When True, skip robots.txt checks for every URL. CLI exposes this
    # via --ignore-robots on ``crawl`` / ``download``.
    ignore_robots: bool = False
    # Resume an existing Frontier job. When set, ``Frontier.open`` is
    # invoked with ``resume=True`` and the seed URL is not re-queued.
    resume_job_id: str | None = None
    # Per-URL attempt cap before a row transitions to ``dead``.
    max_attempts: int = 3
    # When True, DomainRegistry snoozes each host after every OK fetch
    # using an EWMA-based delay (instead of the fixed ``delay`` below).
    adaptive_delay: bool = False
    # Days until a ``visited`` row is considered stale and eligible for
    # a revalidation fetch (used by weekly delta refresh jobs).
    refresh_days: int = 7


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

    def __init__(self, config: CrawlConfig, robots: RobotsCache | None = None):
        self._config = config
        self._session: httpx.AsyncClient | None = None
        # Per-host rate limiter — guards HTTP fetches so one domain cannot
        # starve another and we stay within polite-crawling bounds.
        self._rate_limiter: DomainRateLimiter | None = (
            DomainRateLimiter(rate=config.rate_limit, per=1.0)
            if config.rate_limit and config.rate_limit > 0
            else None
        )
        # Lazy-import to avoid circular and to keep robots optional.
        if robots is None and not config.ignore_robots:
            robots = RobotsCache(
                user_agent=config.user_agent or DEFAULT_USER_AGENT
            )
        self._robots: RobotsCache | None = robots

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

    def _generate_job_id(self) -> str:
        """Generate a new frontier job id (host-prefixed UUID4 short)."""
        host = urlparse(self._config.seed_url).hostname or "job"
        return f"authcrawl-{host}-{uuid.uuid4().hex[:8]}"

    async def _seed_sitemap(
        self, frontier: Frontier, session: httpx.AsyncClient
    ) -> None:
        """Best-effort sitemap-first seeding.

        Failures are swallowed — sitemap absence is the common case.
        """
        try:
            from .sitemap import discover_sitemap_urls

            ua = self._config.user_agent or DEFAULT_USER_AGENT
            entries = await discover_sitemap_urls(
                self._config.seed_url, client=session, user_agent=ua
            )
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug("sitemap discovery failed: %r", exc)
            return
        for entry in entries:
            if not _should_crawl(
                entry.url,
                self._config.seed_url,
                self._config.include_patterns,
                self._config.exclude_patterns,
            ):
                continue
            try:
                await frontier.queue.add(
                    entry.url,
                    depth=0,
                    priority=5,
                    max_attempts=self._config.max_attempts,
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("sitemap add failed: %r", exc)

    async def _seed_frontier(
        self, frontier: Frontier, session: httpx.AsyncClient
    ) -> None:
        """Seed the frontier with the initial URL + sitemap discovery.

        Only called on fresh jobs (not resume). Extracted from ``crawl``.
        """
        cfg = self._config
        await frontier.queue.add(
            cfg.seed_url,
            depth=0,
            priority=10,
            max_attempts=cfg.max_attempts,
        )
        await self._seed_sitemap(frontier, session)

    async def _prefilter_batch(
        self,
        batch: list[FrontierItem],
        session: httpx.AsyncClient,
        frontier: Frontier,
    ) -> list[FrontierItem]:
        """Filter a scheduled batch down to items safe to fetch.

        Applies (in order): max-depth prune, same-origin / include-exclude
        pattern check, and robots.txt gate. Each filtered-out item is
        marked on the frontier (``mark_skipped`` for policy, ``mark_dead``
        with reason ``"robots"`` for robots-denied).
        """
        cfg = self._config
        runnable: list[FrontierItem] = []
        for item in batch:
            if item.depth > cfg.max_depth:
                await frontier.queue.mark_skipped(item.fp)
                continue
            if not _should_crawl(
                item.url,
                cfg.seed_url,
                cfg.include_patterns,
                cfg.exclude_patterns,
            ):
                await frontier.queue.mark_skipped(item.fp)
                continue
            if self._robots is not None and not cfg.ignore_robots:
                ua = cfg.user_agent or DEFAULT_USER_AGENT
                try:
                    allowed = await self._robots.can_fetch(
                        item.url, ua, client=session
                    )
                except (
                    httpx.HTTPError,
                    httpx.InvalidURL,
                    ValueError,
                    AttributeError,
                ) as exc:
                    logger.debug(
                        "robots.can_fetch fallback for %s: %r",
                        item.url,
                        exc,
                    )
                    allowed = True
                if not allowed:
                    await frontier.queue.mark_dead(item.fp, "robots")
                    continue
            runnable.append(item)
        return runnable

    async def crawl(self) -> AsyncIterator[CrawlResult]:
        """BFS crawl from seed_url, yielding CrawlResult per page.

        Frontier-backed: dedup, per-host round-robin, retries, adaptive
        delay, and resume are delegated to :class:`Frontier`. The async
        iterator contract is preserved — each yielded :class:`CrawlResult`
        represents one fetched (or 304-short-circuited) page.
        """
        cfg = self._config
        resuming = cfg.resume_job_id is not None
        job_id = cfg.resume_job_id or self._generate_job_id()
        # Install shutdown handlers for this crawl. Best-effort.
        try:
            _shutdown.install_signal_handlers()
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug("shutdown handler install failed: %r", exc)
        page_count = 0
        # Counters for the completion event.
        ok_count = 0
        dead_count = 0
        unchanged_count = 0
        target_host = urlparse(cfg.seed_url).hostname or cfg.seed_url
        _t_start = time.monotonic()
        emit_event(
            "started",
            target=target_host,
            msg=f"job_id={job_id}",
        )
        _interrupted = False
        _exception: BaseException | None = None
        try:
            async with self._build_session() as session:
                async with await Frontier.open(
                    job_id,
                    resume=resuming,
                    adaptive_mode=cfg.adaptive_delay,
                ) as frontier:
                    if not resuming:
                        await self._seed_frontier(frontier, session)

                    while page_count < cfg.max_pages:
                        if _shutdown.is_shutdown_requested():
                            print(
                                _shutdown.resume_hint(job_id),
                                file=sys.stderr,
                                flush=True,
                            )
                            _interrupted = True
                            break
                        with start_span(
                            "schedule",
                            **{
                                "flarecrawl.phase": "schedule",
                                "flarecrawl.job_id": job_id,
                                "batch_size": cfg.workers,
                            },
                        ):
                            batch = await frontier.queue.next_batch(cfg.workers)
                        if not batch:
                            break

                        # Pre-filter batch: robots + same-origin/patterns.
                        runnable = await self._prefilter_batch(
                            batch, session, frontier
                        )

                        if not runnable:
                            # All items skipped/blocked — keep looping.
                            continue

                        semaphore = asyncio.Semaphore(cfg.workers)
                        tasks = [
                            self._fetch_item(session, item, frontier, semaphore, job_id)
                            for item in runnable
                        ]
                        results = await asyncio.gather(
                            *tasks, return_exceptions=True
                        )

                        for result in results:
                            if isinstance(result, BaseException):
                                continue
                            if result is None:
                                # 304 Not Modified — counted as unchanged.
                                unchanged_count += 1
                                continue
                            page_count += 1
                            if result.error is None:
                                ok_count += 1
                            else:
                                dead_count += 1
                            yield result
                            if page_count >= cfg.max_pages:
                                break

                            if result.error is None and result.depth < cfg.max_depth:
                                for link in result.links_found:
                                    if _should_crawl(
                                        link,
                                        cfg.seed_url,
                                        cfg.include_patterns,
                                        cfg.exclude_patterns,
                                    ):
                                        try:
                                            await frontier.queue.add(
                                                link,
                                                depth=result.depth + 1,
                                                max_attempts=cfg.max_attempts,
                                            )
                                        except Exception as exc:  # pragma: no cover
                                            logger.debug(
                                                "queue.add failed: %r", exc
                                            )

                        await frontier.maybe_checkpoint()

                        if cfg.delay > 0 and not cfg.adaptive_delay:
                            await asyncio.sleep(cfg.delay)
        except BaseException as exc:
            _exception = exc
            raise
        finally:
            duration_ms = int((time.monotonic() - _t_start) * 1000)
            counts = {
                "urls": ok_count,
                "dead": dead_count,
                "unchanged": unchanged_count,
            }
            if _exception is not None and not isinstance(_exception, GeneratorExit):
                emit_event(
                    "failed",
                    target=target_host,
                    level="error",
                    duration_ms=duration_ms,
                    counts=counts,
                    msg=str(_exception),
                )
            elif _interrupted:
                emit_event(
                    "interrupted",
                    target=target_host,
                    level="warn",
                    duration_ms=duration_ms,
                    counts={**counts, "in_flight_rolled_back": 0},
                )
            else:
                emit_event(
                    "completed",
                    target=target_host,
                    duration_ms=duration_ms,
                    counts=counts,
                )

    def _error_result(
        self, item: FrontierItem, elapsed_s: float, err: str
    ) -> CrawlResult:
        """Build a ``CrawlResult`` for a failed fetch.

        ``elapsed_s`` is expected in **seconds** (already rounded or
        not — we round to 2dp here). ``err`` is the terminal error
        label that callers would otherwise paste verbatim into the
        result. Keeps the five near-identical failure branches of
        ``_fetch_item_body`` from drifting.
        """
        return CrawlResult(
            url=item.url,
            depth=item.depth,
            content=None,
            content_type="",
            links_found=[],
            elapsed=round(elapsed_s, 2),
            error=err,
        )

    async def _fetch_item(
        self,
        session: httpx.AsyncClient,
        item: FrontierItem,
        frontier: Frontier,
        semaphore: asyncio.Semaphore,
        job_id: str | None = None,
    ) -> CrawlResult | None:
        """Fetch one FrontierItem, update frontier state, return CrawlResult.

        Returns None when the fetch was a 304 Not Modified (nothing new to
        yield to the caller) or when a retry/dead transition fires.
        """
        cfg = self._config
        with start_span(
            "fetch",
            **{
                "flarecrawl.phase": "fetch",
                "flarecrawl.job_id": job_id or "",
                "url.domain": item.hostname or "",
            },
        ) as _fetch_span:
            return await self._fetch_item_body(
                session, item, frontier, semaphore, job_id, cfg, _fetch_span
            )

    async def _fetch_item_body(
        self,
        session: httpx.AsyncClient,
        item: FrontierItem,
        frontier: Frontier,
        semaphore: asyncio.Semaphore,
        job_id: str | None,
        cfg: CrawlConfig,
        _fetch_span,
    ) -> CrawlResult | None:
        """Body of ``_fetch_item`` — extracted so the tracing span can
        wrap it as a single ``with`` block without rewriting control
        flow."""
        async with semaphore:
            # Merge conditional headers from VisitedStore.
            cond = await frontier.visited.conditional_headers(item.fp)
            headers = dict(cond) if cond else {}
            start = time.monotonic()
            try:
                if self._rate_limiter is not None:
                    async with self._rate_limiter.for_url(item.url):
                        resp = await session.get(item.url, headers=headers or None)
                else:
                    resp = await session.get(item.url, headers=headers or None)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                await frontier.queue.mark_retry(item.fp, type(exc).__name__)
                await frontier.domains.bump_fail(item.hostname)
                return self._error_result(
                    item, time.monotonic() - start, type(exc).__name__
                )
            except httpx.HTTPError as exc:
                await frontier.queue.mark_dead(item.fp, repr(exc))
                await frontier.domains.bump_fail(item.hostname)
                return self._error_result(
                    item, time.monotonic() - start, str(exc)
                )
            except Exception as exc:  # pragma: no cover — defensive
                await frontier.queue.mark_dead(item.fp, repr(exc))
                await frontier.domains.bump_fail(item.hostname)
                return self._error_result(
                    item, time.monotonic() - start, repr(exc)
                )

            elapsed_ms = (time.monotonic() - start) * 1000.0
            sc = resp.status_code
            try:
                _fetch_span.set_attribute("http.status", sc)
                _fetch_span.set_attribute("response_ms", elapsed_ms)
            except Exception:  # pragma: no cover — no-op span path
                pass
            etag = resp.headers.get("etag")
            last_modified = resp.headers.get("last-modified")
            content_len = len(resp.content or b"")

            # 304 Not Modified — fast-path, no re-fetch yielded.
            if sc == 304:
                await frontier.queue.mark_done(item.fp)
                await frontier.visited.touch_unchanged(item.fp)
                await frontier.domains.observe(
                    item.hostname,
                    ok=True,
                    response_ms=elapsed_ms,
                    bytes_received=0,
                )
                return None

            # Retry class — 408/429/5xx/522/524.
            if sc in RETRY_CODES:
                retry_after = resp.headers.get("retry-after")
                if retry_after is not None:
                    try:
                        await frontier.domains.snooze(
                            item.hostname, float(retry_after)
                        )
                    except (TypeError, ValueError):
                        pass
                await frontier.queue.mark_retry(item.fp, f"http_{sc}")
                await frontier.domains.bump_fail(item.hostname)
                return self._error_result(item, elapsed_ms / 1000.0, f"http_{sc}")

            # Other 4xx — terminal dead.
            if 400 <= sc < 500:
                await frontier.queue.mark_dead(item.fp, f"http_{sc}")
                await frontier.domains.observe(
                    item.hostname,
                    ok=False,
                    response_ms=elapsed_ms,
                    bytes_received=content_len,
                )
                return self._error_result(item, elapsed_ms / 1000.0, f"http_{sc}")

            # 2xx / 3xx success.
            ct = resp.headers.get("content-type", "text/html").split(";")[0].strip()
            html = resp.text
            with start_span(
                "parse",
                **{
                    "flarecrawl.phase": "parse",
                    "flarecrawl.job_id": job_id or "",
                    "url.domain": item.hostname or "",
                },
            ):
                links = _extract_links(html, item.url) if "html" in ct else []

            if cfg.format == "html":
                content = html
            elif cfg.format == "markdown":
                main_html = extract_main_content(html)
                content = html_to_markdown(main_html)
            else:
                content = html

            # Record visited with revalidation metadata.
            next_refresh_at = (
                time.time() + cfg.refresh_days * 86400.0
                if cfg.refresh_days and cfg.refresh_days > 0
                else None
            )
            await frontier.visited.record(
                item.fp,
                item.url,
                status_code=sc,
                etag=etag,
                last_modified=last_modified,
                next_refresh_at=next_refresh_at,
            )
            await frontier.queue.mark_done(item.fp)
            await frontier.domains.observe(
                item.hostname,
                ok=True,
                response_ms=elapsed_ms,
                bytes_received=content_len,
            )

            return CrawlResult(
                url=item.url,
                depth=item.depth,
                content=content,
                content_type=ct,
                links_found=links,
                elapsed=round(elapsed_ms / 1000.0, 2),
            )
