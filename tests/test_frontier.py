"""Tests for the SQLite crawl frontier (items 9, 10, 11, 15)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flarecrawl.frontier import (
    CB_FAIL_THRESHOLD,
    CB_SICK_SECONDS,
    Frontier,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "jobs"
    d.mkdir()
    return d


# ---------------------------------------------------------------- item 9 core


def test_open_creates_db(job_dir: Path):
    async def run():
        async with await Frontier.open("job1", base_dir=job_dir) as f:
            assert (job_dir / "job1.sqlite").exists()
            counts = await f.counts()
            assert counts == {}

    _run(run())


def test_add_then_next_batch(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a.example/1", depth=0)
            await f.add("https://a.example/2", depth=1)
            items = await f.next_batch(10)
            urls = {i.url for i in items}
            assert urls == {"https://a.example/1", "https://a.example/2"}
            # Batch marks them in_flight.
            again = await f.next_batch(10)
            assert again == []

    _run(run())


def test_add_duplicate_is_idempotent(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            await f.add("https://a/", 0)
            await f.add("https://a/", 1)
            counts = await f.counts()
            assert counts.get("pending") == 1

    _run(run())


def test_mark_done_updates_visited(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            (item,) = await f.next_batch(1)
            await f.mark_done(item.url, 200, etag='"abc"', last_modified="Wed")
            counts = await f.counts()
            assert counts.get("done") == 1
            # visited row populated with conditional headers (item 11 contract).
            cur = await f._db.execute(
                "SELECT etag, last_modified, status_code FROM visited WHERE url=?",
                ("https://a/",),
            )
            row = await cur.fetchone()
            assert row == ('"abc"', "Wed", 200)

    _run(run())


def test_delta_crawl_surfaces_prior_etag(job_dir: Path):
    """Item 11: next_batch joins in etag/last_modified from a fresh resume."""
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            (item,) = await f.next_batch(1)
            await f.mark_done(item.url, 200, etag='"abc"', last_modified="Wed")
            await f.checkpoint()
        async with await Frontier.open("j", resume=True, base_dir=job_dir) as f2:
            # simulate the next crawl pass: URL added again as pending.
            await f2._db.execute(
                "INSERT OR REPLACE INTO frontier(url, depth, priority, added_at, status)"
                " VALUES(?,?,?,?, 'pending')",
                ("https://a/", 0, 0, 0),
            )
            items = await f2.next_batch(1)
            assert items and items[0].etag == '"abc"'
            assert items[0].last_modified == "Wed"

    _run(run())


def test_priority_ordering(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/low", 0, priority=0)
            await f.add("https://a/hi", 0, priority=10)
            items = await f.next_batch(1)
            assert items[0].url == "https://a/hi"

    _run(run())


def test_resume_preserves_data(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            await f.checkpoint()
        # Reopen with resume=True.
        async with await Frontier.open("j", resume=True, base_dir=job_dir) as f2:
            counts = await f2.counts()
            assert counts.get("pending") == 1

    _run(run())


def test_no_resume_wipes_data(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            await f.checkpoint()
        async with await Frontier.open("j", resume=False, base_dir=job_dir) as f2:
            counts = await f2.counts()
            assert counts == {}

    _run(run())


def test_mark_failed_and_unchanged(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://a/", 0)
            (item,) = await f.next_batch(1)
            await f.mark_failed(item.url, "boom")
            counts = await f.counts()
            assert counts.get("failed") == 1

            await f.add("https://b/", 0)
            (b,) = await f.next_batch(1)
            await f.mark_done(b.url, 200, etag='"e"')
            await f.mark_unchanged(b.url)
            counts = await f.counts()
            assert counts.get("done") == 1

    _run(run())


def test_meta_roundtrip(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.set_meta("seed", "https://a")
            assert await f.get_meta("seed") == "https://a"
            assert await f.get_meta("missing") is None

    _run(run())


# --------------------------------------------------------- item 15 circuit-br


def test_circuit_breaker_flips_sick(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            for i in range(CB_FAIL_THRESHOLD):
                url = f"https://sick.example/p{i}"
                await f.add(url, 0)
                await f.mark_failed(url)
            stats = await f.domain_stats("sick.example")
            assert stats["consecutive_fails"] >= CB_FAIL_THRESHOLD
            assert stats["sick_until"] > 0

            # Now a further add + next_batch should exclude this host.
            await f.add("https://sick.example/q", 0)
            batch = await f.next_batch(10)
            assert all("sick.example" not in b.url for b in batch)

    _run(run())


def test_circuit_breaker_recovers_on_success(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            for i in range(CB_FAIL_THRESHOLD):
                url = f"https://flake.example/p{i}"
                await f.add(url, 0)
                await f.mark_failed(url)
            ok = "https://flake.example/ok"
            await f.add(ok, 0)
            # bypass sick filter via direct mark_done after manual claim
            await f._db.execute(
                "UPDATE frontier SET status='in_flight' WHERE url = ?", (ok,)
            )
            await f.mark_done(ok, 200)
            stats = await f.domain_stats("flake.example")
            assert stats["consecutive_fails"] == 0
            assert stats["sick_until"] == 0

    _run(run())


def test_breaker_constants():
    # Guardrail: don't regress on the spec numbers.
    assert CB_FAIL_THRESHOLD == 10
    assert CB_SICK_SECONDS == 600


# ---------------------------------------------------------------- item 10 bloom


def test_bloom_dedup_pre_check(job_dir: Path):
    """Bloom short-circuits duplicate adds; SQLite never sees the second insert."""
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            url = "https://a.example/unique"
            await f.add(url, 0)
            # Bloom should report presence on the second add.
            if f._bloom is not None:
                assert url in f._bloom
            # Second add is a no-op from a counts perspective.
            await f.add(url, 0)
            counts = await f.counts()
            assert counts.get("pending") == 1

    _run(run())


def test_bloom_persists_across_reopen(job_dir: Path):
    async def run():
        async with await Frontier.open("j", base_dir=job_dir) as f:
            await f.add("https://p.example/1", 0)
            await f.checkpoint()
            from flarecrawl._bloom_io import RBLOOM_AVAILABLE
            if not RBLOOM_AVAILABLE:
                pytest.skip("rbloom not installed; persistence is no-op")
        async with await Frontier.open("j", resume=True, base_dir=job_dir) as f2:
            if f2._bloom is not None:
                assert "https://p.example/1" in f2._bloom

    _run(run())
