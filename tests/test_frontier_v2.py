"""Tests for flarecrawl.frontier_v2 — role-separated crawl frontier."""

from __future__ import annotations

import pytest
pytest.importorskip("selectolax", reason="optional dep")
pytest.importorskip("aiosqlite", reason="optional dep")
import asyncio
import time
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from flarecrawl.fingerprint import fingerprint
from flarecrawl.frontier_v2 import (
    DeadLetter,
    DomainRegistry,
    Frontier,
    FrontierItem,
    FrontierQueue,
    RETRY_CODES,
    SCHEMA_VERSION,
    VisitedStore,
)

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def fr(tmp_path: Path) -> Frontier:
    f = await Frontier.open("job-test", resume=False, base_dir=tmp_path)
    try:
        yield f
    finally:
        await f.close()


@pytest_asyncio.fixture
async def fr2(tmp_path: Path) -> Frontier:
    """Secondary frontier used for adaptive-mode tests."""
    f = await Frontier.open(
        "job-adaptive", resume=False, base_dir=tmp_path, adaptive_mode=True
    )
    try:
        yield f
    finally:
        await f.close()


# ---------------------------------------------------------------------
# Group 3 — schema + open
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_tables_exist(fr: Frontier) -> None:
    cur = await fr._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {r[0] for r in await cur.fetchall()}
    assert {"frontier", "visited", "domain_stats", "domain_budget", "meta"} <= names


@pytest.mark.asyncio
async def test_meta_seeds(fr: Frontier) -> None:
    assert await fr.get_meta("frontier_schema_version") == SCHEMA_VERSION
    assert await fr.get_meta("canon_version") == "1"
    assert await fr.get_meta("fp_algo") == "blake2b-16"


@pytest.mark.asyncio
async def test_schema_version_is_2() -> None:
    assert SCHEMA_VERSION == "2"


@pytest.mark.asyncio
async def test_reopen_without_resume_wipes(tmp_path: Path) -> None:
    f1 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    await f1.queue.add("http://a.example/", depth=0)
    await f1.checkpoint()
    await f1.close()
    f2 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    counts = await f2.queue.counts()
    assert counts == {}
    await f2.close()


@pytest.mark.asyncio
async def test_reopen_with_resume_preserves(tmp_path: Path) -> None:
    f1 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    await f1.queue.add("http://a.example/", depth=0)
    await f1.checkpoint()
    await f1.close()
    f2 = await Frontier.open("j", resume=True, base_dir=tmp_path)
    counts = await f2.queue.counts()
    assert counts.get("pending") == 1
    await f2.close()


@pytest.mark.asyncio
async def test_open_returns_role_attrs(fr: Frontier) -> None:
    assert isinstance(fr.queue, FrontierQueue)
    assert isinstance(fr.visited, VisitedStore)
    assert isinstance(fr.domains, DomainRegistry)
    assert isinstance(fr.dead_letter, DeadLetter)


# ---------------------------------------------------------------------
# Group 4 — queue add / dedup
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_new_returns_true(fr: Frontier) -> None:
    assert await fr.queue.add("http://a.example/x", depth=0) is True


@pytest.mark.asyncio
async def test_add_dup_returns_false(fr: Frontier) -> None:
    assert await fr.queue.add("http://a.example/x", depth=0) is True
    assert await fr.queue.add("http://a.example/x", depth=0) is False


@pytest.mark.asyncio
async def test_tracking_params_collapse(fr: Frontier) -> None:
    a = await fr.queue.add("http://a.example/x?utm_source=a&q=1", depth=0)
    b = await fr.queue.add("http://a.example/x?utm_source=b&q=1", depth=0)
    assert a is True
    assert b is False


@pytest.mark.asyncio
async def test_case_collapse(fr: Frontier) -> None:
    a = await fr.queue.add("HTTP://A.Example.com:80/x", depth=0)
    b = await fr.queue.add("http://a.example.com/x", depth=0)
    assert a is True
    assert b is False


@pytest.mark.asyncio
async def test_fp_column_is_16_bytes(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    cur = await fr._db.execute("SELECT fp FROM frontier LIMIT 1")
    row = await cur.fetchone()
    assert row is not None
    assert isinstance(row[0], bytes)
    assert len(row[0]) == 16


@pytest.mark.asyncio
async def test_method_and_body_differentiate(fr: Frontier) -> None:
    a = await fr.queue.add("http://a.example/x", depth=0, method="GET")
    b = await fr.queue.add("http://a.example/x", depth=0, method="POST")
    c = await fr.queue.add(
        "http://a.example/x", depth=0, method="POST", body=b"alt"
    )
    assert a is True
    assert b is True
    assert c is True


# ---------------------------------------------------------------------
# Group 5 — scheduler
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_batch_round_robin(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/1", depth=0)
    await fr.queue.add("http://a.example/2", depth=0)
    await fr.queue.add("http://b.example/1", depth=0)
    await fr.queue.add("http://b.example/2", depth=0)
    batch = await fr.queue.next_batch(10)
    hosts = [it.hostname for it in batch]
    assert sorted(hosts) == ["a.example", "b.example"]
    assert len(batch) == 2  # only one per host


@pytest.mark.asyncio
async def test_next_batch_priority_within_host(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/low", depth=0, priority=0)
    await fr.queue.add("http://a.example/high", depth=0, priority=10)
    batch = await fr.queue.next_batch(1)
    assert len(batch) == 1
    assert batch[0].url.endswith("/high")


@pytest.mark.asyncio
async def test_next_batch_no_double_claim(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    b1 = await fr.queue.next_batch(5)
    b2 = await fr.queue.next_batch(5)
    assert len(b1) == 1
    assert b2 == []


@pytest.mark.asyncio
async def test_next_batch_excludes_snoozed(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    await fr.domains.snooze("a.example", 60)
    batch = await fr.queue.next_batch(5)
    assert batch == []


@pytest.mark.asyncio
async def test_next_batch_excludes_sick(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    await fr.domains.set_sick("a.example", 60)
    batch = await fr.queue.next_batch(5)
    assert batch == []


@pytest.mark.asyncio
async def test_next_batch_respects_next_retry_at(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    # Claim & fail to set next_retry_at.
    [item] = await fr.queue.next_batch(5)
    await fr.queue.mark_retry(item.fp, "boom")
    # Backoff = 2 ** 1 = 2s, so not yet eligible.
    batch = await fr.queue.next_batch(5)
    assert batch == []


@pytest.mark.asyncio
async def test_next_batch_excludes_over_budget(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/1", depth=0)
    await fr.queue.add("http://a.example/2", depth=0)
    # Cap the host at 0 URLs (i.e. can't fetch any more once we've
    # observed one). Register an existing observation first.
    await fr.domains.set_budget("a.example", max_urls=1)
    await fr.domains.observe(
        "a.example", ok=True, response_ms=50.0, bytes_received=10
    )
    batch = await fr.queue.next_batch(5)
    assert batch == []


@pytest.mark.asyncio
async def test_next_batch_serves_after_retry_window(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    [item] = await fr.queue.next_batch(5)
    # Manually clear the next_retry_at to simulate time passing.
    await fr.queue.mark_retry(item.fp, "boom")
    await fr._db.execute(
        "UPDATE frontier SET next_retry_at = 0 WHERE fp = ?", (item.fp,)
    )
    batch = await fr.queue.next_batch(5)
    assert len(batch) == 1
    assert batch[0].attempts == 1


# ---------------------------------------------------------------------
# Group 6 — retry
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_increments_attempts(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0, max_attempts=3)
    [item] = await fr.queue.next_batch(1)
    await fr.queue.mark_retry(item.fp, "boom")
    cur = await fr._db.execute(
        "SELECT attempts, status FROM frontier WHERE fp = ?", (item.fp,)
    )
    row = await cur.fetchone()
    assert row == (1, "pending")


@pytest.mark.asyncio
async def test_retry_backoff_is_power_of_two(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0, max_attempts=10)
    [item] = await fr.queue.next_batch(1)
    t0 = time.time()
    await fr.queue.mark_retry(item.fp, "boom")
    cur = await fr._db.execute(
        "SELECT next_retry_at FROM frontier WHERE fp = ?", (item.fp,)
    )
    row = await cur.fetchone()
    assert row is not None
    # attempts was 0, now 1, backoff = 2 ** 1 = 2s.
    assert 1.5 <= (row[0] - t0) <= 3.0


@pytest.mark.asyncio
async def test_retry_backoff_capped_at_600(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0, max_attempts=100)
    [item] = await fr.queue.next_batch(1)
    # Force attempts to 20 so 2**21 would blow past 600s.
    await fr._db.execute(
        "UPDATE frontier SET attempts=20 WHERE fp=?", (item.fp,)
    )
    t0 = time.time()
    await fr.queue.mark_retry(item.fp, "boom")
    cur = await fr._db.execute(
        "SELECT next_retry_at FROM frontier WHERE fp = ?", (item.fp,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert (row[0] - t0) <= 601


@pytest.mark.asyncio
async def test_retry_goes_dead_at_max(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0, max_attempts=2)
    [item] = await fr.queue.next_batch(1)
    await fr.queue.mark_retry(item.fp, "boom")  # attempts 0 -> 1
    # Clear retry window so we can re-claim.
    await fr._db.execute(
        "UPDATE frontier SET next_retry_at = 0 WHERE fp = ?", (item.fp,)
    )
    [item2] = await fr.queue.next_batch(1)
    await fr.queue.mark_retry(item2.fp, "boom2")  # attempts 1 -> 2 => dead
    cur = await fr._db.execute(
        "SELECT status, last_error FROM frontier WHERE fp = ?", (item.fp,)
    )
    row = await cur.fetchone()
    assert row == ("dead", "boom2")


@pytest.mark.asyncio
async def test_retry_codes_constant_contents() -> None:
    for code in (408, 429, 500, 502, 503, 504, 522, 524):
        assert code in RETRY_CODES
    assert 200 not in RETRY_CODES
    assert 404 not in RETRY_CODES


# ---------------------------------------------------------------------
# Group 7 — adaptive delay
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ewma_math(fr: Frontier) -> None:
    await fr.domains.observe(
        "a.example", ok=True, response_ms=100, bytes_received=0
    )
    stats1 = await fr.domains.stats("a.example")
    assert stats1["ewma_response_ms"] == 100.0
    await fr.domains.observe(
        "a.example", ok=True, response_ms=200, bytes_received=0
    )
    stats2 = await fr.domains.stats("a.example")
    # alpha=0.3: 0.3*200 + 0.7*100 = 130
    assert stats2["ewma_response_ms"] == pytest.approx(130.0)


@pytest.mark.asyncio
async def test_adaptive_sets_snooze_when_enabled(fr2: Frontier) -> None:
    t0 = time.time()
    await fr2.domains.observe(
        "a.example", ok=True, response_ms=500, bytes_received=0
    )
    stats = await fr2.domains.stats("a.example")
    # delay = clamp(500 * 2.0, 200, 10000) = 1000 ms
    assert 0.8 <= (stats["snooze_until"] - t0) <= 1.5


@pytest.mark.asyncio
async def test_adaptive_does_nothing_when_disabled(fr: Frontier) -> None:
    await fr.domains.observe(
        "a.example", ok=True, response_ms=500, bytes_received=0
    )
    stats = await fr.domains.stats("a.example")
    assert stats["snooze_until"] == 0


@pytest.mark.asyncio
async def test_adaptive_clamps_min_and_max(fr2: Frontier) -> None:
    # Very small response → clamped to min (200ms).
    t0 = time.time()
    await fr2.domains.observe(
        "low.example", ok=True, response_ms=1, bytes_received=0
    )
    stats_lo = await fr2.domains.stats("low.example")
    assert 0.1 <= (stats_lo["snooze_until"] - t0) <= 0.4
    # Very large → clamped to max (10s).
    t1 = time.time()
    await fr2.domains.observe(
        "hi.example", ok=True, response_ms=100_000, bytes_received=0
    )
    stats_hi = await fr2.domains.stats("hi.example")
    assert 9.5 <= (stats_hi["snooze_until"] - t1) <= 11.0


# ---------------------------------------------------------------------
# Group 8 — snooze vs sick
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_caps_at_120(fr: Frontier) -> None:
    t0 = time.time()
    await fr.domains.snooze("a.example", 999)
    stats = await fr.domains.stats("a.example")
    assert (stats["snooze_until"] - t0) <= 121


@pytest.mark.asyncio
async def test_sick_default_600(fr: Frontier) -> None:
    t0 = time.time()
    await fr.domains.set_sick("a.example")
    stats = await fr.domains.stats("a.example")
    assert 599 <= (stats["sick_until"] - t0) <= 601


@pytest.mark.asyncio
async def test_success_resets_consecutive_and_sick(fr: Frontier) -> None:
    for _ in range(10):
        await fr.domains.bump_fail("a.example")
    stats = await fr.domains.stats("a.example")
    assert stats["consecutive_fails"] == 10
    assert stats["sick_until"] > time.time()
    await fr.domains.observe(
        "a.example", ok=True, response_ms=100, bytes_received=0
    )
    stats2 = await fr.domains.stats("a.example")
    assert stats2["consecutive_fails"] == 0
    assert stats2["sick_until"] == 0


@pytest.mark.asyncio
async def test_success_does_not_reset_snooze(fr: Frontier) -> None:
    await fr.domains.snooze("a.example", 60)
    s1 = await fr.domains.stats("a.example")
    await fr.domains.observe(
        "a.example", ok=True, response_ms=100, bytes_received=0
    )
    s2 = await fr.domains.stats("a.example")
    assert s2["snooze_until"] == s1["snooze_until"]


# ---------------------------------------------------------------------
# Group 9 — resume rollback
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_resets_in_flight(tmp_path: Path) -> None:
    f1 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    await f1.queue.add("http://a.example/x", depth=0)
    await f1.queue.next_batch(1)  # flips to in_flight
    await f1.checkpoint()
    await f1.close()
    f2 = await Frontier.open("j", resume=True, base_dir=tmp_path)
    counts = await f2.queue.counts()
    assert counts.get("pending") == 1
    assert counts.get("in_flight", 0) == 0
    await f2.close()


@pytest.mark.asyncio
async def test_rollback_count_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    f1 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    await f1.queue.add("http://a.example/x", depth=0)
    await f1.queue.next_batch(1)
    await f1.checkpoint()
    await f1.close()
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="flarecrawl.frontier"):
        f2 = await Frontier.open("j", resume=True, base_dir=tmp_path)
    assert any("rolled back" in rec.message for rec in caplog.records)
    await f2.close()


@pytest.mark.asyncio
async def test_resume_preserves_bloom(tmp_path: Path) -> None:
    f1 = await Frontier.open("j", resume=False, base_dir=tmp_path)
    await f1.queue.add("http://a.example/x", depth=0)
    await f1.checkpoint()
    await f1.close()
    f2 = await Frontier.open("j", resume=True, base_dir=tmp_path)
    # Dup insert should be rejected; if bloom was missing, SQLite would
    # still catch it, but this exercises the path.
    res = await f2.queue.add("http://a.example/x", depth=0)
    assert res is False
    await f2.close()


# ---------------------------------------------------------------------
# Group 10 — budgets
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_max_urls_excludes(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/1", depth=0)
    await fr.domains.set_budget("a.example", max_urls=0)
    # With max_urls=0 and urls_fetched=0, 0 < 0 is false → available.
    # To exercise exclusion we set max_urls=1 after one observation.
    await fr.domains.observe(
        "a.example", ok=True, response_ms=50, bytes_received=0
    )
    await fr.domains.set_budget("a.example", max_urls=1)
    batch = await fr.queue.next_batch(5)
    assert batch == []


@pytest.mark.asyncio
async def test_budget_null_means_no_cap(fr: Frontier) -> None:
    await fr.domains.set_budget("a.example", max_urls=None)
    assert await fr.domains.within_budget("a.example") is True


@pytest.mark.asyncio
async def test_budget_roundtrips(fr: Frontier) -> None:
    await fr.domains.set_budget(
        "a.example", max_urls=10, max_bytes=1_000_000, max_seconds=60
    )
    cur = await fr._db.execute(
        "SELECT max_urls, max_bytes, max_seconds FROM domain_budget"
        " WHERE hostname = ?",
        ("a.example",),
    )
    row = await cur.fetchone()
    assert row == (10, 1_000_000, 60.0)


# ---------------------------------------------------------------------
# Group 11 — dead letter
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_list(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0, max_attempts=1)
    [item] = await fr.queue.next_batch(1)
    await fr.queue.mark_retry(item.fp, "boom")  # goes straight to dead
    rows = [r async for r in fr.dead_letter.list()]
    assert len(rows) == 1
    assert rows[0]["url"] == "http://a.example/x"
    assert rows[0]["last_error"] == "boom"


@pytest.mark.asyncio
async def test_dead_letter_empty(fr: Frontier) -> None:
    rows = [r async for r in fr.dead_letter.list()]
    assert rows == []


# ---------------------------------------------------------------------
# Group 12 — delta crawl integration
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conditional_headers_from_visited(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    [item] = await fr.queue.next_batch(1)
    await fr.visited.record(
        item.fp, item.url, 200, etag='"abc"', last_modified="Wed, 21 Oct 2015"
    )
    headers = await fr.visited.conditional_headers(item.fp)
    assert headers == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Wed, 21 Oct 2015",
    }


@pytest.mark.asyncio
async def test_mark_done_after_304_refreshes_fetched_at(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    [item] = await fr.queue.next_batch(1)
    await fr.visited.record(item.fp, item.url, 200, etag='"abc"')
    cur = await fr._db.execute(
        "SELECT fetched_at FROM visited WHERE fp = ?", (item.fp,)
    )
    before = (await cur.fetchone())[0]
    await asyncio.sleep(0.01)
    await fr.visited.touch_unchanged(item.fp)
    await fr.queue.mark_done(item.fp)
    cur = await fr._db.execute(
        "SELECT fetched_at, status_code FROM visited WHERE fp = ?", (item.fp,)
    )
    row = await cur.fetchone()
    assert row[0] > before
    assert row[1] == 304


# ---------------------------------------------------------------------
# Misc / regression
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frontier_item_shape() -> None:
    it = FrontierItem(
        fp=b"\x00" * 16,
        url="http://x/",
        hostname="x",
        method="GET",
        depth=0,
        attempts=0,
    )
    assert it.url == "http://x/"
    assert it.etag is None


@pytest.mark.asyncio
async def test_checkpoint_runs(fr: Frontier) -> None:
    await fr.queue.add("http://a.example/x", depth=0)
    await fr.checkpoint()  # should not raise
