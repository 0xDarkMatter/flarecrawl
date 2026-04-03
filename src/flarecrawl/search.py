"""Web search via Jina Search API for Flarecrawl.

Provides web search capabilities using Jina's free Search API.
Results can be returned as-is or piped into the scrape pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import os

import httpx


_JINA_SEARCH_URL = "https://s.jina.ai/"


def _get_jina_api_key() -> str | None:
    """Get Jina API key from env var."""
    return os.environ.get("JINA_API_KEY", "").strip() or None


@dataclass
class SearchResult:
    """A single search result."""

    url: str
    title: str
    snippet: str


def jina_search(
    query: str,
    *,
    limit: int = 10,
    timeout: int = 15,
    proxy: str | None = None,
) -> list[SearchResult]:
    """Search the web via Jina Search API.

    Args:
        query: Search query string.
        limit: Maximum number of results.
        timeout: Request timeout in seconds.
        proxy: Optional proxy URL.

    Returns:
        List of SearchResult objects.
    """
    api_key = _get_jina_api_key()
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "text",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        proxy=proxy,
    ) as client:
        resp = client.get(
            f"{_JINA_SEARCH_URL}{query}",
            headers=headers,
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "Jina Search requires an API key. "
                "Set JINA_API_KEY env var (free at https://jina.ai/api-key)"
            )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in (data.get("data", []) or [])[:limit]:
        results.append(SearchResult(
            url=item.get("url", ""),
            title=item.get("title", ""),
            snippet=item.get("description", item.get("content", ""))[:500],
        ))
    return results
