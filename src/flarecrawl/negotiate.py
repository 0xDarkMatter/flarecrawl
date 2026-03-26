"""Markdown content negotiation for Flarecrawl.

Attempts to fetch markdown directly from sites that support the
``Accept: text/markdown`` content negotiation protocol (e.g. Cloudflare
Markdown for Agents). When successful, this avoids spinning up a headless
browser entirely — faster, cheaper, and often higher quality.

Domain-level capability is cached so that only one probe per domain is
needed across batch scrapes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import __version__
from .config import get_config_dir

# Cache TTLs (seconds)
_POSITIVE_TTL = 7 * 86400   # 7 days — unlikely to disable once enabled
_NEGATIVE_TTL = 24 * 3600   # 24 hours — sites may enable it
_CACHE_FILE = "markdown_domains.json"

# Request timeout for negotiation (keep short — this is a fast-path probe)
_NEGOTIATE_TIMEOUT = 10


@dataclass
class NegotiationResult:
    """Result of a successful markdown content negotiation."""

    content: str
    tokens: int | None = None
    content_signal: dict | None = None
    elapsed: float = 0.0
    headers: dict = field(default_factory=dict)


# ------------------------------------------------------------------
# Domain capability cache
# ------------------------------------------------------------------


def _cache_path() -> Path:
    return get_config_dir() / _CACHE_FILE


def _load_domain_cache() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_domain_cache(cache: dict) -> None:
    path = _cache_path()
    try:
        path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass


def domain_supports_markdown(domain: str) -> bool | None:
    """Check cached domain capability.

    Returns True if known to support, False if known not to, None if unknown.
    """
    cache = _load_domain_cache()
    entry = cache.get(domain)
    if entry is None:
        return None

    checked = entry.get("checked", 0)
    supports = entry.get("supports", False)
    ttl = _POSITIVE_TTL if supports else _NEGATIVE_TTL

    if time.time() - checked > ttl:
        return None  # Expired

    return supports


def _cache_domain(domain: str, supports: bool) -> None:
    """Cache whether a domain supports markdown negotiation."""
    cache = _load_domain_cache()
    cache[domain] = {"supports": supports, "checked": time.time()}

    # Prune old entries (keep max 500)
    if len(cache) > 500:
        entries = sorted(cache.items(), key=lambda x: x[1].get("checked", 0))
        cache = dict(entries[-500:])

    _save_domain_cache(cache)


def clear_domain_cache() -> int:
    """Clear the domain capability cache. Returns count of entries removed."""
    path = _cache_path()
    if not path.exists():
        return 0
    try:
        cache = _load_domain_cache()
        count = len(cache)
        path.unlink()
        return count
    except OSError:
        return 0


def get_negotiate_session(timeout: int = _NEGOTIATE_TIMEOUT) -> httpx.Client:
    """Create a reusable httpx session for content negotiation.

    Use this in batch mode to avoid creating a new connection per URL.
    The caller is responsible for closing the session.
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        http2=True,
        follow_redirects=True,
    )


# ------------------------------------------------------------------
# Content-Signal header parser
# ------------------------------------------------------------------


def _parse_content_signal(header: str) -> dict:
    """Parse Content-Signal header value.

    Example: "ai-train=yes, search=yes, ai-input=yes"
    Returns: {"ai-train": "yes", "search": "yes", "ai-input": "yes"}
    """
    result = {}
    for part in header.split(","):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            result[key.strip()] = value.strip()
    return result


# ------------------------------------------------------------------
# Negotiation
# ------------------------------------------------------------------


def try_negotiate(
    url: str,
    *,
    session: httpx.Client | None = None,
    extra_headers: dict | None = None,
    timeout: int = _NEGOTIATE_TIMEOUT,
) -> NegotiationResult | None:
    """Attempt markdown content negotiation with the target URL.

    Sends a GET request with ``Accept: text/markdown``. If the server
    returns ``content-type: text/markdown``, the response is used directly.
    Otherwise returns None and the caller should fall back to browser rendering.

    .. note::
       Do NOT pass the CF API client session here — it carries
       ``Authorization: Bearer <cf_token>`` which must not leak to
       arbitrary sites.  Pass a clean session or None (one will be
       created).

    Args:
        url: The target URL.
        session: Optional *clean* httpx.Client (no CF auth headers).
        extra_headers: Additional headers (user auth, cookies, language, etc.).
        timeout: Request timeout in seconds.

    Returns:
        NegotiationResult on success, None if the site doesn't support it.
    """
    parsed = urlparse(url)
    domain = parsed.netloc

    # Check domain cache first
    cached = domain_supports_markdown(domain)
    if cached is False:
        return None  # Known non-supporter

    headers = {
        "Accept": "text/markdown, text/html;q=0.9, */*;q=0.8",
        "User-Agent": f"Flarecrawl/{__version__} (+https://github.com/0xDarkMatter/flarecrawl)",
    }
    if extra_headers:
        headers.update(extra_headers)

    start = time.time()
    own_session = session is None

    try:
        if own_session:
            session = httpx.Client(
                timeout=httpx.Timeout(timeout),
                http2=True,
                follow_redirects=True,
            )

        response = session.get(url, headers=headers, follow_redirects=True)
        elapsed = time.time() - start

        content_type = response.headers.get("content-type", "")

        if "text/markdown" in content_type and response.status_code == 200:
            # Success — site supports markdown
            _cache_domain(domain, True)

            # Parse metadata headers
            tokens = None
            tokens_header = response.headers.get("x-markdown-tokens")
            if tokens_header:
                try:
                    tokens = int(tokens_header)
                except (ValueError, TypeError):
                    pass

            content_signal = None
            signal_header = response.headers.get("content-signal")
            if signal_header:
                content_signal = _parse_content_signal(signal_header)

            return NegotiationResult(
                content=response.text,
                tokens=tokens,
                content_signal=content_signal,
                elapsed=round(elapsed, 3),
                headers=dict(response.headers),
            )

        # Site didn't return markdown
        _cache_domain(domain, False)
        return None

    except (httpx.HTTPError, httpx.TimeoutException, OSError):
        # Network error — don't cache (transient), fall back to browser
        return None
    finally:
        if own_session and session is not None:
            session.close()
