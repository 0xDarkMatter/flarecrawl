# MCP Documentation Snippets

**Status**: Staged for later application.
**Reason**: README.md and AGENTS.md have uncommitted edits in the main repo.
**Apply when**: The parallel cli.py split + README/AGENTS edits land.

---

## README.md — Stage 2.6 MCP section

Insert after the "CLI Usage" section and before "Configuration":

```markdown
## MCP Server

Flarecrawl exposes a [Model Context Protocol](https://modelcontextprotocol.io/) server
so AI agents (Claude Code, Cursor, etc.) can use flarecrawl tools directly.

**Install the MCP extra:**

```bash
uv pip install 'flarecrawl[mcp]'
```

**Wire up in Claude Code** (`.mcp.json`):

```json
{
  "mcpServers": {
    "flarecrawl": {
      "command": "flarecrawl",
      "args": ["mcp"]
    }
  }
}
```

**36 tools** across 4 groups:

| Group | Count | Description |
|-------|------:|-------------|
| Orientation | 5 | `capabilities`, `guide`, `diagnostics`, `permissions_check`, `schema_generate` |
| T1 Composite | 5 | `read_page`, `research_web`, `site_overview`, `extract_data`, `check_page_changes` |
| T2 Curated | 17 | `web_search`, `fetch_url`, `page_screenshot`, `tech_detect`, `crawl_start`, ... |
| T3 Raw | 9 | `scrape_raw`, `p6_raw`, `spider_raw`, ... (full CLI fidelity) |

**First call:**

```python
# In any MCP session — call capabilities() first for the full tool catalogue
capabilities()
```

**Read-only mode** (excludes write/destructive tools):

```bash
flarecrawl mcp --read-only
```

**Coverage gaps** (CLI commands not exposed via MCP):
`videos`, `authcrawl`, `frontier`, `batch`, `auth login/logout`,
`cache clear`, `rules *`, `cdp *`, `webmcp *`, `--interactive/--headed` flags.
These require the CLI — see `flarecrawl guide` for usage.
```

---

## AGENTS.md — Stage 2.6 MCP section

Insert after the "## Authentication" section:

```markdown
## MCP Server

Flarecrawl exposes an MCP server with **36 tools** for AI agent use.

### Quick setup

```bash
uv pip install 'flarecrawl[mcp]'
```

Add to `.mcp.json`:
```json
{"mcpServers": {"flarecrawl": {"command": "flarecrawl", "args": ["mcp"]}}}
```

### Always call first

```python
capabilities()  # Returns full tool catalogue, personas, recipes, known limitations
```

### Tool tiers

| Tier | When to use | Example |
|------|-------------|---------|
| **Orientation** | Session start, capability check | `capabilities()`, `diagnostics()` |
| **T1 Composite** | 80% of tasks | `read_page(url)`, `research_web(query)` |
| **T2 Curated** | Single-resource ops | `web_search()`, `tech_detect()`, `crawl_start()` |
| **T3 Raw** | Full CLI fidelity needed | `scrape_raw(url, options={...})` |

### Error handling

All errors return `{"ok": false, "error": {"code": ..., "next_steps": [...]}}`.
`meta.blocked` verdicts include escalation suggestions automatically:
- `vendor=akamai` → try `scrape_raw` with `stealth=True`, then `p6_raw`
- `kind=cf_1020_hard` or `terminal=true` → do not retry (hard block)
- `exit_code=2` → AUTH_REQUIRED → run `flarecrawl auth login`

### Read-only mode

```bash
flarecrawl mcp --read-only
```

Excludes: `page_interact`, `site_download`, `p6_raw`, `recipe_run_raw`, `spider_raw`.

### Coverage gaps (CLI only)

`videos`, `authcrawl`, `frontier`, `batch`, `auth login/logout`,
`cache clear`, `rules *`, `cdp *`, `webmcp *`, `--interactive/--headed`.

See `diagnostics()` for quota/auth status, `permissions_check(action)` for capability preflight.
```
