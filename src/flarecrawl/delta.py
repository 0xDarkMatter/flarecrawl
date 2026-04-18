"""Delta-crawl helpers (item 11).

Given a ``FrontierItem`` carrying prior ``etag`` / ``last_modified``,
emit the conditional HTTP request headers that let the origin reply
with 304 Not Modified. ``is_unchanged`` interprets the response.
"""

from __future__ import annotations

import httpx

from .frontier_v2 import FrontierItem


def conditional_headers(item: FrontierItem) -> dict[str, str]:
    """Build ``If-None-Match`` / ``If-Modified-Since`` headers if we have them."""
    headers: dict[str, str] = {}
    if item.etag:
        headers["If-None-Match"] = item.etag
    if item.last_modified:
        headers["If-Modified-Since"] = item.last_modified
    return headers


def is_unchanged(resp: httpx.Response) -> bool:
    return resp.status_code == 304
