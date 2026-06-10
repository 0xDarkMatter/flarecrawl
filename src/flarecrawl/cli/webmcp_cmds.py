"""webmcp sub-app — WebMCP tool discovery and execution."""

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


webmcp_app = typer.Typer(help="WebMCP tool discovery and execution")


@webmcp_app.command("discover")
def webmcp_discover(
    url: Annotated[str, typer.Argument(help="URL to discover WebMCP tools on")],
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep session alive")] = 60,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
):
    """Discover WebMCP tools exposed by a website.

    WebMCP lets sites declare structured tools that AI agents can call
    directly â€” no HTML scraping needed. Requires Chrome 146+ (CF lab pool).

    Example:
        flarecrawl webmcp discover https://hotel-site.com --json
    """
    from ..cdp import CDPError

    _validate_url(url, json_output)
    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()
        page.navigate(url, wait_until="networkidle0")

        try:
            tools = page.webmcp_list_tools()
        except (CDPError, FlareCrawlError) as e:
            if "not supported" in str(e).lower():
                if json_output:
                    _output_json({"data": {"tools": [], "supported": False}, "meta": {"url": url}})
                else:
                    console.print("[yellow]WebMCP not supported[/yellow] on this page")
                    console.print("[dim]Requires Chrome 146+ via CF lab pool[/dim]")
                return
            raise

        if json_output:
            _output_json({
                "data": {"tools": tools, "supported": True, "count": len(tools)},
                "meta": {"url": url},
            })
        else:
            if not tools:
                console.print(f"[dim]No WebMCP tools found on {url}[/dim]")
            else:
                console.print(f"\n[bold]WebMCP Tools[/bold] ({len(tools)} found)\n")
                for tool in tools:
                    console.print(f"  [cyan]{tool.get('name', '?')}[/cyan]")
                    if tool.get("description"):
                        console.print(f"    {tool['description']}")
                    if tool.get("inputSchema"):
                        props = tool["inputSchema"].get("properties", {})
                        if props:
                            params = ", ".join(f"{k}: {v.get('type', '?')}" for k, v in props.items())
                            console.print(f"    [dim]params: {params}[/dim]")
                    console.print()

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


@webmcp_app.command("call")
def webmcp_call(
    url: Annotated[str, typer.Argument(help="URL with WebMCP tools")],
    tool: Annotated[str, typer.Option("--tool", "-t", help="Tool name to execute")] = ...,
    params: Annotated[str | None, typer.Option("--params", "-p", help="Tool parameters as JSON")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep session alive")] = 60,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
):
    """Execute a WebMCP tool on a website.

    First discover available tools with 'webmcp discover', then call them.

    Example:
        flarecrawl webmcp call https://hotel.com --tool searchHotels --params '{"city": "Paris"}' --json
    """
    _validate_url(url, json_output)

    parsed_params = None
    if params:
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError as e:
            _error(f"Invalid JSON params: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()
        page.navigate(url, wait_until="networkidle0")

        start = _time.time()
        result = page.webmcp_execute(tool, parsed_params)
        elapsed = _time.time() - start

        if json_output:
            _output_json({
                "data": {"tool": tool, "params": parsed_params, "result": result, "elapsed": round(elapsed, 2)},
                "meta": {"url": url},
            })
        else:
            console.print(f"\n[bold]{tool}[/bold] returned:\n")
            if isinstance(result, (dict, list)):
                console.print(json.dumps(result, indent=2))
            else:
                console.print(str(result))
            console.print(f"\n[dim]{elapsed:.2f}s[/dim]")

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


# ------------------------------------------------------------------
# Design extraction
# ------------------------------------------------------------------

