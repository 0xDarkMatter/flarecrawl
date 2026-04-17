"""Stealth HTTP client for Flarecrawl.

Provides HTTP requests with real browser TLS fingerprints via curl_cffi.
When curl_cffi is installed and stealth mode is enabled, all direct HTTP
requests use Safari/Chrome TLS impersonation to avoid bot detection based
on JA3/JA4 fingerprinting.

Usage:
    from .stealth import stealth_get, stealth_session, is_available

    # One-off request
    resp = stealth_get(url, headers=headers)

    # Reusable session (batch mode)
    with stealth_session() as session:
        resp = session.get(url, headers=headers)
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


def is_available() -> bool:
    """Check if curl_cffi is installed."""
    try:
        from curl_cffi import requests as _  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass(slots=True)
class StealthResponse:
    """Normalised response matching httpx.Response interface."""
    status_code: int
    text: str
    headers: dict
    url: str


def _to_response(resp: Any) -> StealthResponse:
    """Convert curl_cffi response to our normalised type."""
    return StealthResponse(
        status_code=resp.status_code,
        text=resp.text,
        headers=dict(resp.headers),
        url=str(resp.url),
    )


def stealth_get(
    url: str,
    *,
    headers: dict | None = None,
    timeout: int = 15,
    impersonate: str = "safari",
    proxy: str | None = None,
) -> StealthResponse | None:
    """Make a GET request with browser TLS fingerprint.

    Returns StealthResponse on success, None on error.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return None

    try:
        resp = cffi_requests.get(
            url,
            headers=headers or {},
            impersonate=impersonate,
            timeout=timeout,
            proxies={"https": proxy, "http": proxy} if proxy else None,
        )
        return _to_response(resp)
    except Exception:
        return None


class StealthSession:
    """Reusable stealth HTTP session for batch operations."""

    def __init__(self, impersonate: str = "safari", timeout: int = 15, proxy: str | None = None):
        self._impersonate = impersonate
        self._timeout = timeout
        self._proxy = proxy
        self._session = None

    def _ensure_session(self):
        if self._session is None:
            from curl_cffi import requests as cffi_requests
            self._session = cffi_requests.Session(
                impersonate=self._impersonate,
                timeout=self._timeout,
                proxies={"https": self._proxy, "http": self._proxy} if self._proxy else None,
            )

    def get(self, url: str, *, headers: dict | None = None, **kwargs) -> StealthResponse | None:
        try:
            self._ensure_session()
            resp = self._session.get(url, headers=headers or {}, **kwargs)
            return _to_response(resp)
        except Exception:
            return None

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@contextmanager
def stealth_session(impersonate: str = "safari", timeout: int = 15, proxy: str | None = None):
    """Context manager for a reusable stealth session."""
    session = StealthSession(impersonate=impersonate, timeout=timeout, proxy=proxy)
    try:
        yield session
    finally:
        session.close()
