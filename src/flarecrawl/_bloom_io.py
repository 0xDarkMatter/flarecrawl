"""Persistence shim for the rbloom visited-dedup filter (item 10).

Gracefully falls back to an in-memory ``set()`` when ``rbloom`` is not
importable (e.g. Windows without a pre-built wheel). The fallback still
exposes ``__contains__`` / ``add`` / ``save`` / ``load`` semantics so
``Frontier`` does not need to branch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

try:
    from rbloom import Bloom  # type: ignore[import-untyped]

    RBLOOM_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised on platforms missing rbloom
    Bloom = None  # type: ignore[assignment,misc]
    RBLOOM_AVAILABLE = False


# ~10M URLs at fpr=0.001 → ~15 MB on disk.
_EXPECTED = 10_000_000
_FPR = 0.001


def _stable_hash(obj: object) -> int:
    """Deterministic 128-bit hash for rbloom persistence.

    rbloom refuses to save filters that use Python's built-in ``hash``
    because ``PYTHONHASHSEED`` shifts between interpreters. We feed it a
    SHA-256-based signed int instead.
    """
    data = str(obj).encode("utf-8")
    digest = hashlib.sha256(data).digest()[:16]
    # rbloom wants a signed int that fits in 128 bits.
    val = int.from_bytes(digest, "big", signed=False)
    # Convert to signed 128-bit range.
    if val >= (1 << 127):
        val -= 1 << 128
    return val


class _SetBloom:
    """Tiny in-memory fallback implementing the contract Frontier expects."""

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: set[str] = set()

    def __contains__(self, item: object) -> bool:
        return item in self._s

    def add(self, item: str) -> None:
        self._s.add(item)

    def save(self, path: str) -> None:  # noqa: D401
        # No-op: set() is not persisted. Deliberately silent.
        _ = path

    @classmethod
    def load(cls, path: str) -> _SetBloom:  # noqa: D401
        _ = path
        return cls()


def _bloom_path(db_path: Path) -> Path:
    return db_path.with_suffix(".bloom")


def load_or_create(db_path: Path) -> Any:
    """Load a persisted bloom next to ``db_path``, or create a new one."""
    bp = _bloom_path(db_path)
    if not RBLOOM_AVAILABLE:
        return _SetBloom()
    if bp.exists():
        try:
            return Bloom.load(str(bp), _stable_hash)
        except Exception:
            pass
    return Bloom(_EXPECTED, _FPR, _stable_hash)


def save(db_path: Path, bloom: Any) -> None:
    bp = _bloom_path(db_path)
    try:
        bloom.save(str(bp))
    except Exception:
        pass
