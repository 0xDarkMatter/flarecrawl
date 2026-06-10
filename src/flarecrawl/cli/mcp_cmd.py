"""mcp command — start the MCP stdio server."""

from __future__ import annotations

from typing import Annotated

import typer

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command("mcp")
def mcp_cmd(
    read_only: Annotated[
        bool,
        typer.Option(
            "--read-only",
            help=(
                "Start in read-only mode (excludes page_interact, site_download, "
                "p6_raw, recipe_run_raw, spider_raw)."
            ),
        ),
    ] = False,
) -> None:
    """Start MCP stdio server for this tool.

    Exposes 36 tools (31 in read-only mode) via the Model Context Protocol:
    5 orientation + 5 T1 composite + 17 T2 curated + 9 T3 raw.

    Requires the mcp extra:
        uv pip install 'flarecrawl[mcp]'

    Usage in Claude Code (.mcp.json):
        {"mcpServers": {"flarecrawl": {"command": "flarecrawl", "args": ["mcp"]}}}
    """
    try:
        from ..mcp_serve import serve
    except ImportError:
        typer.echo(
            "The 'mcp' package is not installed. "
            "Install it with: uv pip install 'flarecrawl[mcp]'",
            err=True,
        )
        raise typer.Exit(1) from None

    serve(read_only=read_only)


def register(app: typer.Typer) -> None:
    """Register direct commands onto the main app."""
    app.command("mcp")(mcp_cmd)
