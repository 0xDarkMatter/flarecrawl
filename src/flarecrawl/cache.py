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

# v0.23.0 P1.3: bodies smaller than this in HTML/markdown formats are likely
# error pages or bot-detection stubs. Don't cache them — they're rarely the
# real content the user wanted, and caching them blocks recovery.
SUSPICIOUSLY_SMALL_HTML = 1024  # bytes


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


def cacheable_response(response: dict | str | list, *, allow_empty: bool = False) -> bool:
    """Decide whether a response should be persisted to cache.

    Errs toward NOT caching. The cost of a cache miss is one extra request;
    the cost of caching an error page is up to TTL of bad results returned
    silently.

    Rules (any failure → not cacheable):
        - Non-200 status when status is recorded
        - Empty content / zero-length body
        - HTML or markdown content shorter than SUSPICIOUSLY_SMALL_HTML
          (likely a bot-detection stub or error page)

    Args:
        response: Response payload to evaluate.
        allow_empty: If True, skip the empty/small-body checks. Use this
            when the caller has explicitly opted in (e.g. ``--cache-empty``).
    """
    if allow_empty:
        return True

    # String response (raw markdown / HTML body)
    if isinstance(response, str):
        return len(response) >= 1  # zero-length strings are never cacheable

    # List response (e.g. links extraction)
    if isinstance(response, list):
        return len(response) > 0

    # Dict response — typical CF API shape
    if isinstance(response, dict):
        # If a status field is present, require 200
        status = response.get("status") or response.get("statusCode")
        if isinstance(status, int) and status != 200:
            return False

        # Drill into common content carriers
        content = response.get("content")
        if content is None:
            content = response.get("data", {}) if isinstance(response.get("data"), dict) else {}
            content = content.get("content") if isinstance(content, dict) else None
        if content is None:
            # No identifiable content — let it through (defer to caller)
            return True

        if isinstance(content, str):
            if len(content) == 0:
                return False
            # Detect HTML/markdown stubs
            fmt = response.get("format") or (
                response.get("meta", {}).get("format") if isinstance(response.get("meta"), dict) else None
            )
            if fmt in ("html", "markdown") and len(content) < SUSPICIOUSLY_SMALL_HTML:
                return False
        elif isinstance(content, (list, dict)) and len(content) == 0:
            return False

    return True


def put(endpoint: str, body: dict, response: dict | str | list, *, allow_empty: bool = False) -> bool:
    """Cache a response.

    Returns True if the response was persisted, False if it was rejected by
    :func:`cacheable_response` or a write error occurred. Non-2xx and empty/
    stub responses are skipped by default — pass ``allow_empty=True`` to
    keep the legacy behaviour.
    """
    if not cacheable_response(response, allow_empty=allow_empty):
        return False

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
        return True
    except OSError:
        return False  # Cache write failure is non-fatal but reportable


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
