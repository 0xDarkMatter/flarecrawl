"""Frontier v2 — role-separated SQLite-backed crawl frontier.

Designed against the spec in ``docs/research/FRONTIER-COMPARISON.md``
after surveying Scrapy, Heritrix, Colly, Crawlee, Nutch, and Frontera.
All code is written from scratch; no copy-paste from any of those
projects.

The engine exposes four internal role classes — :class:`FrontierQueue`,
:class:`VisitedStore`, :class:`DomainRegistry`, :class:`DeadLetter` —
fronted by the :class:`Frontier` façade. All classes share a single
:class:`aiosqlite.Connection`.

Storage layout
--------------
One SQLite DB per crawl job. Schema version ``2`` is recorded in the
``meta`` table.

- ``frontier`` — one row per unique ``(method, canonical_url, body)``.
  Primary key is a 16-byte BLAKE2b fingerprint (see
  :mod:`flarecrawl.fingerprint`). Raw URL is kept for logging/export.
- ``visited`` — one row per fingerprint that has been fetched at least
  once. Holds conditional-request metadata (``etag`` /
  ``last_modified``) and a ``next_refresh_at`` hint for weekly refresh
  jobs.
- ``domain_stats`` — per-host counters + snooze/sick windows + EWMA
  response time for adaptive delay.
- ``domain_budget`` — optional per-host caps (URL count, bytes, seconds).
- ``meta`` — key/value bag; seeded with ``frontier_schema_version=2``,
  ``canon_version=1``, ``fp_algo=blake2b-16``.

Example
-------
>>> # doctest: +SKIP
>>> async def demo():
...     fr = await Frontier.open("my_job", resume=False)
...     await fr.queue.add("http://example.com/a", depth=0)
...     batch = await fr.queue.next_batch(10)
...     await fr.close()
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import aiosqlite

from ._validate import validate_job_id
from .canon import canonicalize
from .fingerprint import fingerprint as fp_of

__all__ = [
    "Frontier",
    "FrontierItem",
    "FrontierQueue",
    "VisitedStore",
    "DomainRegistry",
    "DeadLetter",
    "RETRY_CODES",
    "SCHEMA_VERSION",
    "default_jobs_dir",
]

logger = logging.getLogger("flarecrawl.frontier")

#: HTTP status codes that should schedule a retry rather than
#: transition straight to ``dead``. Mirrors Scrapy's defaults plus the
#: Cloudflare-specific 522/524 codes.
RETRY_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504, 522, 524})

SCHEMA_VERSION = "2"
_CANON_VERSION = "1"
_FP_ALGO = "blake2b-16"

# Snooze/sick defaults (seconds).
_SNOOZE_CAP_S = 120.0
_SICK_DEFAULT_S = 600.0
_SICK_FAIL_THRESHOLD = 10
_RETRY_BACKOFF_CAP_S = 600.0

# Adaptive delay defaults.
_EWMA_ALPHA = 0.3
_ADAPTIVE_DELAY_FACTOR_DEFAULT = 2.0
_ADAPTIVE_MIN_DELAY_MS = 200.0
_ADAPTIVE_MAX_DELAY_MS = 10_000.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    fp BLOB PRIMARY KEY,
    url TEXT NOT NULL,
    hostname TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'GET',
    depth INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('pending','in_flight','done','failed','dead','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_retry_at REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS frontier_host_status_added
    ON frontier(hostname, status, added_at);
CREATE INDEX IF NOT EXISTS frontier_status_priority
    ON frontier(status, priority DESC, added_at);

CREATE TABLE IF NOT EXISTS visited (
    fp BLOB PRIMARY KEY,
    url TEXT NOT NULL,
    status_code INTEGER,
    etag TEXT,
    last_modified TEXT,
    fetched_at REAL,
    next_refresh_at REAL
);

CREATE TABLE IF NOT EXISTS domain_stats (
    hostname TEXT PRIMARY KEY,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    urls_fetched INTEGER NOT NULL DEFAULT 0,
    bytes_fetched INTEGER NOT NULL DEFAULT 0,
    seconds_spent REAL NOT NULL DEFAULT 0,
    ewma_response_ms REAL,
    snooze_until REAL NOT NULL DEFAULT 0,
    sick_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS domain_budget (
    hostname TEXT PRIMARY KEY,
    max_urls INTEGER,
    max_bytes INTEGER,
    max_seconds REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def default_jobs_dir() -> Path:
    """Return ``$XDG_CACHE_HOME/flarecrawl/jobs`` (or ``~/.cache/…``).

    Honours the ``FLARECRAWL_FRONTIER_DIR`` environment variable as an
    explicit override.

    Example
    -------
    >>> isinstance(default_jobs_dir(), Path)
    True
    """
    override = os.environ.get("FLARECRAWL_FRONTIER_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "flarecrawl" / "jobs"


def _hostname(url: str) -> str:
    """Return the lowercased network location of ``url`` (host[:port])."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return (parts.hostname or "").lower()


# ---------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------


@dataclass(slots=True)
class FrontierItem:
    """A single claim returned from :meth:`FrontierQueue.next_batch`.

    Carries the data the crawler needs to actually fetch the URL plus
    the fingerprint so results can be written back.

    Example
    -------
    >>> item = FrontierItem(fp=b"\\x00" * 16, url="http://x/", hostname="x",
    ...                     method="GET", depth=0, attempts=0)
    >>> item.url
    'http://x/'
    """

    fp: bytes
    url: str
    hostname: str
    method: str
    depth: int
    attempts: int
    etag: str | None = None
    last_modified: str | None = None


# ---------------------------------------------------------------------
# Role: FrontierQueue
# ---------------------------------------------------------------------


class FrontierQueue:
    """Owns the ``frontier`` table.

    Handles URL insertion (with dedup), per-host round-robin batch
    claiming, and status transitions (done / retry / dead).

    Example
    -------
    >>> # doctest: +SKIP
    >>> await fr.queue.add("http://example.com/", depth=0)
    True
    """

    __slots__ = ("_db", "_bloom", "_on_write")

    def __init__(
        self,
        db: aiosqlite.Connection,
        bloom: Any = None,
        on_write: Any = None,
    ) -> None:
        self._db = db
        self._bloom = bloom
        self._on_write = on_write

    async def add(
        self,
        url: str,
        *,
        depth: int,
        priority: int = 0,
        method: str = "GET",
        body: bytes = b"",
        max_attempts: int = 3,
    ) -> bool:
        """Insert a URL. Returns ``True`` if new, ``False`` if duplicate.

        Canonicalises the URL, computes a fingerprint, and checks an
        optional rbloom fast-path. SQLite uniqueness is the ultimate
        authority regardless of bloom state.
        """
        fp = fp_of(method, url, body)
        canonical = canonicalize(url)
        host = _hostname(canonical)

        # Bloom fast-path keyed by fp.
        if self._bloom is not None:
            try:
                if fp in self._bloom:
                    pass  # maybe-present: fall through to SQLite
                else:
                    self._bloom.add(fp)
                    await self._db.execute(
                        "INSERT INTO frontier(fp, url, hostname, method, depth,"
                        " priority, added_at, status, max_attempts)"
                        " VALUES(?,?,?,?,?,?,?,'pending',?)",
                        (
                            fp,
                            canonical,
                            host,
                            method.upper(),
                            depth,
                            priority,
                            time.time(),
                            max_attempts,
                        ),
                    )
                    self._note_write()
                    return True
            except (aiosqlite.IntegrityError,):
                # Bloom false negative is impossible, so this should
                # not happen — but if fp was already present we treat
                # it as duplicate.
                return False
            except Exception as exc:  # bloom backend trouble
                logger.debug("bloom add failed: %r", exc)

        # SQLite uniqueness path.
        cur = await self._db.execute(
            "SELECT 1 FROM frontier WHERE fp = ?", (fp,)
        )
        if await cur.fetchone() is not None:
            return False
        try:
            await self._db.execute(
                "INSERT INTO frontier(fp, url, hostname, method, depth,"
                " priority, added_at, status, max_attempts)"
                " VALUES(?,?,?,?,?,?,?,'pending',?)",
                (
                    fp,
                    canonical,
                    host,
                    method.upper(),
                    depth,
                    priority,
                    time.time(),
                    max_attempts,
                ),
            )
        except aiosqlite.IntegrityError:
            return False
        if self._bloom is not None:
            try:
                self._bloom.add(fp)
            except Exception as exc:
                logger.debug("bloom add (late) failed: %r", exc)
        self._note_write()
        return True

    async def next_batch(self, n: int = 10) -> list[FrontierItem]:
        """Claim up to ``n`` pending URLs, one per host.

        Excludes hosts that are snoozed, sick, or over their URL
        budget. Returned rows are atomically flipped to ``in_flight``.
        """
        if n <= 0:
            return []
        now = time.time()
        # CTE: rank pending rows per hostname. Join domain_stats /
        # budget to filter out unavailable hosts.
        sql = """
        WITH candidates AS (
            SELECT f.fp, f.url, f.hostname, f.method, f.depth, f.attempts,
                   v.etag, v.last_modified,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.hostname
                       ORDER BY f.priority DESC, f.added_at ASC
                   ) AS rn
            FROM frontier f
            LEFT JOIN visited v ON v.fp = f.fp
            LEFT JOIN domain_stats d ON d.hostname = f.hostname
            LEFT JOIN domain_budget b ON b.hostname = f.hostname
            WHERE f.status = 'pending'
              AND (f.next_retry_at IS NULL OR f.next_retry_at <= ?)
              AND COALESCE(d.snooze_until, 0) <= ?
              AND COALESCE(d.sick_until, 0)   <= ?
              AND (b.max_urls IS NULL OR COALESCE(d.urls_fetched, 0) < b.max_urls)
        )
        SELECT fp, url, hostname, method, depth, attempts, etag, last_modified
        FROM candidates
        WHERE rn = 1
        LIMIT ?
        """
        cur = await self._db.execute(sql, (now, now, now, n))
        rows = await cur.fetchall()
        if not rows:
            return []
        fps = [row[0] for row in rows]
        placeholders = ",".join("?" * len(fps))
        await self._db.execute(
            f"UPDATE frontier SET status='in_flight' "
            f"WHERE fp IN ({placeholders}) AND status='pending'",
            fps,
        )
        self._note_write()
        return [
            FrontierItem(
                fp=row[0],
                url=row[1],
                hostname=row[2],
                method=row[3],
                depth=row[4],
                attempts=row[5],
                etag=row[6],
                last_modified=row[7],
            )
            for row in rows
        ]

    async def mark_done(self, fp: bytes) -> None:
        """Transition a fingerprint from ``in_flight`` to ``done``.

        Also invoked by the 304-response path — a not-modified result is
        semantically a successful fetch for the frontier (the visited
        row's ``fetched_at`` is refreshed elsewhere).
        """
        await self._db.execute(
            "UPDATE frontier SET status='done' WHERE fp = ?", (fp,)
        )
        self._note_write()

    async def mark_retry(self, fp: bytes, err: str) -> None:
        """Increment attempts, schedule exponential backoff, or go dead.

        If ``attempts + 1 >= max_attempts`` the row transitions to
        ``dead``. Otherwise the next retry is scheduled at
        ``now + min(600, 2 ** attempts)``.
        """
        cur = await self._db.execute(
            "SELECT attempts, max_attempts FROM frontier WHERE fp = ?", (fp,)
        )
        row = await cur.fetchone()
        if row is None:
            return
        attempts, max_attempts = int(row[0]), int(row[1])
        new_attempts = attempts + 1
        if new_attempts >= max_attempts:
            await self.mark_dead(fp, err)
            return
        backoff = min(_RETRY_BACKOFF_CAP_S, float(2 ** new_attempts))
        await self._db.execute(
            "UPDATE frontier SET status='pending', attempts=?,"
            " next_retry_at=?, last_error=? WHERE fp=?",
            (new_attempts, time.time() + backoff, err, fp),
        )
        self._note_write()

    async def mark_dead(self, fp: bytes, err: str) -> None:
        """Terminal: status=dead."""
        await self._db.execute(
            "UPDATE frontier SET status='dead', last_error=? WHERE fp = ?",
            (err, fp),
        )
        self._note_write()

    async def mark_skipped(self, fp: bytes) -> None:
        """Non-error terminal: e.g. blocked by robots."""
        await self._db.execute(
            "UPDATE frontier SET status='skipped' WHERE fp = ?", (fp,)
        )
        self._note_write()

    async def rollback_in_flight(self) -> int:
        """Reset any ``in_flight`` rows to ``pending``. Returns the count."""
        cur = await self._db.execute(
            "UPDATE frontier SET status='pending' WHERE status='in_flight'"
        )
        # aiosqlite's cursor.rowcount is populated for UPDATEs.
        count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        self._note_write()
        return count

    async def counts(self) -> dict[str, int]:
        """Return status → row-count map."""
        cur = await self._db.execute(
            "SELECT status, COUNT(*) FROM frontier GROUP BY status"
        )
        rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    def _note_write(self) -> None:
        if self._on_write is not None:
            self._on_write()


# ---------------------------------------------------------------------
# Role: VisitedStore
# ---------------------------------------------------------------------


class VisitedStore:
    """Owns the ``visited`` table (dedup state + conditional-request hints).

    Example
    -------
    >>> # doctest: +SKIP
    >>> await fr.visited.conditional_headers(item.fp)
    {'If-None-Match': '"abc"'}
    """

    __slots__ = ("_db", "_on_write")

    def __init__(self, db: aiosqlite.Connection, on_write: Any = None) -> None:
        self._db = db
        self._on_write = on_write

    async def record(
        self,
        fp: bytes,
        url: str,
        status_code: int,
        etag: str | None = None,
        last_modified: str | None = None,
        next_refresh_at: float | None = None,
    ) -> None:
        """Insert or update a visited row."""
        await self._db.execute(
            "INSERT INTO visited(fp, url, status_code, etag, last_modified,"
            " fetched_at, next_refresh_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(fp) DO UPDATE SET"
            "   url=excluded.url,"
            "   status_code=excluded.status_code,"
            "   etag=excluded.etag,"
            "   last_modified=excluded.last_modified,"
            "   fetched_at=excluded.fetched_at,"
            "   next_refresh_at=excluded.next_refresh_at",
            (
                fp,
                url,
                status_code,
                etag,
                last_modified,
                time.time(),
                next_refresh_at,
            ),
        )
        if self._on_write is not None:
            self._on_write()

    async def touch_unchanged(self, fp: bytes) -> None:
        """Record a 304 Not Modified response on an existing row."""
        await self._db.execute(
            "UPDATE visited SET status_code=304, fetched_at=? WHERE fp=?",
            (time.time(), fp),
        )
        if self._on_write is not None:
            self._on_write()

    async def conditional_headers(self, fp: bytes) -> dict[str, str]:
        """Build ``If-None-Match`` / ``If-Modified-Since`` headers if present."""
        cur = await self._db.execute(
            "SELECT etag, last_modified FROM visited WHERE fp=?", (fp,)
        )
        row = await cur.fetchone()
        if row is None:
            return {}
        headers: dict[str, str] = {}
        if row[0]:
            headers["If-None-Match"] = row[0]
        if row[1]:
            headers["If-Modified-Since"] = row[1]
        return headers

    async def due_for_refresh(
        self, now: float, limit: int
    ) -> list[tuple[bytes, str]]:
        """Return ``(fp, url)`` pairs whose ``next_refresh_at`` has passed."""
        cur = await self._db.execute(
            "SELECT fp, url FROM visited"
            " WHERE next_refresh_at IS NOT NULL AND next_refresh_at <= ?"
            " LIMIT ?",
            (now, limit),
        )
        return [(row[0], row[1]) for row in await cur.fetchall()]


# ---------------------------------------------------------------------
# Role: DomainRegistry
# ---------------------------------------------------------------------


class DomainRegistry:
    """Owns ``domain_stats`` and ``domain_budget``.

    Example
    -------
    >>> # doctest: +SKIP
    >>> await fr.domains.observe("example.com", ok=True, response_ms=120, bytes_received=2048)
    """

    __slots__ = ("_db", "_on_write", "_adaptive", "_delay_factor", "_min_ms", "_max_ms")

    def __init__(
        self,
        db: aiosqlite.Connection,
        on_write: Any = None,
        *,
        adaptive_mode: bool = False,
        delay_factor: float = _ADAPTIVE_DELAY_FACTOR_DEFAULT,
        min_delay_ms: float = _ADAPTIVE_MIN_DELAY_MS,
        max_delay_ms: float = _ADAPTIVE_MAX_DELAY_MS,
    ) -> None:
        self._db = db
        self._on_write = on_write
        self._adaptive = adaptive_mode
        self._delay_factor = delay_factor
        self._min_ms = min_delay_ms
        self._max_ms = max_delay_ms

    async def _ensure_row(self, hostname: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO domain_stats(hostname) VALUES(?)", (hostname,)
        )

    async def observe(
        self,
        hostname: str,
        *,
        ok: bool,
        response_ms: float,
        bytes_received: int = 0,
    ) -> None:
        """Record a successful or failed observation for a host.

        Updates EWMA, counters, and — if adaptive mode is on —
        ``snooze_until``.
        """
        await self._ensure_row(hostname)
        # Read current EWMA so we can blend.
        cur = await self._db.execute(
            "SELECT ewma_response_ms FROM domain_stats WHERE hostname=?",
            (hostname,),
        )
        row = await cur.fetchone()
        prev = row[0] if row and row[0] is not None else None
        new_ewma = (
            _EWMA_ALPHA * response_ms + (1.0 - _EWMA_ALPHA) * prev
            if prev is not None
            else float(response_ms)
        )
        if ok:
            await self._db.execute(
                "UPDATE domain_stats SET"
                "   ok_count = ok_count + 1,"
                "   consecutive_fails = 0,"
                "   sick_until = 0,"
                "   urls_fetched = urls_fetched + 1,"
                "   bytes_fetched = bytes_fetched + ?,"
                "   seconds_spent = seconds_spent + ?,"
                "   ewma_response_ms = ?"
                " WHERE hostname = ?",
                (
                    int(bytes_received),
                    float(response_ms) / 1000.0,
                    new_ewma,
                    hostname,
                ),
            )
        else:
            await self._db.execute(
                "UPDATE domain_stats SET"
                "   fail_count = fail_count + 1,"
                "   consecutive_fails = consecutive_fails + 1,"
                "   urls_fetched = urls_fetched + 1,"
                "   seconds_spent = seconds_spent + ?,"
                "   ewma_response_ms = ?"
                " WHERE hostname = ?",
                (float(response_ms) / 1000.0, new_ewma, hostname),
            )
            # Trip sick if threshold crossed.
            await self._trip_sick_if_threshold(hostname)

        if ok and self._adaptive:
            delay_ms = max(
                self._min_ms,
                min(self._max_ms, new_ewma * self._delay_factor),
            )
            await self._db.execute(
                "UPDATE domain_stats SET snooze_until = ? WHERE hostname = ?",
                (time.time() + delay_ms / 1000.0, hostname),
            )

        if self._on_write is not None:
            self._on_write()

    async def bump_fail(self, hostname: str) -> None:
        """Record a failure without a response-time sample."""
        await self._ensure_row(hostname)
        await self._db.execute(
            "UPDATE domain_stats SET fail_count = fail_count + 1,"
            " consecutive_fails = consecutive_fails + 1"
            " WHERE hostname = ?",
            (hostname,),
        )
        await self._trip_sick_if_threshold(hostname)
        if self._on_write is not None:
            self._on_write()

    async def _trip_sick_if_threshold(self, hostname: str) -> None:
        """Set ``sick_until`` if ``consecutive_fails`` crossed the threshold.

        Idempotent — the predicate in the WHERE clause means healthy
        rows are not touched.
        """
        await self._db.execute(
            "UPDATE domain_stats SET sick_until = ?"
            " WHERE hostname = ? AND consecutive_fails >= ?",
            (time.time() + _SICK_DEFAULT_S, hostname, _SICK_FAIL_THRESHOLD),
        )

    async def snooze(self, hostname: str, seconds: float) -> None:
        """Short transient backoff. Capped at 120 s."""
        seconds = min(seconds, _SNOOZE_CAP_S)
        await self._ensure_row(hostname)
        await self._db.execute(
            "UPDATE domain_stats SET snooze_until ="
            " MAX(snooze_until, ?) WHERE hostname = ?",
            (time.time() + seconds, hostname),
        )
        if self._on_write is not None:
            self._on_write()

    async def set_sick(self, hostname: str, seconds: float = _SICK_DEFAULT_S) -> None:
        """Long circuit-break. Default 10 min."""
        await self._ensure_row(hostname)
        await self._db.execute(
            "UPDATE domain_stats SET sick_until = ? WHERE hostname = ?",
            (time.time() + seconds, hostname),
        )
        if self._on_write is not None:
            self._on_write()

    async def is_available(self, hostname: str) -> bool:
        """Return False if host is snoozed, sick, or over budget."""
        now = time.time()
        cur = await self._db.execute(
            "SELECT COALESCE(snooze_until, 0), COALESCE(sick_until, 0),"
            " COALESCE(urls_fetched, 0) FROM domain_stats WHERE hostname = ?",
            (hostname,),
        )
        row = await cur.fetchone()
        snooze_until, sick_until, urls_fetched = (
            (row[0], row[1], row[2]) if row else (0.0, 0.0, 0)
        )
        if snooze_until > now or sick_until > now:
            return False
        cur2 = await self._db.execute(
            "SELECT max_urls FROM domain_budget WHERE hostname = ?", (hostname,)
        )
        row2 = await cur2.fetchone()
        if row2 and row2[0] is not None and urls_fetched >= int(row2[0]):
            return False
        return True

    async def set_budget(
        self,
        hostname: str,
        *,
        max_urls: int | None = None,
        max_bytes: int | None = None,
        max_seconds: float | None = None,
    ) -> None:
        """Upsert the per-host budget row."""
        await self._db.execute(
            "INSERT INTO domain_budget(hostname, max_urls, max_bytes, max_seconds)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(hostname) DO UPDATE SET"
            "   max_urls=excluded.max_urls,"
            "   max_bytes=excluded.max_bytes,"
            "   max_seconds=excluded.max_seconds",
            (hostname, max_urls, max_bytes, max_seconds),
        )
        if self._on_write is not None:
            self._on_write()

    async def within_budget(self, hostname: str) -> bool:
        """Return False if any non-NULL budget has been exhausted."""
        cur = await self._db.execute(
            "SELECT b.max_urls, b.max_bytes, b.max_seconds,"
            " COALESCE(d.urls_fetched, 0),"
            " COALESCE(d.bytes_fetched, 0),"
            " COALESCE(d.seconds_spent, 0)"
            " FROM domain_budget b"
            " LEFT JOIN domain_stats d ON d.hostname = b.hostname"
            " WHERE b.hostname = ?",
            (hostname,),
        )
        row = await cur.fetchone()
        if row is None:
            return True
        mu, mb, ms, u, bts, s = row
        if mu is not None and u >= mu:
            return False
        if mb is not None and bts >= mb:
            return False
        if ms is not None and s >= ms:
            return False
        return True

    async def stats(self, hostname: str) -> dict[str, Any]:
        """Return a snapshot of counters/flags for a host."""
        cur = await self._db.execute(
            "SELECT ok_count, fail_count, consecutive_fails,"
            " urls_fetched, bytes_fetched, seconds_spent,"
            " ewma_response_ms, snooze_until, sick_until"
            " FROM domain_stats WHERE hostname = ?",
            (hostname,),
        )
        row = await cur.fetchone()
        if row is None:
            return {
                "ok_count": 0,
                "fail_count": 0,
                "consecutive_fails": 0,
                "urls_fetched": 0,
                "bytes_fetched": 0,
                "seconds_spent": 0.0,
                "ewma_response_ms": None,
                "snooze_until": 0.0,
                "sick_until": 0.0,
            }
        return {
            "ok_count": row[0],
            "fail_count": row[1],
            "consecutive_fails": row[2],
            "urls_fetched": row[3],
            "bytes_fetched": row[4],
            "seconds_spent": row[5],
            "ewma_response_ms": row[6],
            "snooze_until": row[7],
            "sick_until": row[8],
        }


# ---------------------------------------------------------------------
# Role: DeadLetter
# ---------------------------------------------------------------------


class DeadLetter:
    """Read-only view over ``frontier WHERE status='dead'``.

    Example
    -------
    >>> # doctest: +SKIP
    >>> async for row in fr.dead_letter.list():
    ...     print(row["url"], row["last_error"])
    """

    __slots__ = ("_db",)

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def list(self) -> AsyncIterator[dict[str, Any]]:
        """Yield one dict per dead row."""
        cur = await self._db.execute(
            "SELECT fp, url, hostname, method, attempts, last_error, added_at"
            " FROM frontier WHERE status='dead'"
            " ORDER BY added_at ASC"
        )
        async for row in cur:
            yield {
                "fp": row[0],
                "url": row[1],
                "hostname": row[2],
                "method": row[3],
                "attempts": row[4],
                "last_error": row[5],
                "added_at": row[6],
            }


# ---------------------------------------------------------------------
# Facade: Frontier
# ---------------------------------------------------------------------


class Frontier:
    """Top-level façade.

    Construct via :meth:`Frontier.open`. Exposes :attr:`queue`,
    :attr:`visited`, :attr:`domains`, and :attr:`dead_letter` as
    role-scoped attributes.

    Example
    -------
    >>> # doctest: +SKIP
    >>> fr = await Frontier.open("job-1", resume=False)
    >>> await fr.queue.add("http://example.com/", depth=0)
    >>> await fr.close()
    """

    __slots__ = (
        "_db",
        "_db_path",
        "_job_id",
        "_closed",
        "_bloom",
        "_bg_task",
        "_checkpoint_every_n",
        "_checkpoint_every_s",
        "_ops_since_checkpoint",
        "_last_checkpoint_at",
        "queue",
        "visited",
        "domains",
        "dead_letter",
    )

    def __init__(
        self,
        db: aiosqlite.Connection,
        job_id: str,
        db_path: Path,
        *,
        adaptive_mode: bool = False,
        checkpoint_every_n: int = 1000,
        checkpoint_every_s: float = 30.0,
        bloom: Any = None,
    ) -> None:
        self._db = db
        self._db_path = db_path
        self._job_id = job_id
        self._closed = False
        self._bloom = bloom
        self._bg_task: asyncio.Task[None] | None = None
        self._checkpoint_every_n = checkpoint_every_n
        self._checkpoint_every_s = checkpoint_every_s
        self._ops_since_checkpoint = 0
        self._last_checkpoint_at = time.monotonic()

        self.queue = FrontierQueue(db, bloom=bloom, on_write=self._note_write)
        self.visited = VisitedStore(db, on_write=self._note_write)
        self.domains = DomainRegistry(
            db, on_write=self._note_write, adaptive_mode=adaptive_mode
        )
        self.dead_letter = DeadLetter(db)

    @classmethod
    async def open(
        cls,
        job_id: str,
        *,
        resume: bool = False,
        base_dir: Path | None = None,
        adaptive_mode: bool = False,
    ) -> Frontier:
        """Open (and optionally resume) a frontier database.

        When ``resume=False`` any existing ``<job_id>.sqlite`` is
        unlinked. When ``resume=True`` the DB is opened in place and
        any ``in_flight`` rows are flipped to ``pending`` (with a
        WARNING log line if any rolled back).
        """
        validate_job_id(job_id)
        base = base_dir or default_jobs_dir()
        base.mkdir(parents=True, exist_ok=True)
        db_path = base / f"{job_id}.sqlite"
        if not resume and db_path.exists():
            db_path.unlink()
            # Also drop the sidecar bloom.
            bloom_sidecar = db_path.with_suffix(".bloom")
            if bloom_sidecar.exists():
                bloom_sidecar.unlink()
        db = await aiosqlite.connect(db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA)
        await db.commit()

        # Meta seed.
        await db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('frontier_schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        await db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('canon_version', ?)",
            (_CANON_VERSION,),
        )
        await db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('fp_algo', ?)",
            (_FP_ALGO,),
        )
        await db.commit()

        bloom: Any = None
        try:
            from . import _bloom_io

            bloom = _bloom_io.load_or_create(db_path)
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug("bloom init failed: %r", exc)
            bloom = None

        fr = cls(
            db,
            job_id,
            db_path,
            adaptive_mode=adaptive_mode,
            bloom=bloom,
        )

        if resume:
            count = await fr.queue.rollback_in_flight()
            if count:
                logger.warning(
                    "frontier resume: rolled back %d in_flight row(s) in job %s",
                    count,
                    job_id,
                )
            await fr._commit()

        fr._start_checkpoint_task()
        return fr

    # ------------------------------------------------------------------ meta
    async def set_meta(self, key: str, value: str) -> None:
        """Upsert a meta row. Useful for job-level flags."""
        await self._db.execute(
            "INSERT INTO meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._note_write()

    async def get_meta(self, key: str) -> str | None:
        """Fetch a meta value or None."""
        cur = await self._db.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    # ----------------------------------------------------------- checkpoint
    def _note_write(self) -> None:
        self._ops_since_checkpoint += 1

    async def _commit(self) -> None:
        await self._db.commit()

    async def checkpoint(self) -> None:
        """Commit the transaction and TRUNCATE the WAL."""
        await self._db.commit()
        try:
            await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except aiosqlite.Error as exc:
            logger.debug("wal_checkpoint failed: %r", exc)
        self._ops_since_checkpoint = 0
        self._last_checkpoint_at = time.monotonic()
        if self._bloom is not None:
            try:
                from . import _bloom_io

                _bloom_io.save(self._db_path, self._bloom)
            except Exception as exc:  # pragma: no cover
                logger.debug("bloom save failed: %r", exc)

    async def maybe_checkpoint(self) -> None:
        """Checkpoint if either threshold is hit."""
        now = time.monotonic()
        if (
            self._ops_since_checkpoint >= self._checkpoint_every_n
            or (now - self._last_checkpoint_at) >= self._checkpoint_every_s
        ):
            await self.checkpoint()

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
                except Exception as exc:  # pragma: no cover
                    logger.debug("background checkpoint failed: %r", exc)

        try:
            loop_obj = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._bg_task = loop_obj.create_task(loop())

    # ----------------------------------------------------------------- close
    async def close(self) -> None:
        """Stop the background task, checkpoint, and close the DB."""
        self._closed = True
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.checkpoint()
        except Exception as exc:  # pragma: no cover
            logger.debug("final checkpoint failed: %r", exc)
        await self._db.close()

    async def __aenter__(self) -> Frontier:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
