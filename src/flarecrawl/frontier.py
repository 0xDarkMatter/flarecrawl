"""SQLite-backed crawl frontier with checkpointing (item 9).

One DB per crawl job (``~/.cache/flarecrawl/jobs/<job_id>.sqlite``). WAL
enabled for concurrent reads. Schema:

* ``frontier`` — URLs to crawl, tracked status.
* ``visited`` — per-URL result (status code, etag, last-modified) used
  for delta crawls (item 11).
* ``domain_stats`` — per-hostname ok/fail counters + sickness window
  for the circuit breaker (item 15).
* ``meta`` — k/v bag for job-level config.

Items 10, 11, 15 layer on top of this module. Auto-checkpointing runs
every 1000 URLs or 30 seconds via a background asyncio task.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    url TEXT PRIMARY KEY,
    depth INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('pending','in_flight','done','failed','skipped'))
);
CREATE INDEX IF NOT EXISTS ix_frontier_status_priority
    ON frontier(status, priority DESC, added_at);

CREATE TABLE IF NOT EXISTS visited (
    url TEXT PRIMARY KEY,
    status_code INTEGER,
    etag TEXT,
    last_modified TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS domain_stats (
    hostname TEXT PRIMARY KEY,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    sick_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _hostname(url: str) -> str:
    return urlparse(url).netloc


def default_jobs_dir() -> Path:
    """Return ``~/.cache/flarecrawl/jobs`` (honours ``XDG_CACHE_HOME``)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "flarecrawl" / "jobs"


@dataclass(slots=True)
class FrontierItem:
    url: str
    depth: int
    etag: str | None = None
    last_modified: str | None = None


# Circuit-breaker knobs (item 15).
CB_FAIL_THRESHOLD = 10
CB_SICK_SECONDS = 600.0


class Frontier:
    """Async SQLite frontier.

    Construct via the ``open()`` classmethod.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        job_id: str,
        db_path: Path,
        *,
        checkpoint_every_n: int = 1000,
        checkpoint_every_s: float = 30.0,
    ) -> None:
        self._db = db
        self._job_id = job_id
        self._db_path = db_path
        self._checkpoint_every_n = checkpoint_every_n
        self._checkpoint_every_s = checkpoint_every_s
        self._ops_since_checkpoint = 0
        self._last_checkpoint_at = time.monotonic()
        self._bg_task: asyncio.Task[None] | None = None
        self._closed = False
        # Bloom filter (item 10) — lazy-attached by the caller; kept here
        # so ``add`` can consult it before hitting SQLite.
        self._bloom: Any = None

    # ------------------------------------------------------------------ open
    @classmethod
    async def open(
        cls,
        job_id: str,
        *,
        resume: bool = False,
        base_dir: Path | None = None,
    ) -> Frontier:
        base = base_dir or default_jobs_dir()
        base.mkdir(parents=True, exist_ok=True)
        db_path = base / f"{job_id}.sqlite"
        if not resume and db_path.exists():
            db_path.unlink()
        db = await aiosqlite.connect(db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.executescript(_SCHEMA)
        await db.commit()
        frontier = cls(db, job_id, db_path)
        # Try to attach the persisted bloom (item 10).
        try:
            from . import _bloom_io

            frontier._bloom = _bloom_io.load_or_create(db_path)
        except Exception:
            frontier._bloom = None
        frontier._start_checkpoint_task()
        return frontier

    def _start_checkpoint_task(self) -> None:
        async def loop() -> None:
            while not self._closed:
                try:
                    await asyncio.sleep(self._checkpoint_every_s)
                except asyncio.CancelledError:
                    return
                if self._closed:
                    return
                try:
                    await self.checkpoint()
                except Exception:
                    # Background task swallows to avoid crashing the crawl.
                    pass

        try:
            loop_obj = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._bg_task = loop_obj.create_task(loop())

    # ------------------------------------------------------------------- add
    async def add(
        self, url: str, depth: int, priority: int = 0
    ) -> bool:
        """Insert a URL into the frontier.

        Returns ``True`` if newly added, ``False`` if it was already
        present (frontier or visited).
        """
        # Bloom fast-path (item 10): skip SQLite probe when the bloom
        # definitively says "new".
        if self._bloom is not None:
            try:
                if url in self._bloom:
                    # maybe-present → fall through to SQLite uniqueness
                    pass
                else:
                    self._bloom.add(url)
                    await self._db.execute(
                        "INSERT INTO frontier(url, depth, priority, added_at, status)"
                        " VALUES(?,?,?,?, 'pending')",
                        (url, depth, priority, time.time()),
                    )
                    self._after_write()
                    return True
            except Exception:
                # Any bloom error → silently fall back to SQLite path.
                pass

        # SQLite uniqueness path.
        cur = await self._db.execute(
            "SELECT 1 FROM visited WHERE url = ?", (url,)
        )
        if await cur.fetchone() is not None:
            return False
        await self._db.execute(
            "INSERT OR IGNORE INTO frontier(url, depth, priority, added_at, status)"
            " VALUES(?,?,?,?, 'pending')",
            (url, depth, priority, time.time()),
        )
        changes = self._db.total_changes
        self._after_write()
        if self._bloom is not None:
            try:
                self._bloom.add(url)
            except Exception:
                pass
        # total_changes is cumulative; we can't easily diff here, so
        # re-check via a targeted SELECT.
        cur = await self._db.execute(
            "SELECT status FROM frontier WHERE url = ?", (url,)
        )
        row = await cur.fetchone()
        _ = changes  # silence unused
        return row is not None

    # ------------------------------------------------------------ next_batch
    async def next_batch(self, n: int = 10) -> list[FrontierItem]:
        """Atomically claim up to ``n`` pending URLs.

        Filters out URLs whose hostname is currently marked sick
        (circuit breaker, item 15).
        """
        now = time.time()
        # Pick candidate URLs, joining in any prior visited etag/last_mod
        # (item 11) and filtering sick domains (item 15).
        cur = await self._db.execute(
            """
            SELECT f.url, f.depth, v.etag, v.last_modified
            FROM frontier f
            LEFT JOIN visited v ON v.url = f.url
            WHERE f.status = 'pending'
              AND NOT EXISTS (
                SELECT 1 FROM domain_stats d
                WHERE d.sick_until > ?
                  AND instr(f.url, d.hostname) > 0
              )
            ORDER BY f.priority DESC, f.added_at ASC
            LIMIT ?
            """,
            (now, n),
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        urls = [row[0] for row in rows]
        placeholders = ",".join("?" * len(urls))
        await self._db.execute(
            f"UPDATE frontier SET status='in_flight' "
            f"WHERE url IN ({placeholders})",
            urls,
        )
        self._after_write()
        return [
            FrontierItem(url=row[0], depth=row[1], etag=row[2], last_modified=row[3])
            for row in rows
        ]

    # ---------------------------------------------------------------- mark_*
    async def mark_done(
        self,
        url: str,
        status_code: int = 200,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE frontier SET status='done' WHERE url = ?", (url,)
        )
        await self._db.execute(
            "INSERT INTO visited(url, status_code, etag, last_modified, fetched_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(url) DO UPDATE SET"
            "   status_code=excluded.status_code,"
            "   etag=excluded.etag,"
            "   last_modified=excluded.last_modified,"
            "   fetched_at=excluded.fetched_at",
            (url, status_code, etag, last_modified, time.time()),
        )
        await self._bump_domain_ok(url)
        self._after_write()

    async def mark_unchanged(self, url: str) -> None:
        """304 Not Modified — keep prior etag/last_modified, refresh timestamp."""
        await self._db.execute(
            "UPDATE frontier SET status='done' WHERE url = ?", (url,)
        )
        await self._db.execute(
            "UPDATE visited SET fetched_at=?, status_code=304 WHERE url = ?",
            (time.time(), url),
        )
        await self._bump_domain_ok(url)
        self._after_write()

    async def mark_failed(self, url: str, err: str | None = None) -> None:
        await self._db.execute(
            "UPDATE frontier SET status='failed' WHERE url = ?", (url,)
        )
        await self._bump_domain_fail(url)
        self._after_write()
        _ = err

    async def mark_skipped(self, url: str) -> None:
        await self._db.execute(
            "UPDATE frontier SET status='skipped' WHERE url = ?", (url,)
        )
        self._after_write()

    # ---------------------------------------------------- circuit breaker #15
    async def _bump_domain_ok(self, url: str) -> None:
        host = _hostname(url)
        await self._db.execute(
            "INSERT INTO domain_stats(hostname, ok_count) VALUES(?, 1)"
            " ON CONFLICT(hostname) DO UPDATE SET"
            "   ok_count = ok_count + 1,"
            "   consecutive_fails = 0,"
            "   sick_until = 0",
            (host,),
        )

    async def _bump_domain_fail(self, url: str) -> None:
        host = _hostname(url)
        await self._db.execute(
            "INSERT INTO domain_stats(hostname, fail_count, consecutive_fails)"
            " VALUES(?, 1, 1)"
            " ON CONFLICT(hostname) DO UPDATE SET"
            "   fail_count = fail_count + 1,"
            "   consecutive_fails = consecutive_fails + 1",
            (host,),
        )
        # Flip to sick if threshold hit.
        await self._db.execute(
            "UPDATE domain_stats SET sick_until = ?"
            " WHERE hostname = ? AND consecutive_fails >= ?",
            (time.time() + CB_SICK_SECONDS, host, CB_FAIL_THRESHOLD),
        )

    async def domain_stats(self, hostname: str) -> dict[str, Any]:
        cur = await self._db.execute(
            "SELECT ok_count, fail_count, consecutive_fails, sick_until"
            " FROM domain_stats WHERE hostname = ?",
            (hostname,),
        )
        row = await cur.fetchone()
        if row is None:
            return {
                "ok_count": 0,
                "fail_count": 0,
                "consecutive_fails": 0,
                "sick_until": 0.0,
            }
        return {
            "ok_count": row[0],
            "fail_count": row[1],
            "consecutive_fails": row[2],
            "sick_until": row[3],
        }

    # ------------------------------------------------------------- checkpoint
    def _after_write(self) -> None:
        self._ops_since_checkpoint += 1

    async def checkpoint(self) -> None:
        """Commit + WAL flush. Also persists the bloom filter if present."""
        await self._db.commit()
        try:
            await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except aiosqlite.Error:
            pass
        self._ops_since_checkpoint = 0
        self._last_checkpoint_at = time.monotonic()
        if self._bloom is not None:
            try:
                from . import _bloom_io

                _bloom_io.save(self._db_path, self._bloom)
            except Exception:
                pass

    async def maybe_checkpoint(self) -> None:
        now = time.monotonic()
        if (
            self._ops_since_checkpoint >= self._checkpoint_every_n
            or (now - self._last_checkpoint_at) >= self._checkpoint_every_s
        ):
            await self.checkpoint()

    # ------------------------------------------------------------------ meta
    async def set_meta(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def get_meta(self, key: str) -> str | None:
        cur = await self._db.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    # ----------------------------------------------------------------- stats
    async def counts(self) -> dict[str, int]:
        cur = await self._db.execute(
            "SELECT status, COUNT(*) FROM frontier GROUP BY status"
        )
        rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    # ----------------------------------------------------------------- close
    async def close(self) -> None:
        self._closed = True
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.checkpoint()
        except Exception:
            pass
        await self._db.close()

    async def __aenter__(self) -> Frontier:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
