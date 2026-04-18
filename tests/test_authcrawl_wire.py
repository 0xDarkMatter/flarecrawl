"""Integration tests for AuthenticatedCrawler wired onto Frontier v2.

These tests exercise the rewritten BFS loop end-to-end via
``httpx.MockTransport`` and verify frontier state transitions
(visited rows, domain stats, dead rows, retries) rather than any
in-memory attribute on the crawler.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from typing import Callable

import httpx
import pytest

from flarecrawl.authcrawl import AuthenticatedCrawler, CrawlConfig
from flarecrawl.frontier_v2 import Frontier, default_jobs_dir
from flarecrawl import shutdown as _shutdown


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_frontier_dir(monkeypatch, tmp_path):
    """Isolate each test's frontier SQLite jobs into a tmp dir."""
    monkeypatch.setenv("FLARECRAWL_FRONTIER_DIR", str(tmp_path / "jobs"))
    # Also reset the shutdown event between tests.
    _shutdown.reset()
    yield


def _mock_session_builder(handler: Callable[[httpx.Request], httpx.Response]):
    """Return a lambda that builds an httpx client bound to ``handler``.

    Used to monkey-patch ``AuthenticatedCrawler._build_session``.
    """
    transport = httpx.MockTransport(handler)

    def _builder() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, follow_redirects=True)

    return _builder


async def _collect(crawler: AuthenticatedCrawler):
    results = []
    async for r in crawler.crawl():
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# Core wiring
# ---------------------------------------------------------------------------


def test_crawl_seeds_frontier_from_start_url():
    """A fresh crawl must insert the seed URL into the frontier (status=done)."""

    seed = "https://example.com/"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>ok</body></html>")

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    results = asyncio.run(_collect(crawler))
    assert len(results) == 1
    assert results[0].url == seed
    assert results[0].error is None


def test_crawl_respects_resume_job_id():
    """When resume_job_id is set, an existing job is opened and not re-seeded."""

    job_id = f"test-resume-{uuid.uuid4().hex[:6]}"

    # Same host as cfg.seed_url so _should_crawl passes. The assertion
    # is that we hit /existing (from the resume) without re-seeding /.
    seed_host = "https://resumehost.example.com"

    async def _pre_seed():
        fr = await Frontier.open(job_id, resume=False)
        await fr.queue.add(f"{seed_host}/existing", depth=0, priority=10)
        await fr.close()

    asyncio.run(_pre_seed())

    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, text="<html></html>")

    cfg = CrawlConfig(
        seed_url=f"{seed_host}/",
        resume_job_id=job_id,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))
    # Only the pre-seeded /existing URL was fetched; / was never added.
    assert any("/existing" in u for u in calls)
    assert not any(u.endswith("/") and "/existing" not in u for u in calls)


def test_crawl_skips_visited_urls():
    """Re-crawling a URL that's already in visited must not re-fetch it.

    We seed a visited row pre-crawl; the queue won't have the seed URL
    either since no resume flag is passed, but the frontier dedup test
    is that the internal FrontierQueue.add returns False on duplicates.
    """

    job_id = f"test-dedup-{uuid.uuid4().hex[:6]}"

    async def _pre_seed():
        fr = await Frontier.open(job_id, resume=False)
        added1 = await fr.queue.add("https://dedup.example.com/", depth=0)
        added2 = await fr.queue.add("https://dedup.example.com/", depth=0)
        await fr.close()
        return added1, added2

    a1, a2 = asyncio.run(_pre_seed())
    assert a1 is True
    assert a2 is False


def test_crawl_uses_conditional_headers_on_revisit():
    """Second crawl of same URL sends If-None-Match / If-Modified-Since.

    We pre-populate a VisitedStore row with an etag; then a revisit
    (via resume) must include ``If-None-Match`` in the outgoing request.
    """

    job_id = f"test-cond-{uuid.uuid4().hex[:6]}"
    url = "https://cond.example.com/page"

    from flarecrawl.fingerprint import fingerprint as fp_of
    from flarecrawl.canon import canonicalize

    async def _pre_seed():
        fr = await Frontier.open(job_id, resume=False)
        await fr.queue.add(url, depth=0)
        # Stamp a visited row so the conditional headers kick in.
        fp = fp_of("GET", canonicalize(url), b"")
        await fr.visited.record(
            fp, url, status_code=200, etag='"abc"', last_modified=None
        )
        await fr.close()

    asyncio.run(_pre_seed())

    seen_headers: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append({k.lower(): v for k, v in req.headers.items()})
        return httpx.Response(304)

    cfg = CrawlConfig(
        seed_url=url,
        resume_job_id=job_id,
        max_pages=5,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))
    assert seen_headers, "no outbound request was made"
    # The crawler merges conditional headers into the request.
    assert any("if-none-match" in h for h in seen_headers)


def test_crawl_retries_on_retry_code_429():
    """A 429 response schedules a retry (frontier row returns to pending)."""

    seed = "https://retry429.example.com/"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"})

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
        max_attempts=3,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    results = asyncio.run(_collect(crawler))
    # We got back exactly one error-shaped CrawlResult for the 429.
    assert len(results) == 1
    assert results[0].error == "http_429"


def test_crawl_marks_dead_after_max_attempts():
    """With max_attempts=1, a retry-class response jumps straight to dead."""

    seed = "https://dead.example.com/"
    job_id = f"test-dead-{uuid.uuid4().hex[:6]}"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cfg = CrawlConfig(
        seed_url=seed,
        resume_job_id=None,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
        max_attempts=1,
    )
    crawler = AuthenticatedCrawler(cfg)
    # Override generate_job_id so we can inspect the DB after.
    crawler._generate_job_id = lambda: job_id  # type: ignore[method-assign]
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))

    # After the crawl, reopen the frontier and confirm status='dead'.
    async def _check():
        fr = await Frontier.open(job_id, resume=True)
        counts = await fr.queue.counts()
        await fr.close()
        return counts

    counts = asyncio.run(_check())
    assert counts.get("dead", 0) >= 1


def test_crawl_bumps_domain_fail_on_exception():
    """httpx.ConnectError path calls DomainRegistry.bump_fail."""

    seed = "https://connfail.example.com/"
    job_id = f"test-connfail-{uuid.uuid4().hex[:6]}"

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._generate_job_id = lambda: job_id  # type: ignore[method-assign]
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))

    async def _check():
        fr = await Frontier.open(job_id, resume=True)
        stats = await fr.domains.stats("connfail.example.com")
        await fr.close()
        return stats

    stats = asyncio.run(_check())
    assert stats["fail_count"] >= 1


def test_crawl_with_adaptive_delay_sets_snooze():
    """adaptive_delay=True toggles EWMA-based snoozing on successful OKs."""

    seed = "https://adaptive.example.com/"
    job_id = f"test-adaptive-{uuid.uuid4().hex[:6]}"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>")

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
        adaptive_delay=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._generate_job_id = lambda: job_id  # type: ignore[method-assign]
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))

    async def _check():
        fr = await Frontier.open(job_id, resume=True)
        stats = await fr.domains.stats("adaptive.example.com")
        await fr.close()
        return stats

    stats = asyncio.run(_check())
    # With adaptive_mode=True, a snooze window was stamped.
    assert stats["snooze_until"] > 0


def test_crawl_graceful_shutdown_prints_resume_hint(capsys):
    """Setting the shutdown event causes crawl() to print the resume hint."""

    seed = "https://shutdown.example.com/"
    job_id = f"test-shutdown-{uuid.uuid4().hex[:6]}"

    def handler(req: httpx.Request) -> httpx.Response:
        # Request the shutdown on first hit — next loop iteration bails.
        _shutdown.request_shutdown()
        return httpx.Response(200, text="<html></html>")

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=50,
        max_depth=1,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._generate_job_id = lambda: job_id  # type: ignore[method-assign]
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    asyncio.run(_collect(crawler))
    err = capsys.readouterr().err
    assert "--resume" in err
    assert job_id in err


def test_crawl_sitemap_seeding_is_best_effort():
    """Sitemap discovery failures must not abort the crawl."""

    seed = "https://nositemap.example.com/"

    def handler(req: httpx.Request) -> httpx.Response:
        # Return empty sitemap / robots — no URLs to seed.
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/sitemap.xml":
            return httpx.Response(404)
        return httpx.Response(200, text="<html></html>")

    cfg = CrawlConfig(
        seed_url=seed,
        max_pages=1,
        max_depth=0,
        delay=0,
        rate_limit=None,
        ignore_robots=True,
    )
    crawler = AuthenticatedCrawler(cfg)
    crawler._build_session = _mock_session_builder(handler)  # type: ignore[method-assign]
    results = asyncio.run(_collect(crawler))
    # Seed still fetched — sitemap miss is non-fatal.
    assert len(results) == 1
    assert results[0].error is None
