"""Shared HTTP helpers for polite GETs with bounded resource use.

The same client-ownership / origin-extraction / bounded-GET dance
repeats in :mod:`flarecrawl.robots` and :mod:`flarecrawl.sitemap`.
Factor out the three building blocks:

- :func:`origin` — ``scheme://netloc`` extraction for a URL.
- :func:`ensure_client` — async context manager that yields a
  ``(client, owns)`` pair. ``owns=True`` when we created the client and
  must close it on exit; ``owns=False`` when the caller supplied one.
- :func:`polite_get` — ``httpx.AsyncClient.get`` wrapper with
  follow-redirects, UA header, optional ``max_bytes`` content-length
  cap, and ``None`` on network error.

Only these three primitives are exported; callers still own the
per-module ``MAX_*`` constants and parse logic.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

__all__ = ["ensure_client", "origin", "polite_get"]

logger = logging.getLogger(__name__)


def origin(url: str) -> str:
    """Return ``scheme://netloc`` for ``url``.

    Example
    -------
    >>> origin("https://example.com:443/a?b=1")
    'https://example.com:443'
    """
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


@asynccontextmanager
async def ensure_client(
    client: httpx.AsyncClient | None,
) -> AsyncIterator[tuple[httpx.AsyncClient, bool]]:
    """Yield ``(client, owns)``.

    If ``client`` is ``None`` a fresh :class:`httpx.AsyncClient` is
    created; ``owns`` is ``True`` and the client is closed on exit.
    If a client is passed in, it is yielded untouched and the caller
    retains ownership.
    """
    if client is None:
        owned = httpx.AsyncClient()
        try:
            yield owned, True
        finally:
            await owned.aclose()
    else:
        yield client, False


async def polite_get(
    url: str,
    *,
    client: httpx.AsyncClient,
    user_agent: str,
    timeout: float = 10.0,
    max_bytes: int | None = None,
) -> httpx.Response | None:
    """GET ``url`` with a User-Agent header and size cap.

    Returns the :class:`httpx.Response` on success, or ``None`` when
    the GET raised :class:`httpx.HTTPError` / :class:`httpx.InvalidURL`
    or the response advertises a ``content-length`` above ``max_bytes``.

    The function deliberately does NOT treat ``4xx``/``5xx`` as errors
    — callers inspect ``resp.status_code`` themselves because the
    meaningful response for (e.g.) a missing robots.txt differs from
    a missing sitemap.xml.
    """
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.debug("polite_get(%s) failed: %r", url, exc)
        return None
    if max_bytes is not None:
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > max_bytes:
                    logger.debug(
                        "polite_get(%s) size cap tripped: %s > %d",
                        url,
                        cl,
                        max_bytes,
                    )
                    return None
            except ValueError:
                pass
    return resp
