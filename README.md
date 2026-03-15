# FlareCrawl

[![GitHub](https://img.shields.io/badge/github-0xDarkMatter%2Fflarecrawl-blue?logo=github)](https://github.com/0xDarkMatter/flarecrawl)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Cloudflare](https://img.shields.io/badge/cloudflare-browser--rendering-orange?logo=cloudflare)](https://developers.cloudflare.com/browser-rendering/)

> Cloudflare Browser Rendering CLI — drop-in replacement for `firecrawl`, much cheaper.

CLI that wraps Cloudflare's [Browser Rendering REST API](https://developers.cloudflare.com/browser-rendering/rest-api/) with the same command structure as `firecrawl`. Supports scraping, crawling, URL discovery, screenshots, PDFs, and AI-powered data extraction — all running on Cloudflare's headless Chromium infrastructure. Significantly cheaper than Firecrawl at scale (free 10 min/day, then $0.09/hr vs per-page credit pricing).

## Why FlareCrawl?

| | Firecrawl | FlareCrawl |
|---|---|---|
| **Pricing** | ~$0.003-0.01/page (credits) | Free 10 min/day, then $0.09/hr |
| **30-page crawl** | ~$0.09-0.30 | **$0.0013** |
| **1,000-page crawl** | ~$3-10 | **~$0.025** |
| **JS rendering** | Yes | Yes (headless Chromium on edge) |
| **PDF generation** | No | Yes |
| **AI extraction** | Spark models (paid) | Workers AI (included) |
| **Self-hosted option** | Yes (complex) | Cloudflare infrastructure |
| **Web search** | Yes | No |

## Install

```bash
cd X:\Fabric\FlareCrawl
uv venv && uv pip install -e .
```

## Setup

1. **Create an API token** at https://dash.cloudflare.com/profile/api-tokens
2. Add permission: **Account → Browser Rendering → Edit**
3. Authenticate:

```bash
flarecrawl auth login
# Or non-interactive:
flarecrawl auth login --account-id YOUR_ACCOUNT_ID --token YOUR_TOKEN
```

4. Verify:

```bash
flarecrawl auth status
flarecrawl --status   # Shows auth + pricing info
```

### Environment Variables (CI/CD)

```bash
export FLARECRAWL_ACCOUNT_ID="your-account-id"
export FLARECRAWL_API_TOKEN="your-api-token"
```

## Commands

### scrape — Fetch page content

```bash
# Default: markdown output to stdout
flarecrawl scrape https://example.com

# HTML output
flarecrawl scrape https://example.com --format html

# JSON envelope (for piping)
flarecrawl scrape https://example.com --json

# Multiple URLs (scraped concurrently, up to 3 at a time)
flarecrawl scrape https://a.com https://b.com https://c.com --json

# From a file of URLs
flarecrawl scrape --input urls.txt --json

# With timing info
flarecrawl scrape https://example.com --timing

# Filter JSON fields
flarecrawl scrape https://example.com --json --fields url,content

# Extract links only
flarecrawl scrape https://example.com --format links --json

# Take screenshot via scrape
flarecrawl scrape https://example.com --screenshot -o page.png
```

**Formats:** `markdown` (default), `html`, `links`, `screenshot`, `json` (AI extraction)

### crawl — Crawl a website

```bash
# Start crawl and wait for results
flarecrawl crawl https://example.com --wait --limit 50

# With progress indicator
flarecrawl crawl https://example.com --wait --progress --limit 100

# Fire and forget (returns job ID)
flarecrawl crawl https://example.com --limit 50

# Check status of running crawl
flarecrawl crawl JOB_ID --status

# Get results from completed crawl
flarecrawl crawl JOB_ID

# Filter paths
flarecrawl crawl https://docs.example.com --wait --limit 200 \
  --include-paths "/docs,/api" --exclude-paths "/zh,/ja"

# Stream results as NDJSON (one record per line)
flarecrawl crawl JOB_ID --ndjson --fields url,markdown

# Skip JS rendering for faster crawl
flarecrawl crawl https://example.com --wait --limit 100 --no-render

# Follow subdomains
flarecrawl crawl https://example.com --wait --allow-subdomains

# Save to file
flarecrawl crawl https://example.com --wait --limit 50 -o results.json
```

### map — Discover URLs

```bash
# List all links on a page
flarecrawl map https://example.com

# JSON output
flarecrawl map https://example.com --json

# Include subdomains
flarecrawl map https://example.com --include-subdomains

# Limit results
flarecrawl map https://example.com --limit 20 --json
```

### download — Save site to disk

```bash
# Download as markdown files to .flarecrawl/
flarecrawl download https://docs.example.com --limit 50

# Download as HTML
flarecrawl download https://example.com --limit 20 --format html

# Filter paths
flarecrawl download https://docs.example.com --limit 100 \
  --include-paths "/docs" --exclude-paths "/changelog"

# Skip confirmation prompt
flarecrawl download https://example.com --limit 10 -y
```

Files are saved to `.flarecrawl/<domain>/` with sanitized filenames.

### extract — AI-powered data extraction

```bash
# Extract structured data with a natural language prompt
flarecrawl extract "Get all product names and prices" \
  --urls https://shop.example.com --json

# With JSON schema for structured output
flarecrawl extract "Extract article metadata" \
  --urls https://blog.example.com \
  --schema '{"type":"json_schema","schema":{"type":"object","properties":{"title":{"type":"string"},"date":{"type":"string"}}}}'

# Schema from file
flarecrawl extract "Extract data" --urls https://example.com --schema-file schema.json

# Multiple URLs
flarecrawl extract "Get page title" --urls https://a.com,https://b.com --json
```

Uses Cloudflare Workers AI for extraction (no additional cost).

### screenshot — Capture web pages

```bash
# Default: saves to screenshot.png
flarecrawl screenshot https://example.com

# Custom output path
flarecrawl screenshot https://example.com -o hero.png

# Full page
flarecrawl screenshot https://example.com -o full.png --full-page

# Specific element
flarecrawl screenshot https://example.com --selector "main" -o main.png

# Custom viewport
flarecrawl screenshot https://example.com --width 1440 --height 900 -o wide.png

# JPEG format
flarecrawl screenshot https://example.com --format jpeg -o page.jpg

# JSON output (base64 encoded)
flarecrawl screenshot https://example.com --json
```

### pdf — Render pages as PDF

```bash
# Default: saves to page.pdf
flarecrawl pdf https://example.com

# Custom output
flarecrawl pdf https://example.com -o report.pdf

# Landscape A4
flarecrawl pdf https://example.com -o report.pdf --landscape --format a4

# JSON output (base64 encoded)
flarecrawl pdf https://example.com --json
```

### usage — Track browser time

```bash
# Show today's usage and history
flarecrawl usage

# JSON output
flarecrawl usage --json
```

Tracks the `X-Browser-Ms-Used` header from each API response locally. Free tier is 600,000ms (10 minutes) per day.

### auth — Authentication

```bash
flarecrawl auth login                    # Interactive
flarecrawl auth login --account-id ID --token TOKEN  # Non-interactive
flarecrawl auth status                   # Human-readable
flarecrawl auth status --json            # Machine-readable
flarecrawl auth logout                   # Clear credentials
```

## Firecrawl Compatibility

FlareCrawl is designed as a drop-in replacement for the `firecrawl` CLI:

| firecrawl command | flarecrawl equivalent | Notes |
|---|---|---|
| `firecrawl scrape URL` | `flarecrawl scrape URL` | Same flags |
| `firecrawl scrape URL1 URL2` | `flarecrawl scrape URL1 URL2` | Concurrent (3 workers) |
| `firecrawl crawl URL --wait` | `flarecrawl crawl URL --wait` | Same flags |
| `firecrawl map URL` | `flarecrawl map URL` | Same flags |
| `firecrawl download URL` | `flarecrawl download URL` | Saves to `.flarecrawl/` |
| `firecrawl agent PROMPT` | `flarecrawl extract PROMPT` | Uses Workers AI |
| `firecrawl credit-usage` | `flarecrawl usage` | Local tracking |
| `firecrawl search QUERY` | **Not supported** | No CF equivalent |
| `firecrawl --status` | `flarecrawl --status` | Same |

### What's different

- **No `search` command** — Cloudflare doesn't have a web search API
- **`extract` instead of `agent`** — same concept, different name to avoid confusion
- **PDF command** — bonus: Cloudflare supports PDF rendering, Firecrawl doesn't
- **Output directory** — `.flarecrawl/` instead of `.firecrawl/`

## Output Format (Fabric Protocol)

All `--json` output follows the Fabric Protocol envelope:

```json
{
  "data": { ... },
  "meta": { "format": "markdown", "count": 1 }
}
```

Errors:

```json
{
  "error": { "code": "AUTH_REQUIRED", "message": "Not authenticated..." }
}
```

### Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Continue |
| 1 | Error | Check stderr for details |
| 2 | Auth required | Run `flarecrawl auth login` |
| 3 | Not found | Check job ID |
| 4 | Validation | Fix arguments |
| 5 | Forbidden | Check token permissions |
| 7 | Rate limited | Wait and retry |

## Advanced Usage

### Raw JSON body passthrough

Every command supports `--body` to send a raw JSON payload directly to the CF API, bypassing all flag processing:

```bash
flarecrawl scrape --body '{
  "url": "https://example.com",
  "gotoOptions": {"waitUntil": "networkidle0", "timeout": 60000},
  "rejectResourceTypes": ["image", "media"]
}' --json
```

### Piping and chaining

```bash
# Map URLs then scrape them
flarecrawl map https://docs.example.com --json | \
  jq -r '.data[]' | head -10 > urls.txt
flarecrawl scrape --input urls.txt --json -o results.json

# Crawl and extract just the markdown
flarecrawl crawl https://example.com --wait --limit 10 --json | \
  jq -r '.data.records[] | select(.status=="completed") | .markdown'

# Stream crawl results through jq
flarecrawl crawl JOB_ID --ndjson --fields url,markdown | \
  jq -r '.url + "\t" + (.markdown | length | tostring)'
```

### Retry behavior

Requests automatically retry up to 3 times on HTTP 429 (rate limited), 502, and 503 errors with exponential backoff. Timeouts also trigger retries.

## Pricing Details

| Tier | Browser Time | Concurrent Browsers |
|------|-------------|-------------------|
| **Free** | 10 min/day | 3 max |
| **Paid** | 10 hr/month included, then $0.09/hr | 10 (averaged monthly), +$2/extra |

Browser time is shared between REST API calls and Workers bindings. Track your usage with `flarecrawl usage`.

## Project Structure

```
X:/Fabric/FlareCrawl/
├── pyproject.toml              # Package config + [tool.fabric] metadata
├── AGENTS.md                   # AI agent context
├── README.md                   # This file
├── src/flarecrawl/
│   ├── __init__.py             # Version
│   ├── cli.py                  # Typer CLI (all commands)
│   ├── client.py               # CF Browser Rendering API client
│   └── config.py               # Credentials + usage tracking
└── tests/
    ├── conftest.py             # Test fixtures
    ├── test_cli.py             # CLI tests (46 tests)
    └── test_client.py          # Client tests
```

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/

# Reinstall after changes
uv pip install -e .
```

## License

MIT
