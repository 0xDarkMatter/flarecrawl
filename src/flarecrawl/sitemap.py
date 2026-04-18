"""Sitemap-first URL discovery (item 12).

Walk ``robots.txt`` for ``Sitemap:`` entries, then fall back to
``<base_url>/sitemap.xml``. Sitemap XML is parsed with selectolax's XML
mode for speed; output is a list of ``(url, lastmod)`` tuples, suitable
for seeding into the crawl frontier with a high priority.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from . import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

_SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_FETCH_TIMEOUT = 15.0

#: Hard cap on a single sitemap response body. The sitemap protocol
#: limits uncompressed sitemaps to 50 MiB but most are well under
#: 10 MiB. 10 MiB balances "accept realistic real-world sitemaps"
#: with "don't let a hostile origin exhaust memory".
MAX_SITEMAP_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class SitemapEntry:
    url: str
    lastmod: str | None = None


async def _get(
    url: str, client: httpx.AsyncClient, user_agent: str,
    *, max_bytes: int | None = None,
) -> httpx.Response | None:
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
        )
    except (httpx.HTTPError, httpx.InvalidURL):
        return None
    if max_bytes is not None:
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > max_bytes:
                    logger.debug(
                        "sitemap at %s exceeds cap (%s bytes); skipping",
                        url,
                        cl,
                    )
                    return None
            except ValueError:
                pass
    return resp


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def sitemap_urls_from_robots(
    base_url: str,
    client: httpx.AsyncClient,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[str]:
    """Return the list of sitemap URLs advertised in robots.txt (if any)."""
    origin = _origin(base_url)
    resp = await _get(f"{origin}/robots.txt", client, user_agent)
    if resp is None or resp.status_code >= 400:
        return []
    return _SITEMAP_RE.findall(resp.text)


def parse_sitemap_xml(text: str) -> list[SitemapEntry]:
    """Parse sitemap XML (urlset or sitemapindex). Nested indexes expand to
    their child ``loc`` entries marked with ``lastmod=None`` for the caller
    to follow if desired.
    """
    # selectolax's HTMLParser handles sloppy XML well enough for sitemaps.
    tree = HTMLParser(text)
    entries: list[SitemapEntry] = []
    for url_node in tree.css("url"):
        loc = url_node.css_first("loc")
        if not loc or not loc.text(strip=True):
            continue
        lm = url_node.css_first("lastmod")
        entries.append(
            SitemapEntry(
                url=loc.text(strip=True),
                lastmod=lm.text(strip=True) if lm else None,
            )
        )
    if entries:
        return entries
    # Sitemap index: just return the child sitemap URLs.
    for sm in tree.css("sitemap"):
        loc = sm.css_first("loc")
        if loc and loc.text(strip=True):
            entries.append(SitemapEntry(url=loc.text(strip=True), lastmod=None))
    return entries


async def discover_sitemap_urls(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    follow_index: bool = True,
    max_sitemaps: int = 50,
) -> list[SitemapEntry]:
    """Discover sitemap URLs for ``base_url``.

    Resolution order:
    1. ``robots.txt`` ``Sitemap:`` entries.
    2. ``<origin>/sitemap.xml`` fallback.

    If the response is a sitemap-index and ``follow_index`` is true,
    child sitemaps are fetched and merged (up to ``max_sitemaps``).
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient()
    try:
        sitemap_urls = await sitemap_urls_from_robots(
            base_url, client, user_agent
        )
        if not sitemap_urls:
            sitemap_urls = [f"{_origin(base_url)}/sitemap.xml"]

        seen_sitemaps: set[str] = set()
        queue = list(sitemap_urls)
        entries: list[SitemapEntry] = []
        while queue and len(seen_sitemaps) < max_sitemaps:
            sm_url = queue.pop(0)
            if sm_url in seen_sitemaps:
                continue
            seen_sitemaps.add(sm_url)
            resp = await _get(
                sm_url, client, user_agent, max_bytes=MAX_SITEMAP_BYTES
            )
            if resp is None or resp.status_code >= 400:
                continue
            parsed = parse_sitemap_xml(resp.text)
            # Heuristic: if every entry has no lastmod and the doc tag is
            # ``<sitemapindex>``, follow.
            if follow_index and "<sitemapindex" in resp.text[:200].lower():
                for e in parsed:
                    if e.url not in seen_sitemaps:
                        queue.append(e.url)
                continue
            entries.extend(parsed)
        return entries
    finally:
        if owns_client:
            await client.aclose()
