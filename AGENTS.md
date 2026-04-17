# Flarecrawl - AI Agent Context

> Cloudflare Browser Run CLI — Firecrawl-compatible, cost-efficient at scale.
> Renders JavaScript, extracts content, takes screenshots, generates PDFs, crawls entire sites.
> Now with CDP WebSocket access for persistent browser sessions, real-time debugging, and authenticated scraping.

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
| Scrape with HTTP Basic Auth | `flarecrawl scrape URL --auth user:pass` |
| Crawl with HTTP Basic Auth | `flarecrawl crawl URL --wait --limit N --auth user:pass` |
| Scrape main content only | `flarecrawl scrape URL --only-main-content` |
| Scrape excluding nav/footer | `flarecrawl scrape URL --exclude-tags "nav,footer"` |
| Scrape with custom headers | `flarecrawl scrape URL --headers "Accept-Language: fr"` |
| Scrape mobile viewport | `flarecrawl scrape URL --mobile` |
| Extract images | `flarecrawl scrape URL --format images --json` |
| Extract structured data | `flarecrawl schema URL --json` |
| AI summary | `flarecrawl scrape URL --format summary --json` |
| Diff against cache | `flarecrawl scrape URL --diff --json` |
| Crawl with content filter | `flarecrawl crawl URL --wait --only-main-content --limit N` |
| Crawl with webhook | `flarecrawl crawl URL --wait --limit N --webhook https://hooks.example.com` |
| Custom User-Agent | `flarecrawl scrape URL --user-agent "MyBot/1.0"` |
| CSS selector extraction | `flarecrawl scrape URL --selector "main" --json` |
| Wait for element | `flarecrawl scrape URL --wait-for-selector ".loaded" --json` |
| Run JavaScript | `flarecrawl scrape URL --js-eval "document.title" --json` |
| Process local HTML | `cat page.html \| flarecrawl scrape --stdin --json` |
| Save HAR | `flarecrawl scrape URL --har output.har` |
| Discover URLs (sitemap+feed+links) | `flarecrawl discover URL --json` |
| Backup raw HTML | `flarecrawl scrape URL --backup-dir ./backup` |
| Remove cookie banners | `flarecrawl scrape URL --magic` |
| Request language | `flarecrawl scrape URL --language de` |
| Wayback fallback on 404 | `flarecrawl scrape URL --archived` |
| Auto-scroll lazy content | `flarecrawl scrape URL --scroll` |
| Relevance filter | `flarecrawl scrape URL --query "pricing"` |
| Precision extraction | `flarecrawl scrape URL --precision` |
| Recall extraction | `flarecrawl scrape URL --recall` |
| Dedup crawl results | `flarecrawl crawl URL --wait --deduplicate` |
| Load session cookies | `flarecrawl scrape URL --session cookies.json` |
| Save session from file | `flarecrawl session save mysite --file cookies.json` |
| List saved sessions | `flarecrawl session list` |
| Show session cookies | `flarecrawl session show mysite` |
| Delete saved session | `flarecrawl session delete mysite` |
| Validate session against URL | `flarecrawl session validate mysite https://example.com` |
| Fetch URL (content-aware) | `flarecrawl fetch URL` |
| Fetch with session | `flarecrawl fetch URL --session cookies.json` |
| Fetch with saved session | `flarecrawl fetch URL --session @mysite` |
| Download binary file | `flarecrawl fetch URL -o file.pdf` |
| Discover OpenAPI specs | `flarecrawl openapi URL --json` |
| Download OpenAPI spec | `flarecrawl openapi URL --download -o ./specs` |
| Probe common spec paths | `flarecrawl openapi URL --probe --json` |
| Discover + OpenAPI | `flarecrawl discover URL --openapi --json` |
| YAML batch config | `flarecrawl batch config.yml` |
| Accessibility tree | `flarecrawl scrape URL --format accessibility --json` |
| Enhanced content extraction | `flarecrawl scrape URL --paywall --json` |
| Batch enhanced extraction | `flarecrawl scrape --batch urls.txt --paywall --workers 5` |
| Stealth mode (TLS impersonation) | `flarecrawl scrape URL --stealth` |
| Stealth + enhanced extraction | `flarecrawl scrape URL --paywall --stealth --json` |
| Web search | `flarecrawl search "query" --json` |
| Search + scrape pipeline | `flarecrawl search "topic" --scrape --limit 5 --json` |
| Strip ads from HTML | `flarecrawl scrape URL --clean --format html --json` |
| Proxy all requests | `flarecrawl scrape URL --proxy socks5://localhost:9050` |
| List site rules | `flarecrawl rules list --json` |
| Show rules for domain | `flarecrawl rules show www.nytimes.com` |
| Add user rule | `flarecrawl rules add example.com --referer https://google.com/` |
| Agent-safe sanitisation | `flarecrawl scrape URL --agent-safe --json` |
| Agent-safe + stealth + paywall | `flarecrawl scrape URL --agent-safe --paywall --stealth --json` |
| CDP: persistent browser session | `flarecrawl scrape URL --cdp --keep-alive 60 --json` |
| CDP: interactive auth (login in DevTools) | `flarecrawl scrape URL --interactive --json` |
| CDP: live browser debugging | `flarecrawl scrape URL --live-view` |
| CDP: proper JS eval (async, typed) | `flarecrawl scrape URL --cdp --js-eval "await fetch('/api').then(r => r.json())"` |
| CDP: real HAR network capture | `flarecrawl scrape URL --cdp --har traffic.har --json` |
| CDP: session recording | `flarecrawl scrape URL --record --record-output debug.json` |
| CDP: save cookies after scrape | `flarecrawl scrape URL --cdp --save-cookies session.json` |
| CDP: load cookies for auth | `flarecrawl scrape URL --cdp --load-cookies session.json --json` |
| CDP: get WebSocket URL for Playwright | `flarecrawl cdp connect --json` |
| CDP: list active sessions | `flarecrawl cdp sessions --json` |
| CDP: close sessions | `flarecrawl cdp close` |
| WebMCP: discover site tools | `flarecrawl webmcp discover URL --json` |
| WebMCP: call a site tool | `flarecrawl webmcp call URL --tool searchFlights --params '{"dest":"Paris"}' --json` |
| Design: extract design system | `flarecrawl design extract URL -o DESIGN.md` |
| Design: HTML preview | `flarecrawl design extract URL --preview -o preview.html` |
| Design: coherence score | `flarecrawl design coherence URL --json` |
| Design: compare two sites | `flarecrawl design diff URL1 URL2 --json` |
| Design: full extraction | `flarecrawl design extract URL --full -o DESIGN.md` |
| Interact: fill form fields | `flarecrawl interact URL --fill "#name=John" --fill "#email=j@e.com" --click "button[type=submit]" --json` |
| Interact: with screenshot | `flarecrawl interact URL --fill "#q=search" --click "#go" --screenshot result.png` |
| CDP: multi-URL session reuse | `flarecrawl scrape URL1 URL2 URL3 --cdp --tabs 3 --json` |
| Crawl ignoring robots.txt | `flarecrawl crawl URL --wait --limit 50 --ignore-robots` |
| Skip content negotiation | `flarecrawl scrape URL --no-negotiate` |
| View negotiate domain cache | `flarecrawl negotiate status --json` |
| Clear negotiate domain cache | `flarecrawl negotiate clear` |
| Check usage | `flarecrawl usage --json` |
| Auth status | `flarecrawl auth status --json` |
| Cache status | `flarecrawl cache status --json` |
| Clear cache | `flarecrawl cache clear` |

## Authentication

```bash
# Interactive login (opens browser for guided token setup)
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

**Key flags:** `--format`, `--json`, `--output`, `--timing`, `--timeout`, `--fields`, `--batch`, `--workers`, `--input`, `--wait-for`, `--body`, `--agent-safe`

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

**Key flags:** `--wait`, `--progress`, `--limit`, `--max-depth`, `--format`, `--include-paths`, `--exclude-paths`, `--allow-external-links`, `--allow-subdomains`, `--no-render`, `--source`, `--ndjson`, `--fields`, `--json`, `--body`, `--agent-safe`

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

### Agent-safe metadata (with `--agent-safe --json`)
```json
{"data": {"url": "...", "content": "...", "metadata": {
  "agentSafety": {
    "sanitised": true,
    "findings": [
      {"category": "content_injection", "severity": "high", "description": "Hidden text via CSS (2 elements)", "action": "removed", "count": 2},
      {"category": "prompt_injection", "severity": "high", "description": "Prompt injection patterns removed (1 lines)", "action": "removed", "count": 1},
      {"category": "semantic_manipulation", "severity": "medium", "description": "Urgency language clusters (1 lines)", "action": "flagged", "count": 1}
    ],
    "stats": {"removed": 3, "flagged": 1, "byCategory": {"content_injection": 2, "prompt_injection": 1, "semantic_manipulation": 1}}
  }
}}}
```

**Categories:** `content_injection` (hidden text, comments, attrs, unicode), `prompt_injection` (instruction patterns), `semantic_manipulation` (urgency/authority)

**Actions:** `removed` (content stripped) or `flagged` (content preserved, finding reported)

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
| `firecrawl search QUERY` | `flarecrawl search QUERY` | Via Jina Search API (needs `JINA_API_KEY`) |

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
18. **Use `--auth user:pass`** for HTTP Basic Auth protected sites — works on all commands
19. **Markdown content negotiation is automatic** — scrape tries `Accept: text/markdown` before browser rendering. If the site supports it (e.g. Cloudflare zones with Markdown for Agents), content is fetched directly (~100ms, zero browser time). Check `metadata.source` for `"content-negotiation"` vs `"browser-rendering"`
20. **Use `--no-negotiate`** to force browser rendering when you need full HTML/JS (e.g. tech detection, Wappalyzer)
21. **Domain capability is cached** — one probe per domain (24h negative, 7d positive). Batch scrapes of the same domain only probe once
22. **Use `--paywall`** for enhanced content extraction — applies multi-strategy cascade with per-site optimisations before browser rendering. Zero browser time when direct strategies succeed. Check `metadata.source` for the strategy used
23. **`--paywall` works without auth** — direct HTTP strategies run independently of Cloudflare credentials. With auth, per-site headers are also applied to browser rendering fallback
24. **`--paywall` is opt-in** — does not affect normal scraping. Install `curl_cffi` for improved compatibility with bot-protected sites
25. **Use `--stealth`** for browser TLS fingerprint impersonation on direct HTTP requests. Requires `curl_cffi`. Makes content negotiation and direct fetches appear as real Safari/Chrome browsers to bot detection systems (JA3/JA4 fingerprinting)
26. **Content cleanup is automatic** — ad placeholders, share buttons, newsletter prompts, copyright lines, and nav chrome are stripped from all markdown output. No flag needed
27. **Combine `--paywall --stealth`** for maximum extraction — stealth TLS fingerprint + multi-strategy cascade + per-site header rules + content cleanup
28. **Use `--clean`** to strip ad DOM elements from HTML output. Markdown cleanup is automatic; `--clean` extends it to HTML format
29. **Use `--proxy`** to route all HTTP through a proxy (http/https/socks5). Also set via `FLARECRAWL_PROXY` env var. Affects CLI-to-CF and direct HTTP; does NOT affect CF browser-to-target
30. **`search` command** uses Jina Search API — requires `JINA_API_KEY` env var (free at jina.ai). Use `--scrape` to also scrape each result URL through the normal pipeline
31. **Per-site rules** are now in YAML — defaults ship with the package, user overrides at `~/.config/flarecrawl/rules.yaml`. Use `flarecrawl rules list/show/add/path` to manage
32. **Use `--agent-safe`** to sanitise content against adversarial AI agent traps (DeepMind "AI Agent Traps" taxonomy). Two-phase pipeline: HTML-level (hidden text, comments, suspicious attributes, unicode tricks) + text-level (prompt injection removal, semantic manipulation flagging). JSON output includes `metadata.agentSafety` with findings and stats. Semantic manipulation is flagged only (never removed). Works on `scrape`, `crawl`, `download`, and `extract` commands. Also works with `--stdin`
33. **`--agent-safe` is opt-in** — does not affect normal scraping. When enabled, content is sanitised on all paths (browser rendering, content negotiation, paywall bypass, stdin, crawl records). Combine with `--paywall --stealth` for maximum extraction + safety
34. **`--agent-safe` uses short-line bias** for prompt injection detection — only removes lines <200 chars to avoid stripping legitimate articles about prompt injection. Articles discussing "ignore previous instructions" in long analytical paragraphs are preserved
35. **Agent-safe findings are structured** — each finding has `category`, `severity`, `description`, `action` (removed/flagged), and `count`. Use `metadata.agentSafety.stats.byCategory` to quickly check what was detected without parsing individual findings
36. **Use `--cdp` for persistent browser control** — connects via Chrome DevTools Protocol WebSocket instead of REST. Enables `--interactive`, `--live-view`, `--record`, real `--har`, proper `--js-eval`, and `--keep-alive`. Requires `uv pip install websockets` (or `uv pip install flarecrawl[cdp]`). REST stays default for simple scraping
37. **`--interactive` for authenticated scraping** — opens DevTools for manual login (OAuth, 2FA, CAPTCHAs), auto-saves cookies, then scrapes with the authenticated session. Implies `--cdp`. Saved cookies reusable via `--load-cookies`
38. **`--live-view` for debugging** — prints Chrome DevTools URL for real-time browser inspection. Implies `--cdp --keep-alive 300`. Session stays open until Ctrl+C
39. **`--keep-alive N` for session reuse** — holds browser open N seconds. Sessions persist across invocations. Use `flarecrawl cdp sessions` to list and `flarecrawl cdp close` to clean up
40. **`--record` for session recordings** — saves rrweb-format recording JSON. Use `--record-output path.json` to specify location. Implies `--cdp`
41. **`--save-cookies`/`--load-cookies`** — persist and reuse browser cookies. `--save-cookies session.json` after scrape, `--load-cookies session.json` on next run. Both imply `--cdp`
42. **`--ignore-robots`** on crawl — bypasses robots.txt and AI Crawl Control directives. CF crawl respects these by default
43. **Workers max is now 50** (was 10) — CF quadrupled concurrent browser limit to 120. Free tier: use `--workers 3`, paid: up to `--workers 50`. Override with `FLARECRAWL_MAX_WORKERS` env var
44. **CDP flags auto-promote** — `--interactive`, `--live-view`, `--record`, `--save-cookies`, `--load-cookies`, `--keep-alive` all imply `--cdp`. No need to pass both
45. **`FLARECRAWL_CDP_ENDPOINT` env var** — override the CDP WebSocket URL to use any CDP backend (Oxylabs Scraping Browser, Bright Data, local Chrome `ws://localhost:9222`). When set, flarecrawl skips CF auth for the browser connection

## Pricing Reference

| Tier | Daily Limit | Cost |
|------|------------|------|
| Free | 10 min/day (600,000ms) | $0 |
| Paid | 10 hr/month included | $0.09/hr after |
| Concurrency | Up to 120 (default) | Higher on request |

A typical page scrape uses 100-200ms of browser time. A 30-page crawl uses ~50s (~$0.001).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLARECRAWL_ACCOUNT_ID` | — | Cloudflare account ID (overrides config file) |
| `FLARECRAWL_API_TOKEN` | — | Cloudflare API token (overrides config file) |
| `FLARECRAWL_CACHE_TTL` | 3600 | Response cache TTL in seconds (0 to disable) |
| `FLARECRAWL_MAX_RETRIES` | 3 | Max retry attempts on 429/502/503 |
| `FLARECRAWL_MAX_WORKERS` | 50 | Max parallel workers for batch mode (CF supports 120) |
| `FLARECRAWL_TIMEOUT` | 120 | Request timeout in seconds |
| `FLARECRAWL_CDP_ENDPOINT` | — | Override CDP WebSocket URL (Oxylabs, Bright Data, local Chrome) |
| `FLARECRAWL_PROXY` | - | Default proxy URL (http/https/socks5) |
| `JINA_API_KEY` | - | Jina API key for `search` command (free at jina.ai) |

## Testing

```bash
# Unit tests (723+ tests, no API calls)
pytest tests/ -v

# Agent-safety tests only (137 tests including corpus validation)
pytest tests/test_sanitise.py -v

# Agent-safety corpus only (attack detection + benign false-positive checks)
pytest tests/test_sanitise.py::TestCorpus -v

# Feature test corpus (80 live tests, requires auth)
python tests/corpus.py

# Quick corpus (10 tests, 1 per feature)
python tests/corpus.py --quick

# Test specific feature/site
python tests/corpus.py --feature schema --site news-heavy
```

## Release Process

When shipping a new version:

1. Bump version in `pyproject.toml` and `src/flarecrawl/__init__.py`
2. Add entry to the **Recent Updates** table at the top of `README.md`
3. Update `AGENTS.md` with any new commands, flags, or rules
4. Run `pytest tests/ -v` — all tests must pass
5. Commit and tag: `git tag v0.X.0`
