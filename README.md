# 🔥 Flarecrawl CLI

[![GitHub](https://img.shields.io/badge/github-0xDarkMatter%2Fflarecrawl-blue?logo=github)](https://github.com/0xDarkMatter/flarecrawl)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Cloudflare](https://img.shields.io/badge/cloudflare-browser--rendering-orange?logo=cloudflare)](https://developers.cloudflare.com/browser-rendering/)

> Cloudflare Browser Rendering CLI — Firecrawl-compatible, cost-efficient at scale.

CLI that wraps Cloudflare's [Browser Rendering REST API](https://developers.cloudflare.com/browser-rendering/rest-api/) with the same command structure as Firecrawl. Supports scraping, crawling, URL discovery, screenshots, PDFs, and AI-powered data extraction — all running on Cloudflare's headless Chromium infrastructure. Cost-efficient alternative for high-volume use cases (free 10 min/day, then $0.09/hr).

## Recent Updates

| Version | Date | Changes |
|---------|------|---------|
| **v0.7.0** | 2026-03-20 | `--archived` (Wayback fallback), `--language`, `--magic` (cookie banner removal), filename collision fixes, 197 tests |
| **v0.6.1** | 2026-03-19 | `--backup-dir` for raw HTML archival, discover edge case fixes, 187 tests |
| **v0.6.0** | 2026-03-19 | `--selector`, `--js-eval`, `--wait-for-selector`, `--stdin`, `--har`, `flarecrawl discover` command, 185 tests |
| **v0.5.4** | 2026-03-19 | `--user-agent` on all commands for custom crawler identity or paywall bypass |
| **v0.5.3** | 2026-03-19 | Guided `auth login` with browser auto-open for token setup |
| **v0.5.2** | 2026-03-19 | Content filtering on crawl/download, `--webhook` on crawl, summary+main-content combo, 169 unit tests |
| **v0.5.1** | 2026-03-19 | Feature test corpus (80 live tests across 8 sites), 158 unit tests, all green |
| **v0.5.0** | 2026-03-19 | `--only-main-content`, `--include-tags`/`--exclude-tags`, `--mobile`, `--headers`, `--diff`, formats: `images`/`summary`/`schema`, new `schema` command |
| **v0.4.0** | 2026-03-19 | `--auth user:pass` flag on all commands for HTTP Basic Auth protected sites |
| **v0.3.0** | 2026-03-19 | Batch mode, response caching, connection pooling, HTTP/2, env-var config, 100 tests |

## Why Flarecrawl?

| | Firecrawl | Flarecrawl |
|---|---|---|
| **Pricing model** | Per-page credits | Time-based (free 10 min/day, then $0.09/hr) |
| **JS rendering** | Yes | Yes (headless Chromium on edge) |
| **PDF generation** | No | Yes |
| **AI extraction** | Spark models | Workers AI (included) |
| **Favicon extraction** | Via branding format | Dedicated command |
| **Self-hosted option** | Yes | Cloudflare infrastructure |
| **Web search** | Yes | No |
| **Branding extraction** | Yes | Not yet |

Different pricing models suit different use cases. Flarecrawl's time-based pricing is particularly cost-efficient for high-volume crawls.

## Install

```bash
git clone https://github.com/0xDarkMatter/flarecrawl.git
cd flarecrawl
uv venv && uv pip install -e .
```

## Setup

### 1. Create an API token

1. Go to https://dash.cloudflare.com/profile/api-tokens
2. Click **Create Token**
3. Select **Create Custom Token**
4. Configure:
   - **Token name:** `Flarecrawl` (or anything)
   - **Permissions:** Account → Browser Rendering → Edit
   - **Account Resources:** Include → your account
5. Click **Continue to summary** → **Create Token**
6. Copy the token (shown only once)

### 2. Find your Account ID

1. Go to https://dash.cloudflare.com
2. Click any domain (or the account overview)
3. Look in the right sidebar under **Account ID**
4. Copy the 32-character hex string

### 3. Authenticate

```bash
# Interactive — opens browser to Cloudflare dashboard for guided setup
flarecrawl auth login

# Non-interactive (CI/CD)
flarecrawl auth login --account-id YOUR_ACCOUNT_ID --token YOUR_TOKEN
```

### 4. Verify

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

# Multiple URLs (scraped concurrently)
flarecrawl scrape https://a.com https://b.com https://c.com --json

# Batch mode: file input with NDJSON output and configurable workers
flarecrawl scrape --batch urls.txt --workers 5

# From a file of URLs (backward-compatible alias for --batch)
flarecrawl scrape --input urls.txt --json

# With timing info
flarecrawl scrape https://example.com --timing

# Filter JSON fields
flarecrawl scrape https://example.com --json --fields url,content

# Extract links only
flarecrawl scrape https://example.com --format links --json

# Take screenshot via scrape
flarecrawl scrape https://example.com --screenshot -o page.png

# Wait for JS rendering (SPAs, Swagger UIs)
flarecrawl scrape https://example.com --js

# Bypass response cache
flarecrawl scrape https://example.com --no-cache

# Custom page load strategy
flarecrawl scrape https://example.com --wait-until networkidle2
```

**Formats:** `markdown` (default), `html`, `links`, `screenshot`, `json` (AI extraction)

### HTTP Basic Auth

All commands support `--auth user:password` for sites protected by HTTP Basic Auth:

```bash
flarecrawl scrape https://intranet.example.com --auth admin:secret
flarecrawl crawl https://intranet.example.com --wait --limit 50 --auth admin:secret
flarecrawl download https://intranet.example.com --limit 20 --auth user:pass
flarecrawl screenshot https://intranet.example.com --auth user:pass -o page.png
```

### Content filtering

```bash
# Strip nav/header/footer, keep main article content
flarecrawl scrape https://example.com --only-main-content

# Keep only specific CSS selectors
flarecrawl scrape https://example.com --include-tags "article,.post"

# Remove specific elements
flarecrawl scrape https://example.com --exclude-tags "nav,footer,.sidebar"
```

### Custom headers & mobile

```bash
# Custom HTTP headers
flarecrawl scrape https://example.com --headers "Accept-Language: fr"
flarecrawl scrape https://example.com --headers '{"X-Api-Key": "abc123"}'

# Custom User-Agent (identify your crawler, or try bypassing paywalls)
flarecrawl scrape https://example.com --user-agent "MyBot/1.0 (contact@example.com)"
flarecrawl scrape https://paywalled.example.com --user-agent "Googlebot/2.1"

# Mobile device emulation (iPhone 14 Pro viewport)
flarecrawl scrape https://example.com --mobile
flarecrawl screenshot https://example.com --mobile -o mobile.png
```

### Images, summaries & structured data

```bash
# Extract all image URLs from a page
flarecrawl scrape https://example.com --format images --json

# AI-powered content summary
flarecrawl scrape https://example.com --format summary --json

# Extract LD+JSON, OpenGraph, Twitter Cards
flarecrawl scrape https://example.com --format schema --json

# Dedicated schema command with type filtering
flarecrawl schema https://example.com --json
flarecrawl schema https://example.com --type ld-json --json
flarecrawl schema https://example.com --type opengraph --json
```

### Webhooks

```bash
# POST crawl results to a URL when complete
flarecrawl crawl https://example.com --wait --limit 10 --webhook https://hooks.example.com/crawl

# With custom headers (e.g. auth token)
flarecrawl crawl https://example.com --wait --limit 10 \
  --webhook https://hooks.example.com/crawl \
  --webhook-headers "Authorization: Bearer token123"
```

### CSS selector extraction & JS execution

```bash
# Extract content from specific CSS selector
flarecrawl scrape https://example.com --selector "main" --json

# Wait for a CSS element before capturing (SPAs, lazy-load)
flarecrawl scrape https://example.com --wait-for-selector ".loaded" --json

# Run JavaScript and return the result
flarecrawl scrape https://example.com --js-eval "document.title" --json
flarecrawl scrape https://example.com --js-eval "document.querySelectorAll('a').length" --json
```

### Stdin piping & HAR capture

```bash
# Process local HTML without API call
cat page.html | flarecrawl scrape --stdin --only-main-content
curl https://example.com | flarecrawl scrape --stdin --format schema --json

# Save request metadata to HAR file
flarecrawl scrape https://example.com --har requests.har --json

# Save raw HTML alongside output (for archival/reprocessing)
flarecrawl scrape https://example.com --backup-dir ./html-backup
flarecrawl download https://example.com --limit 20 --backup-dir ./html-backup
```

### URL discovery

```bash
# Discover all URLs via sitemaps, RSS feeds, and page links
flarecrawl discover https://example.com --json

# Sitemaps only
flarecrawl discover https://example.com --no-feed --no-links --json

# With limit
flarecrawl discover https://example.com --limit 100 --json
```

### Cookie banner removal, language, archive fallback

```bash
# Remove cookie banners, GDPR modals, newsletter popups
flarecrawl scrape https://eu-site.example.com --magic

# Request content in a specific language
flarecrawl scrape https://example.com --language de

# Fallback to Internet Archive if page returns 404
flarecrawl scrape https://dead-link.example.com --archived
```

### Change tracking

```bash
# Compare current content against cached version
flarecrawl scrape https://example.com --diff --json
```

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

# Batch mode: parallel extraction with NDJSON output
flarecrawl extract "Get page title" --batch urls.txt --workers 5
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

### favicon — Extract favicon URL

```bash
# Get the best (largest) favicon
flarecrawl favicon https://example.com

# Show all found icons
flarecrawl favicon https://example.com --all

# JSON output
flarecrawl favicon https://example.com --json
flarecrawl favicon https://example.com --all --json
```

Renders the page, parses `<link rel="icon">`, `<link rel="apple-touch-icon">`, and related tags. Returns the largest icon found. Falls back to `/favicon.ico` if no `<link>` tags found.

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

### cache — Response cache management

```bash
flarecrawl cache status                  # Show entries, size, path
flarecrawl cache status --json           # Machine-readable
flarecrawl cache clear                   # Remove all cached responses
```

Responses are cached for 1 hour by default. Use `--no-cache` on any command to bypass.

### Performance features

- **Response caching** — 1-hour TTL, saves redundant browser renders
- **Connection pooling** — persistent httpx session with HTTP/2 support
- **Resource rejection** — skips images/fonts/media/stylesheets for text extraction
- **JS rendering** — opt-in via `--js` flag (waits for networkidle0)

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLARECRAWL_ACCOUNT_ID` | — | Cloudflare account ID |
| `FLARECRAWL_API_TOKEN` | — | Cloudflare API token |
| `FLARECRAWL_CACHE_TTL` | 3600 | Cache TTL in seconds |
| `FLARECRAWL_MAX_RETRIES` | 3 | Max retry attempts |
| `FLARECRAWL_MAX_WORKERS` | 10 | Max parallel workers |
| `FLARECRAWL_TIMEOUT` | 120 | Request timeout in seconds |

## Firecrawl Compatibility

Flarecrawl follows the same command structure as the `firecrawl` CLI:

| firecrawl command | flarecrawl equivalent | Notes |
|---|---|---|
| `firecrawl scrape URL` | `flarecrawl scrape URL` | Same flags |
| `firecrawl scrape URL1 URL2` | `flarecrawl scrape URL1 URL2` | Concurrent |
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
- **`favicon` command** — bonus: extract favicon/apple-touch-icon URLs from pages
- **`schema` command** — bonus: extract LD+JSON, OpenGraph, Twitter Cards
- **PDF command** — bonus: Cloudflare supports PDF rendering, Firecrawl doesn't
- **Output directory** — `.flarecrawl/` instead of `.firecrawl/`
- **`--only-main-content`** — client-side via BeautifulSoup (Firecrawl uses server-side extraction)

## Output Format

All `--json` output follows a consistent envelope:

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

## Batch & Parallel

Commands that operate on multiple URLs support batch mode with configurable parallelism.

### Batch input (`--batch`)

```bash
# Plain text file (one URL per line, # comments supported)
flarecrawl scrape --batch urls.txt --workers 5

# JSON array
flarecrawl scrape --batch urls.json

# NDJSON (one JSON object per line)
flarecrawl extract "Get title" --batch urls.ndjson --workers 3
```

Input format is auto-detected: starts with `[` → JSON array, starts with `{` → NDJSON, otherwise plain text.

### Batch output

Batch mode outputs **NDJSON** (one JSON object per line) with index correlation:

```json
{"index": 0, "status": "ok", "data": {"url": "https://a.com", "content": "...", "elapsed": 1.2}}
{"index": 1, "status": "error", "error": {"code": "TIMEOUT", "message": "Request timed out..."}}
{"index": 2, "status": "ok", "data": {"url": "https://c.com", "content": "...", "elapsed": 0.8}}
```

Results are sorted by index. Failed URLs don't stop processing — errors are reported inline.

### Workers

| Flag | Default | Max | Notes |
|------|---------|-----|-------|
| `--workers` / `-w` | 3 | 10 | Matches CF paid tier concurrency limit |

```bash
# Conservative (free tier: 3 concurrent browsers)
flarecrawl scrape --batch urls.txt --workers 3

# Aggressive (paid tier: up to 10)
flarecrawl scrape --batch urls.txt --workers 10
```

### Supported commands

| Command | `--batch` | `--workers` | Notes |
|---------|-----------|-------------|-------|
| `scrape` | Yes | Yes | Also supports `--input` (alias) |
| `extract` | Yes | Yes | Supplements `--urls` |
| `crawl` | No | No | Has its own async job system |
| `screenshot` | No | No | Single URL |
| `pdf` | No | No | Single URL |

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
# Map URLs then batch scrape them
flarecrawl map https://docs.example.com --json | \
  jq -r '.data[]' | head -10 > urls.txt
flarecrawl scrape --batch urls.txt --workers 5

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
flarecrawl/
├── pyproject.toml              # Package config
├── AGENTS.md                   # AI agent context
├── README.md                   # This file
├── src/flarecrawl/
│   ├── __init__.py             # Version
│   ├── batch.py                # Batch processing (parse + parallel workers)
│   ├── cache.py                # File-based response cache
│   ├── cli.py                  # Typer CLI (all commands)
│   ├── client.py               # CF Browser Rendering API client (httpx pooling, HTTP/2)
│   ├── config.py               # Credentials, usage tracking, env-var config
│   └── extract.py              # HTML extraction (main content, images, schema, tags)
└── tests/
    ├── conftest.py             # Test fixtures
    ├── corpus.py               # Feature test corpus (80 live tests x 8 sites)
    ├── test_batch.py           # Batch module tests
    ├── test_cache.py           # Cache module tests
    ├── test_cli.py             # CLI tests
    ├── test_client.py          # Client tests
    └── test_extract.py         # Extract module tests
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
