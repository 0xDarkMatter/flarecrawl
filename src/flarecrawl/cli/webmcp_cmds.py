"""webmcp sub-app — WebMCP tool discovery and execution."""

from __future__ import annotations

import json
import time as _time
from typing import Annotated

import typer

from ..client import FlareCrawlError
from ._common import (
    EXIT_VALIDATION,
    _error,
    _get_cdp_client,
    _handle_api_error,
    _output_json,
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
    directly — no HTML scraping needed. Requires Chrome 146+ (CF lab pool).

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

