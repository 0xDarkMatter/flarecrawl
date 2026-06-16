# MCP Wiring Snippet

**Status**: ARCHIVED — applied and shipped in v0.31.0 (2026-06-13). Frozen record.
**Note**: The `mcp` subcommand now lives in the `src/flarecrawl/cli/` package
(the cli.py monolith was split). The code below is the original staged snippet,
kept for history; it references the pre-split `cli.py` path.

---

## Typer subcommand to add to cli.py

Add the following `mcp` subcommand to the main `app` in `src/flarecrawl/cli.py`,
alongside the existing top-level commands.

```python
# In src/flarecrawl/cli.py, after the existing imports and app definition:

@app.command("mcp")
def mcp_cmd(
    read_only: bool = typer.Option(
        False,
        "--read-only",
        help="Start in read-only mode (excludes page_interact, site_download, p6_raw, "
             "recipe_run_raw, spider_raw).",
    ),
) -> None:
    """Start MCP stdio server for this tool.

    Exposes 36 tools (31 in read-only mode) via the Model Context Protocol:
    5 orientation + 5 T1 composite + 17 T2 curated + 9 T3 raw.

    Requires the mcp extra:
        uv pip install 'flarecrawl[mcp]'

    Usage in Claude Code (.mcp.json):
        {
          "mcpServers": {
            "flarecrawl": {
              "command": "flarecrawl",
              "args": ["mcp"]
            }
          }
        }
    """
    try:
        from flarecrawl.mcp_serve import serve
    except ImportError:
        import typer
        typer.echo(
            "The 'mcp' package is not installed. "
            "Install it with: uv pip install 'flarecrawl[mcp]'",
            err=True,
        )
        raise typer.Exit(1)

    serve(read_only=read_only)
```

---

## .mcp.json snippet

Add to `.mcp.json` or Claude Code settings to wire up flarecrawl as an MCP server:

```json
{
  "mcpServers": {
    "flarecrawl": {
      "command": "flarecrawl",
      "args": ["mcp"],
      "description": "Flarecrawl web scraping and crawling MCP server"
    }
  }
}
```

For read-only mode:

```json
{
  "mcpServers": {
    "flarecrawl": {
      "command": "flarecrawl",
      "args": ["mcp", "--read-only"]
    }
  }
}
```

---

## Notes

- The `mcp` subcommand does lazy import of `flarecrawl.mcp_serve`, which in turn
  lazily imports `mcp`. This ensures the base CLI works without the mcp extra.
- The `serve()` function handles its own asyncio loop via `asyncio.run()`.
- No `transport` / `port` arguments needed initially — stdio is the only transport.
