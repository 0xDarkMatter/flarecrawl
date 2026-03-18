"""Simple file-based response cache for Flarecrawl.

Caches API responses keyed on (endpoint, url, body_hash) with configurable TTL.
Cache is stored in the platform config directory under a 'cache' subdirectory.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .config import get_config_dir

CACHE_DIR_NAME = "cache"
DEFAULT_TTL = 3600  # 1 hour


def _cache_dir() -> Path:
    d = get_config_dir() / CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(endpoint: str, body: dict) -> str:
    """Generate a deterministic cache key from endpoint + request body."""
    # Normalize body for consistent hashing
    canonical = json.dumps(body, sort_keys=True, default=str)
    h = hashlib.sha256(f"{endpoint}:{canonical}".encode()).hexdigest()[:16]
    return h


def get(endpoint: str, body: dict, ttl: int = DEFAULT_TTL) -> dict | None:
    """Return cached response if valid, else None."""
    key = _cache_key(endpoint, body)
    cache_file = _cache_dir() / f"{key}.json"

    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # Check TTL
    cached_at = data.get("_cached_at", 0)
    if time.time() - cached_at > ttl:
        # Expired — remove stale entry
        try:
            cache_file.unlink()
        except OSError:
            pass
        return None

    return data.get("response")


def put(endpoint: str, body: dict, response: dict | str | list) -> None:
    """Cache a response."""
    key = _cache_key(endpoint, body)
    cache_file = _cache_dir() / f"{key}.json"

    entry = {
        "_cached_at": time.time(),
        "_endpoint": endpoint,
        "_url": body.get("url", ""),
        "response": response,
    }

    try:
        cache_file.write_text(json.dumps(entry, default=str), encoding="utf-8")
    except OSError:
        pass  # Cache write failure is non-fatal


def clear() -> int:
    """Clear all cached entries. Returns count of entries removed."""
    d = _cache_dir()
    count = 0
    for f in d.glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
