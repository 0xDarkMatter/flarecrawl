"""MCP transport entry point for flarecrawl.

This module is the ONLY place where ``mcp`` is imported.  All tool handlers
live in ``flarecrawl.mcp_tools`` and are importable without ``mcp``.

Usage (via CLI):
    flarecrawl mcp serve
    flarecrawl mcp serve --read-only

Usage (programmatic):
    from flarecrawl.mcp_serve import serve
    serve()          # stdio, full mode
    serve(read_only=True)   # read-only mode

The ``mcp`` package must be installed:
    uv pip install 'flarecrawl[mcp]'
"""

from __future__ import annotations

import json
from typing import Any

from flarecrawl.mcp_tools.registry import build_registry

# ---------------------------------------------------------------------------
# Argument dispatch helpers
# ---------------------------------------------------------------------------


def _dispatch(handler: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a handler with the provided arguments dict.

    Filters out None values and maps argument names to handler params.
    """
    import inspect

    sig = inspect.signature(handler)
    params = sig.parameters

    # Build kwargs: only pass args that the handler accepts
    kwargs: dict[str, Any] = {}
    for name, param in params.items():
        if name in arguments:
            kwargs[name] = arguments[name]
        elif param.default is inspect.Parameter.empty:
            # Required param missing — let the handler raise naturally
            pass

    return handler(**kwargs)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def serve(read_only: bool = False) -> None:
    """Start the flarecrawl MCP stdio server.

    Parameters
    ----------
    read_only:
        Exclude write/destructive tools (page_interact, site_download,
        p6_raw, recipe_run_raw, spider_raw).
    """
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise ImportError(
            "The 'mcp' package is required to run the MCP server. "
            "Install it with: uv pip install 'flarecrawl[mcp]'"
        ) from exc

    import asyncio

    registry = build_registry(read_only=read_only)

    # Build MCP Tool objects from registry
    tools: list[Tool] = []
    for name, defn in registry.items():
        # Annotations
        is_mutating = name in {"page_interact", "site_download", "p6_raw", "recipe_run_raw",
                                "spider_raw", "crawl_start", "crawl_raw"}
        tools.append(
            Tool(
                name=name,
                description=defn.get("short_description", ""),
                inputSchema=defn.get("parameters", {"type": "object", "properties": {}, "required": []}),
                annotations={
                    "readOnlyHint": not is_mutating,
                    "destructiveHint": is_mutating,
                    "openWorldHint": False,
                },
            )
        )

    server = Server("flarecrawl")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        # Unknown tool
        if name not in registry:
            if name in {"page_interact", "site_download", "p6_raw", "recipe_run_raw", "spider_raw"} and read_only:
                from flarecrawl.mcp_tools._errors import permission_denied
                result = permission_denied(
                    f"Tool '{name}' is not available in read-only mode.",
                    tool_name=name,
                    next_steps=[
                        {
                            "try": "Restart server without --read-only",
                            "with": {},
                            "why": "This tool is excluded in read-only mode.",
                        }
                    ],
                )
            else:
                result = {
                    "ok": False,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"Unknown tool: {name}",
                        "category": "not_found",
                        "next_steps": [
                            {"try": "capabilities", "with": {}, "why": "Browse the full tool catalogue."}
                        ],
                    },
                }
            return [TextContent(type="text", text=json.dumps(result))]

        handler = registry[name]["handler"]

        try:
            result = _dispatch(handler, arguments or {})
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": str(exc),
                    "category": "upstream_error",
                    "tool": name,
                    "next_steps": [
                        {"try": "diagnostics", "with": {}, "why": "Check server health."}
                    ],
                },
            }

        return [TextContent(type="text", text=json.dumps(result))]

    asyncio.run(stdio_server(server).run())
