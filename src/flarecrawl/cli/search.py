"""search command."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time as _time
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from .. import __version__
from ..batch import parse_batch_file, process_batch
from ..client import MOBILE_PRESET, Client, FlareCrawlError
from ..config import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_WORKERS,
    clear_cdp_session,
    clear_credentials,
    get_account_id,
    get_api_token,
    get_auth_status,
    get_usage,
    list_cdp_sessions,
    load_cdp_session,
    save_cdp_session,
    save_credentials,
)
from ._common import (
    EXIT_AUTH_REQUIRED,
    EXIT_ERROR,
    EXIT_FORBIDDEN,
    EXIT_NOT_FOUND,
    EXIT_RATE_LIMITED,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
    _apply_browser_cookies,
    _apply_tech_detection,
    _attach_tech,
    _classify_url_for_organize,
    _collect_response_signals,
    _enrich_cdp_error,
    _error,
    _filter_detections,
    _filter_fields,
    _filter_record_content,
    _get_cdp_client,
    _get_client,
    _handle_api_error,
    _output_json,
    _output_ndjson,
    _output_text,
    _parse_auth,
    _parse_body,
    _parse_category_list,
    _parse_headers,
    _require_auth,
    _run_then_fetch,
    _sanitize_filename,
    _validate_url,
    console,
)


# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 10,
    scrape_results: Annotated[bool, typer.Option("--scrape", help="Also scrape each result URL")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
    paywall: Annotated[bool, typer.Option("--paywall", help="Paywall bypass for scraped URLs")] = False,
    stealth: Annotated[bool, typer.Option("--stealth", help="Stealth mode for scraped URLs")] = False,
    only_main_content: Annotated[bool, typer.Option("--only-main-content", help="Main content only")] = False,
    clean: Annotated[bool, typer.Option("--clean", help="Strip ads from scraped HTML")] = False,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for --scrape (max 50)")] = 3,
):
    """Search the web and optionally scrape results.

    Backed by Jina's Search API. Jina requires its own free API key
    (this is a Jina dependency, not a Flarecrawl quota): get one at
    https://jina.ai/api-key and export JINA_API_KEY=<key>.

    Example:
        flarecrawl search "python web scraping" --json
        flarecrawl search "topic" --scrape --limit 5 --json
        flarecrawl search "query" --json | jq '.data[].url'
    """
    from ..search import jina_search
    from ..config import get_proxy

    effective_proxy = proxy or get_proxy()

    try:
        results = jina_search(query, limit=limit, proxy=effective_proxy)
    except RuntimeError as e:
        # jina_search raises RuntimeError specifically for the missing/invalid
        # API-key case â€” surface it as an auth error with the actionable hint.
        _error(str(e), "AUTH_REQUIRED", EXIT_AUTH_REQUIRED, as_json=json_output)
        return
    except Exception as e:
        _error(f"Search failed: {e}", "SEARCH_ERROR", EXIT_ERROR, as_json=json_output)
        return

    data = [{"url": r.url, "title": r.title, "snippet": r.snippet} for r in results]

    if scrape_results and data:
        cache_ttl = DEFAULT_CACHE_TTL
        if paywall:
            _has_creds = get_account_id() and get_api_token()
            client = _get_client(True, cache_ttl=cache_ttl, proxy=effective_proxy) if _has_creds else None
        else:
            client = _get_client(True, cache_ttl=cache_ttl, proxy=effective_proxy)

        for item in data:
            try:
                result = _scrape_single(
                    client, item["url"], "markdown", None, False, False,
                    None, None, paywall=paywall, stealth=stealth,
                    only_main_content=only_main_content, clean=clean,
                    proxy=effective_proxy,
                )
                item["content"] = result.get("content", "")
                item["metadata"] = result.get("metadata", {})
            except Exception as e:
                item["content"] = ""
                item["error"] = str(e)

    meta = {"count": len(data), "query": query}

    if json_output:
        _output_json({"data": data, "meta": meta})
    else:
        for i, item in enumerate(data, 1):
            console.print(f"\n[bold]{i}. {item['title']}[/bold]")
            console.print(f"[dim]{item['url']}[/dim]")
            console.print(item["snippet"])
            if "content" in item and item["content"]:
                console.print(f"\n{'â”€' * 60}")
                content = item["content"]
                if len(content) > 2000:
                    content = content[:2000] + "\n\n[dim]... truncated[/dim]"
                _output_text(content)


# ------------------------------------------------------------------
# fetch â€” content-type aware download
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('search')(search)
