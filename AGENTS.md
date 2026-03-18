# Flarecrawl - AI Agent Context

> Cloudflare Browser Rendering CLI — Firecrawl-compatible, cost-efficient at scale.
> Renders JavaScript, extracts content, takes screenshots, generates PDFs, crawls entire sites.

## Quick Reference

| Task | Command |
|------|---------|
| Scrape page to markdown | `flarecrawl scrape URL` |
| Scrape page to HTML | `flarecrawl scrape URL --format html` |
| Scrape multiple URLs | `flarecrawl scrape URL1 URL2 URL3 --json` |
| Batch scrape from file | `flarecrawl scrape --batch urls.txt --workers 5` |
| Scrape from file (compat) | `flarecrawl scrape --input urls.txt --json` |
| Crawl site | `flarecrawl crawl URL --wait --limit N` |
| Crawl with progress | `flarecrawl crawl URL --wait --progress --limit N` |
| Check crawl status | `flarecrawl crawl JOB_ID --status` |
| Get crawl results | `flarecrawl crawl JOB_ID` |
| Stream crawl results | `flarecrawl crawl JOB_ID --ndjson` |
| Discover URLs | `flarecrawl map URL --json` |
| Download site as files | `flarecrawl download URL --limit N` |
| AI data extraction | `flarecrawl extract PROMPT --urls URL --json` |
| Batch extraction | `flarecrawl extract PROMPT --batch urls.txt --workers 5` |
| Screenshot | `flarecrawl screenshot URL -o file.png` |
| Full page screenshot | `flarecrawl screenshot URL -o file.png --full-page` |
| PDF | `flarecrawl pdf URL -o file.pdf` |
| Favicon (best) | `flarecrawl favicon URL` |
| Favicon (all) | `flarecrawl favicon URL --all --json` |
| Scrape with JS rendering | `flarecrawl scrape URL --js` |
| Scrape (bypass cache) | `flarecrawl scrape URL --no-cache` |
| Scrape (custom wait) | `flarecrawl scrape URL --wait-until networkidle2` |
| Check usage | `flarecrawl usage --json` |
| Auth status | `flarecrawl auth status --json` |
| Cache status | `flarecrawl cache status --json` |
| Clear cache | `flarecrawl cache clear` |

## Authentication

```bash
# Interactive login
flarecrawl auth login

# Non-interactive (CI/CD)
flarecrawl auth login --account-id ID --token TOKEN

# Environment variables
export FLARECRAWL_ACCOUNT_ID="your-account-id"
export FLARECRAWL_API_TOKEN="your-api-token"

# Check auth
flarecrawl auth status --json
# Returns: {"data": {"authenticated": true, "source": "config", "account_id": "5e08..."}}
```

**Token setup:** https://dash.cloudflare.com/profile/api-tokens → "Browser Rendering - Edit" permission.

## Command Details

### scrape

Fetches pages. Default output is markdown to stdout.

```bash
# Basic (markdown to stdout)
flarecrawl scrape https://example.com

# JSON envelope
flarecrawl scrape https://example.com --json
# Returns: {"data": {"url": "...", "content": "# Page Title\n...", "elapsed": 2.9}, "meta": {"format": "markdown"}}

# Multiple URLs (concurrent)
flarecrawl scrape https://a.com https://b.com --json --timing
# Returns: {"data": [...], "meta": {"count": 2, "format": "markdown"}}

# Batch mode: file input → parallel workers → NDJSON output
flarecrawl scrape --batch urls.txt --workers 5
# Output: {"index": 0, "status": "ok", "data": {...}}
#         {"index": 1, "status": "ok", "data": {...}}

# From file (backward-compatible alias for --batch, uses --json output)
flarecrawl scrape --input urls.txt --json

# Formats: markdown (default), html, links, screenshot, json
flarecrawl scrape URL --format html --json
flarecrawl scrape URL --format links --json      # Extract all links
flarecrawl scrape URL --format json --json        # AI extraction via /json endpoint

# Filter output fields
flarecrawl scrape URL --json --fields url,content
```

**Key flags:** `--format`, `--json`, `--output`, `--timing`, `--timeout`, `--fields`, `--batch`, `--workers`, `--input`, `--wait-for`, `--body`

### crawl

Async multi-page crawl. Crawl jobs persist for 14 days on Cloudflare.

```bash
# Start + wait for results
flarecrawl crawl https://example.com --wait --limit 50 --json
# Returns: {"data": {"job_id": "...", "status": "completed", "total": 50, "browser_seconds": 53.5, "records": [...]}}

# Fire and forget
flarecrawl crawl https://example.com --limit 50
# Returns: {"data": {"job_id": "uuid-here", "status": "running"}}

# Check status
flarecrawl crawl JOB_ID --status
# Returns: {"data": {"status": "running", "finished": 23, "total": 50}}

# Resume/get results from prior crawl
flarecrawl crawl JOB_ID

# Stream NDJSON (one record per line, auto-paginates)
flarecrawl crawl JOB_ID --ndjson --fields url,markdown

# Path filtering
flarecrawl crawl URL --wait --include-paths "/docs,/api" --exclude-paths "/changelog"

# Fast mode (skip JS rendering)
flarecrawl crawl URL --wait --no-render --limit 100

# Output format
flarecrawl crawl URL --wait --format markdown   # default
flarecrawl crawl URL --wait --format html
```

**Key flags:** `--wait`, `--progress`, `--limit`, `--max-depth`, `--format`, `--include-paths`, `--exclude-paths`, `--allow-external-links`, `--allow-subdomains`, `--no-render`, `--source`, `--ndjson`, `--fields`, `--json`, `--body`

**Record statuses:** `completed`, `queued`, `skipped`, `errored`, `disallowed`, `cancelled`

### map

Discovers URLs on a single page (uses /links endpoint).

```bash
flarecrawl map https://example.com --json
# Returns: {"data": ["https://example.com/page1", ...], "meta": {"count": 97}}

# Include external/subdomain links
flarecrawl map https://example.com --include-subdomains --json
```

For deep multi-page URL discovery, use `crawl` instead.

### download

Crawls a site and saves each page as a file.

```bash
flarecrawl download https://docs.example.com --limit 50
# Creates: .flarecrawl/docs.example.com/page-name.md

flarecrawl download URL --format html --limit 20   # Save as .html
flarecrawl download URL -y --limit 10               # Skip confirmation
```

### extract

AI-powered structured data extraction using Cloudflare Workers AI.

```bash
# Natural language prompt
flarecrawl extract "Get all product names and prices" --urls https://shop.example.com --json
# Returns: {"data": {"url": "...", "data": {"products": [...]}}}

# With JSON schema
flarecrawl extract "Extract metadata" --urls URL --schema '{"type":"json_schema","schema":{"type":"object","properties":{"title":{"type":"string"}}}}'

# Schema from file
flarecrawl extract "Extract data" --urls URL --schema-file schema.json

# Multiple URLs (sequential)
flarecrawl extract "Get page title" --urls https://a.com,https://b.com --json

# Batch mode: parallel extraction with NDJSON output
flarecrawl extract "Get page title" --batch urls.txt --workers 5
# Output: {"index": 0, "status": "ok", "data": {...}}
```

### screenshot

```bash
flarecrawl screenshot URL                          # Saves screenshot.png
flarecrawl screenshot URL -o file.png              # Custom path
flarecrawl screenshot URL --full-page -o full.png  # Full page
flarecrawl screenshot URL --selector "main" -o m.png  # Specific element
flarecrawl screenshot URL --width 1440 --height 900   # Custom viewport
flarecrawl screenshot URL --format jpeg -o file.jpg   # JPEG
flarecrawl screenshot URL --json                   # Base64 JSON output
```

### pdf

```bash
flarecrawl pdf URL                        # Saves page.pdf
flarecrawl pdf URL -o report.pdf          # Custom path
flarecrawl pdf URL --landscape --format a4  # Landscape A4
flarecrawl pdf URL --json                 # Base64 JSON output
```

### favicon

Extracts favicon URL from a web page by parsing `<link rel="icon">` and `<link rel="apple-touch-icon">` tags.

```bash
# Get best (largest) favicon URL
flarecrawl favicon https://example.com
# Output: https://example.com/apple-touch-icon-180x180.png

# All icons with details
flarecrawl favicon https://example.com --all --json
# Returns: {"data": [{"url": "...", "rel": "apple-touch-icon", "sizes": "180x180"}, ...], "meta": {"count": 3}}

# Single best as JSON
flarecrawl favicon https://example.com --json
# Returns: {"data": {"url": "...", "rel": "icon", "sizes": "32x32"}, "meta": {"url": "...", "count": 1}}
```

Falls back to `/favicon.ico` if no `<link>` tags found. Uses `rejectResourceTypes` to skip images/CSS for speed.

### usage

Tracks browser time locally from `X-Browser-Ms-Used` response headers.

```bash
flarecrawl usage
# Shows: today's usage, % of free tier, 7-day history

flarecrawl usage --json
# Returns: {"data": {"today_ms": 154, "today_seconds": 0.2, "today_percent_of_free": 0.0, ...}}
```

## JSON Output Shapes

### Scrape (single URL)
```json
{"data": {"url": "...", "content": "# Markdown...", "elapsed": 2.9}, "meta": {"format": "markdown"}}
```

### Scrape (multiple URLs)
```json
{"data": [{"url": "...", "content": "..."}, ...], "meta": {"count": 3, "format": "markdown"}}
```

### Crawl results
```json
{"data": {"job_id": "...", "status": "completed", "total": 30, "browser_seconds": 53.5, "records": [
  {"url": "...", "status": "completed", "metadata": {"title": "...", "status": 200}, "markdown": "..."},
  {"url": "...", "status": "skipped"}
]}, "meta": {"count": 30}}
```

### Map
```json
{"data": ["https://example.com/a", "https://example.com/b"], "meta": {"count": 2}}
```

### Screenshot/PDF
```json
{"data": {"screenshot": "base64...", "encoding": "base64", "format": "png", "size": 22268}, "meta": {"url": "..."}}
```

### Favicon
```json
{"data": {"url": "https://example.com/apple-touch-icon.png", "rel": "apple-touch-icon", "sizes": "180x180", "type": null}, "meta": {"url": "...", "count": 3}}
```

### Error
```json
{"error": {"code": "AUTH_REQUIRED", "message": "Not authenticated. Run: flarecrawl auth login"}}
```

## Batch & Parallel

`scrape` and `extract` support `--batch` for file-based input with parallel processing.

### Flags

| Flag | Short | Default | Max | Description |
|------|-------|---------|-----|-------------|
| `--batch` | `-b` | — | — | Input file (plain text, JSON array, or NDJSON) |
| `--workers` | `-w` | 3 | 10 | Concurrent workers (capped at CF paid tier limit) |

### Input formats (auto-detected)

| Format | Detection | Example |
|--------|-----------|---------|
| Plain text | Default | One URL per line, `#` comments skipped |
| JSON array | Starts with `[` | `["https://a.com", "https://b.com"]` |
| NDJSON | Starts with `{` | `{"url": "https://a.com"}\n{"url": "https://b.com"}` |

### Output format

Batch mode outputs NDJSON with index correlation:

```json
{"index": 0, "status": "ok", "data": {"url": "...", "content": "...", "elapsed": 1.2}}
{"index": 1, "status": "error", "error": {"code": "TIMEOUT", "message": "..."}}
{"index": 2, "status": "ok", "data": {"url": "...", "content": "...", "elapsed": 0.8}}
```

- Results sorted by `index` (zero-based position in input file)
- Partial failures don't stop processing — errors reported inline
- Exit code 1 if any items failed, 0 if all succeeded
- Progress reported to stderr: `completed/total (errors: N)`

### Examples

```bash
# Map a site → batch scrape the results
flarecrawl map https://docs.example.com --json | jq -r '.data[]' > urls.txt
flarecrawl scrape --batch urls.txt --workers 5

# Batch extract with schema
flarecrawl extract "Get title and date" --batch urls.txt --workers 3 \
  --schema '{"type":"json_schema","schema":{"type":"object","properties":{"title":{"type":"string"}}}}'

# Process batch output with jq
flarecrawl scrape --batch urls.txt | jq 'select(.status == "ok") | .data.content'
```

### Agent guidance

- **Free tier:** Use `--workers 3` (3 concurrent browsers max)
- **Paid tier:** Up to `--workers 10`
- **Always check usage first** for large batches: `flarecrawl usage --json`
- **Use `--batch` over `--input`** — `--input` is backward compat only
- Batch output is NDJSON, not `{data, meta}` envelope — parse line by line

## Exit Codes

| Code | Name | Meaning | Agent Action |
|------|------|---------|-------------|
| 0 | SUCCESS | Operation completed | Process stdout |
| 1 | ERROR | General/unknown error | Read stderr, retry or fail |
| 2 | AUTH_REQUIRED | Not authenticated | Run `flarecrawl auth login` |
| 3 | NOT_FOUND | Job/resource not found | Check job ID is valid |
| 4 | VALIDATION | Invalid input | Fix arguments, check URL format |
| 5 | FORBIDDEN | Permission denied | Check token has "Browser Rendering - Edit" |
| 7 | RATE_LIMITED | Too many requests | Wait and retry (auto-retry built in) |

## Firecrawl Compatibility

| firecrawl | flarecrawl | Notes |
|-----------|------------|-------|
| `firecrawl scrape URL` | `flarecrawl scrape URL` | Same flags |
| `firecrawl scrape URL1 URL2` | `flarecrawl scrape URL1 URL2` | Concurrent |
| `firecrawl crawl URL --wait` | `flarecrawl crawl URL --wait` | Same flags |
| `firecrawl map URL` | `flarecrawl map URL` | Same flags |
| `firecrawl download URL` | `flarecrawl download URL` | `.flarecrawl/` dir |
| `firecrawl agent PROMPT` | `flarecrawl extract PROMPT` | Workers AI |
| `firecrawl credit-usage` | `flarecrawl usage` | Local tracking |
| `firecrawl --status` | `flarecrawl --status` | Same |
| `firecrawl search QUERY` | **Not supported** | No CF equivalent |

## Raw API Passthrough

Every command accepts `--body` to send raw JSON to the CF API:

```bash
flarecrawl scrape --body '{"url":"https://example.com","gotoOptions":{"waitUntil":"networkidle0"}}' --json
```

This bypasses all flag processing and sends the body directly. Useful for advanced options not exposed as flags.

## Agent Rules

1. **Check auth first:** `flarecrawl auth status --json` — exit code 2 means not authenticated
2. **Always use `--json`** when parsing output programmatically (or `--batch` for NDJSON)
3. **Always use `--limit`** on crawl/download — prevents runaway browser time
4. **Check usage before large batches/crawls:** `flarecrawl usage --json` — free tier is 10 min/day
5. **Use `--batch` for multi-URL work** — parallel processing, NDJSON output, configurable `--workers`
6. **Crawl is async by default** — use `--wait` to block until complete, `--progress` for visual feedback
7. **Screenshots/PDFs save to files** — always specify `-o path` and verify the file was created
8. **Use `--fields`** on crawl results to reduce JSON size: `--fields url,markdown`
9. **Use `--ndjson`** for large crawl results to avoid loading everything into memory
10. **Map before crawling** — `flarecrawl map URL --json` shows what pages exist
11. **Retry is automatic** — 429, 502, 503 errors retry 3 times with backoff
12. **Free tier: `--workers 3`**, paid tier: up to `--workers 10`
13. **Responses are cached** for 1 hour by default — use `--no-cache` for fresh data
14. **Use `--js`** on JS-heavy pages (SPAs, Swagger UIs) — waits for networkidle0
15. **Images/fonts/media/stylesheets skipped** by default for faster text extraction
16. **Connection pooling + HTTP/2** — persistent session reuses TCP/TLS across requests
17. **Batch mode fails fast** on auth/permission errors — won't retry 401/403

## Pricing Reference

| Tier | Daily Limit | Cost |
|------|------------|------|
| Free | 10 min/day (600,000ms) | $0 |
| Paid | 10 hr/month included | $0.09/hr after |
| Concurrency | Free: 3, Paid: 10 | +$2/extra browser |

A typical page scrape uses 100-200ms of browser time. A 30-page crawl uses ~50s (~$0.001).
