# 🔥 Flarecrawl CLI

[![Forma](https://img.shields.io/badge/forma-experimental-orange.svg)](https://github.com/forma-tools/forma)
[![GitHub](https://img.shields.io/badge/github-0xDarkMatter%2Fflarecrawl-blue?logo=github)](https://github.com/0xDarkMatter/flarecrawl)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Cloudflare](https://img.shields.io/badge/cloudflare-browser--run-orange?logo=cloudflare)](https://developers.cloudflare.com/browser-rendering/)

> Cloudflare Browser Run CLI — Firecrawl-compatible, cost-efficient at scale.

CLI that wraps Cloudflare's [Browser Run API](https://developers.cloudflare.com/browser-rendering/rest-api/) with the same command structure as Firecrawl. Supports scraping, crawling, URL discovery, screenshots, PDFs, and AI-powered data extraction — all running on Cloudflare's headless Chromium infrastructure. Now with direct Chrome DevTools Protocol (CDP) access for persistent browser sessions, real-time debugging, and authenticated scraping. Cost-efficient alternative for high-volume use cases (free 10 min/day, then $0.09/hr).

## Recent Updates

| Version | Date | Changes |
|---------|------|---------|
| **v0.15.0** | 2026-04-17 | **WebMCP + form interaction + correctness fixes.** `flarecrawl webmcp discover/call` for structured tool discovery on WebMCP-enabled sites. `flarecrawl interact` command with `--fill`, `--click`, `--select` and human-like timing (Bezier mouse curves, variable keystroke delays). `flarecrawl cdp connect` prints WebSocket URL for Playwright/Puppeteer. `FLARECRAWL_CDP_ENDPOINT` env var for custom CDP backends (Oxylabs, Bright Data, local Chrome). Fixed Live View URLs to use `live.browser.run` hosted UI. Session listing/close via real CF REST API. Recording retrieval via `/recording/{session_id}`. `keep_alive` capped at 600s (CF max). `--tabs` for multi-URL session reuse. `--stagehand` stub. Free tier warnings. Live test corpus (740 total tests) |
| **v0.14.1** | 2026-04-16 | **CDP WebSocket integration** — `--cdp` flag for persistent browser sessions via Chrome DevTools Protocol. `--interactive` human-in-the-loop auth (login in DevTools, cookies auto-saved). `--live-view` real-time browser debugging via Chrome DevTools. Proper `--js-eval` via `Runtime.evaluate` (replaces addScriptTag hack). Real `--har` network capture via `Network.enable`. `--record` session recordings (rrweb format). `--keep-alive N` persistent sessions with cross-invocation reuse. `--save-cookies`/`--load-cookies` for authenticated scraping. `--ignore-robots` on crawl. `flarecrawl cdp sessions/close` session management. Workers max 10 → 50 (CF now supports 120 concurrent browsers). Rebranded to Cloudflare Browser Run. 723 tests |
| **v0.14.0** | 2026-04-16 | `fetch` command (content-type aware download), `openapi` command (spec discovery + download), `session` sub-app (save/list/show/delete/validate), `authcrawl` module (authenticated BFS crawler), `--openapi` flag on `discover`, multi-format cookie loading |
| **v0.13.0** | 2026-04-14 | Optimize sanitise pipeline — 51% faster via keyword pre-checks |
| **v0.12.1** | 2026-04-06 | Extended `--agent-safe` attack vector coverage: hidden iframes, hidden form inputs, CSS class hiding, meta tag injection, homoglyph evasion (Cyrillic/Greek), markdown exfiltration detection, HTML entity evasion. 13 sanitisers total, 61-file corpus, 564 tests. Based on [AI Agent Traps](https://ssrn.com/abstract=6372438) (Franklin et al., Google DeepMind, 2026) |
| **v0.12.0** | 2026-04-06 | `--agent-safe` flag for adversarial content sanitisation. Two-phase pipeline (HTML + text) defending against content injection, prompt injection, and semantic manipulation. Informed by [AI Agent Traps](https://ssrn.com/abstract=6372438) (Franklin et al., Google DeepMind, 2026) |
| **v0.11.0** | 2026-04-03 | `search` command (Jina Search), `--proxy` flag, `--clean` for HTML, per-site YAML rulesets (`flarecrawl rules`), `FLARECRAWL_PROXY` env var, 378 tests |
| **v0.10.0** | 2026-04-02 | Enhanced content extraction (`--paywall`), stealth mode (`--stealth`), automatic content cleanup — multi-strategy cascade with per-site optimisations, browser TLS fingerprint impersonation via `curl_cffi`, archive fallbacks, ad/cruft removal on all markdown output, works without auth, batch mode support, 343 tests |
| **v0.9.0** | 2026-03-26 | Markdown content negotiation (`Accept: text/markdown`) — auto-detects sites serving markdown natively, skips browser rendering for faster/cheaper/higher-quality extraction. Domain capability cache, `--no-negotiate`, `source` metadata on all results, `flarecrawl negotiate status/clear`, batch session reuse, 278 tests |
| **v0.8.0** | 2026-03-20 | `--scroll`, `--query`, `--precision`/`--recall`, `--deduplicate`, `--session`, `flarecrawl batch`, `--format accessibility`, 215 tests |
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

## Use Cases

### AI agent perception layer

Flarecrawl is often the first thing an AI agent sees when it reads the web.
`--agent-safe` sanitises scraped content against adversarial attacks
([AI Agent Traps](https://ssrn.com/abstract=6372438), Google DeepMind 2026)
before it enters an LLM context window or RAG pipeline. 13 sanitisers
defend against hidden text injection, prompt injection, and semantic
manipulation — with findings reported in structured JSON metadata.

### Scraping behind authentication

The hardest scraping problem isn't parsing HTML — it's getting past the login
page. `--interactive` opens a real browser in Chrome DevTools where you
complete OAuth flows, solve CAPTCHAs, or handle 2FA manually. Cookies are
auto-saved and reused on subsequent scrapes. No more extracting tokens from
browser DevTools and pasting them into headers.

### High-volume content extraction

Batch mode with up to 50 parallel workers (`--workers 50`) scrapes thousands
of pages concurrently. Combine with `--paywall` (multi-strategy extraction
cascade), `--stealth` (browser TLS fingerprinting), and `--agent-safe` for
production pipelines that handle the real web — paywalls, bot detection, and
adversarial content included.

### Site monitoring and change detection

`--diff` compares current content against the cached version. Combine with
`--har` for network-level visibility into what changed. `--record` saves
full session recordings for audit trails. Use `--keep-alive` for cost-efficient
repeated checks on the same site.

### Documentation and knowledge base building

`flarecrawl download` saves entire sites as markdown files. `flarecrawl discover`
finds every URL via sitemaps, RSS feeds, and link crawling. Content negotiation
(`Accept: text/markdown`) fetches server-rendered markdown directly from
compatible sites — zero browser time, higher quality output.

### API and structured data extraction

`flarecrawl extract` uses Cloudflare Workers AI for natural language data
extraction ("Get all product names and prices"). `flarecrawl schema` extracts
LD+JSON, OpenGraph, and Twitter Cards. `flarecrawl openapi` discovers and
downloads API specifications.

## CDP: Persistent Browser Control

Flarecrawl v0.14.1 adds direct Chrome DevTools Protocol (CDP) access via
Cloudflare's Browser Run WebSocket endpoint. This is an opt-in mode (`--cdp`)
that gives you a persistent, controllable browser session instead of
fire-and-forget REST calls.

**REST (default)** — each command spins up a fresh browser, navigates, extracts,
and destroys. Stateless and cheap. Good for 90% of scraping.

**CDP (`--cdp`)** — you get a live browser session that stays open. Navigate,
interact, inspect, then navigate again — all within the same browser context.
The browser remembers cookies, localStorage, and DOM state between operations.

### When to use CDP

| Scenario | Why CDP |
|----------|---------|
| **Scraping behind login** | `--interactive` opens DevTools, you log in manually (OAuth, 2FA, CAPTCHA), cookies auto-saved for future scrapes |
| **Debugging failed scrapes** | `--live-view` opens Chrome DevTools pointed at the remote browser — see console errors, inspect DOM, watch network |
| **Complex JS execution** | `--js-eval` via CDP uses real `Runtime.evaluate` — async/await works, returns typed objects, not the REST addScriptTag hack |
| **Multi-step workflows** | `--keep-alive 60` holds the browser open — scrape, screenshot, extract without re-navigating (1/3 the browser time cost) |
| **Network debugging** | `--har` captures real browser network traffic (redirects, blocked resources, timing waterfall), not just flarecrawl API metadata |
| **Audit trails** | `--record` saves rrweb session recordings — replay exactly what the browser did |

### CDP examples

```bash
# Interactive auth: login in DevTools, cookies saved, then scrape
flarecrawl scrape https://private-app.example.com --interactive --json

# Debug a scrape in real-time
flarecrawl scrape https://broken-site.com --live-view

# Proper JS execution (async, typed returns)
flarecrawl scrape https://spa.example.com --cdp --js-eval "await fetch('/api/data').then(r => r.json())"

# Persistent session: navigate once, do multiple things
flarecrawl scrape https://example.com --cdp --keep-alive 120 \
  --save-cookies session.json --har traffic.har --json

# Record a session for debugging later
flarecrawl scrape https://example.com --record --record-output debug.json

# Reuse saved cookies for authenticated scraping
flarecrawl scrape https://private-app.example.com --load-cookies session.json --cdp --json

# Manage CDP sessions
flarecrawl cdp sessions --json
flarecrawl cdp close
```

### Install CDP support

CDP requires the `websockets` package (optional dependency):

```bash
uv pip install websockets
# or with extras
uv pip install flarecrawl[cdp]
```

Without `websockets`, all non-CDP features work normally. The `--cdp` flag
will print a clear install instruction if the dependency is missing.

## Quick Start

```bash
git clone https://github.com/0xDarkMatter/flarecrawl.git
cd flarecrawl
uv tool install --editable .
flarecrawl auth login
flarecrawl scrape https://example.com
flarecrawl scrape https://example.com --json | jq '.data.content'
```

## Key Commands

| Command | Description |
|---------|-------------|
| `flarecrawl scrape URL` | Scrape page to markdown (or html/links/images/summary) |
| `flarecrawl crawl URL --wait --limit N` | Crawl site with async job system |
| `flarecrawl download URL --limit N` | Save site pages to disk as markdown/html |
| `flarecrawl extract PROMPT --urls URL` | AI-powered structured data extraction |
| `flarecrawl fetch URL -o file` | Content-type aware download (binary, JSON, HTML) |
| `flarecrawl openapi URL --probe` | Discover OpenAPI/Swagger specs on a site |
| `flarecrawl screenshot URL -o page.png` | Capture full or partial page screenshots |
| `flarecrawl pdf URL -o page.pdf` | Render page as PDF |
| `flarecrawl map URL` | List all links on a page |
| `flarecrawl discover URL` | Discover URLs via sitemaps, feeds, and links |
| `flarecrawl search QUERY` | Web search via Jina API |
| `flarecrawl schema URL` | Extract LD+JSON, OpenGraph, Twitter Cards |
| `flarecrawl favicon URL` | Extract favicon/icon URLs |
| `flarecrawl session list` | Manage saved cookie sessions |

## Install

```bash
git clone https://github.com/0xDarkMatter/flarecrawl.git
cd flarecrawl
uv tool install --editable .
```

## Authentication

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

### Enhanced content extraction

Multi-strategy content extraction that applies per-site optimisations before
falling back to standard browser rendering. Useful for sites that serve content
in SSR HTML but hide it with client-side JavaScript, or sites behind bot
detection that block default request patterns.

```bash
# Enhanced extraction with multi-strategy cascade
flarecrawl scrape https://example.com/article --paywall

# JSON output shows which extraction strategy worked
flarecrawl scrape https://example.com/article --paywall --json
# metadata.source indicates the strategy used

# Batch mode
flarecrawl scrape --batch urls.txt --paywall --workers 5

# No Cloudflare auth required (direct HTTP strategies)
# With auth: falls through to browser rendering if direct strategies fail
```

Strategies are tried in order of speed and cost. Direct HTTP strategies consume
zero browser time. When Cloudflare auth is configured, per-site header
optimisations are also applied to browser rendering requests as a fallback.

Optional dependency for improved compatibility: `pip install curl_cffi`

### Stealth mode

Opt-in browser TLS fingerprint impersonation for direct HTTP requests. Uses
`curl_cffi` to send requests with a real Safari/Chrome TLS handshake, avoiding
bot detection systems that fingerprint JA3/JA4 hashes.

```bash
# Stealth mode for content negotiation and direct fetches
flarecrawl scrape https://example.com --stealth

# Combine with paywall extraction
flarecrawl scrape https://example.com --paywall --stealth --json

# Batch mode
flarecrawl scrape --batch urls.txt --stealth --workers 5
```

Requires: `pip install curl_cffi`

Without `--stealth`, requests use Python's default TLS stack (httpx/OpenSSL)
which is identifiable by bot detection systems. With `--stealth`, the TLS
Client Hello is indistinguishable from a real Safari browser.

### Content cleanup

Markdown output is automatically cleaned of common ad placeholders, share
buttons, newsletter prompts, copyright lines, and navigation chrome.
No flag needed - applied to all markdown output by default.

For HTML output, use `--clean` to strip ad/promo DOM elements:

```bash
# Strip ad containers, social share widgets, cookie banners from HTML
flarecrawl scrape https://example.com --format html --clean --json
```

### Agent-safe mode

Sanitise scraped content against adversarial AI agent traps. See
[Agent Safety](#agent-safety) for full details.

```bash
flarecrawl scrape https://example.com --agent-safe --json
flarecrawl scrape https://example.com --agent-safe --paywall --stealth --json
flarecrawl crawl https://example.com --wait --limit 50 --agent-safe
```

### Web search

Search the web and optionally scrape each result. Uses Jina Search API.

```bash
# Search and get results as JSON
flarecrawl search "python web scraping" --json

# Search and scrape each result through the normal pipeline
flarecrawl search "topic" --scrape --limit 5 --paywall --json

# Pipeline: search -> extract URLs -> batch scrape
flarecrawl search "query" --json | jq -r '.data[].url' > urls.txt
flarecrawl scrape --batch urls.txt --paywall --stealth --workers 5
```

Requires `JINA_API_KEY` env var (free at https://jina.ai/api-key).
With `--scrape`, all scrape flags apply (`--paywall`, `--stealth`, `--only-main-content`, `--clean`).

### Proxy support

Route all HTTP requests through a proxy. Affects CLI-to-Cloudflare API
connections and direct HTTP (stealth, negotiate, paywall). Does NOT affect the
Cloudflare browser-to-target connection (CF's browser uses its own IP).

```bash
# HTTP proxy
flarecrawl scrape https://example.com --proxy http://proxy:8080

# SOCKS5 proxy
flarecrawl scrape https://example.com --proxy socks5://localhost:9050

# Set default via env var
export FLARECRAWL_PROXY=socks5://localhost:9050
flarecrawl scrape https://example.com   # uses env var proxy
```

Supported on all commands: `scrape`, `crawl`, `download`, `extract`, `search`.

### Per-site rules

Customisable per-site header rules for enhanced extraction. Default rules ship
with the package; user overrides are loaded from `~/.config/flarecrawl/rules.yaml`.

```bash
# List all loaded rules
flarecrawl rules list --json

# Show rules for a domain
flarecrawl rules show www.nytimes.com

# Add a custom rule
flarecrawl rules add example.com --referer https://www.google.com/ --cookie "auth=1"

# Show file paths
flarecrawl rules path
```

Rules use Ladder-compatible YAML format:

```yaml
- domain: www.example.com
  headers:
    Referer: "https://www.google.com/"
    Cookie: "session=abc"
- domains:
    - a.com
    - b.com
  headers:
    Referer: "https://t.co/x?amp=1"
```

### Markdown content negotiation

Sites on Cloudflare (Pro+) can serve markdown directly via `Accept: text/markdown`
content negotiation. Flarecrawl auto-detects this on every scrape — when a site
supports it, content is fetched via a simple HTTP GET instead of headless Chromium.

```bash
# Auto-detect (default) — tries content negotiation first
flarecrawl scrape https://blog.cloudflare.com/some-post

# Force browser rendering (skip negotiation)
flarecrawl scrape https://blog.cloudflare.com/some-post --no-negotiate

# JSON output shows the source
flarecrawl scrape https://blog.cloudflare.com/some-post --json
# metadata.source: "content-negotiation" (no browser) or "browser-rendering"
# metadata.markdownTokens: 1234 (from x-markdown-tokens header)
# metadata.contentSignal: {"ai-train": "yes", ...}
```

Benefits when negotiation succeeds:
- **Faster** — ~100-200ms vs 2-3s for browser rendering
- **Cheaper** — zero browser time consumed
- **Higher quality** — server-side conversion by the site owner
- **Domain cached** — one probe per domain, batch-friendly

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

### negotiate — Domain capability cache

```bash
flarecrawl negotiate status              # Show domains that support text/markdown
flarecrawl negotiate status --json       # Machine-readable
flarecrawl negotiate clear               # Reset domain cache
```

Tracks which domains respond to `Accept: text/markdown`. Positive results cached 7 days, negative 24 hours.

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
| `FLARECRAWL_PROXY` | - | Default proxy URL (http/https/socks5) |
| `JINA_API_KEY` | - | Jina API key for `search` command |

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
| `firecrawl search QUERY` | `flarecrawl search QUERY` | Via Jina Search API |
| `firecrawl --status` | `flarecrawl --status` | Same |

### What's different

- **`search` command** — uses Jina Search API (requires `JINA_API_KEY`), with `--scrape` to scrape results
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

## Agent Safety

Flarecrawl is often the perception layer for AI agent workflows - scraped
content flows directly into LLM context windows and RAG systems. The
`--agent-safe` flag sanitises content against adversarial attacks before it
reaches the consuming agent.

### Background

This implementation is informed by
[AI Agent Traps](https://ssrn.com/abstract=6372438) (Franklin, Tomasev,
Jacobs, Leibo, Osindero - Google DeepMind, March 2026), which identifies
six categories of adversarial attacks against autonomous AI agents navigating
the web:

| Category | Target | Flarecrawl Defence |
|----------|--------|-------------------|
| Content Injection | Agent perception | **Defended** - hidden text, comments, attributes, unicode tricks, iframes, hidden inputs, CSS class hiding, meta tags |
| Semantic Manipulation | Agent reasoning | **Defended** - urgency clusters and authority claims flagged (not removed) |
| Behavioural Control | Agent actions | **Defended** - prompt injection patterns detected and stripped |
| Cognitive State | Agent memory/RAG | Upstream - content provenance via `metadata.source` |
| Systemic | Multi-agent networks | Upstream - outside scraping layer |
| Human-in-the-Loop | Human supervisor | Upstream - outside scraping layer |

### Usage

```bash
# Sanitise content for safe agent consumption
flarecrawl scrape https://example.com --agent-safe --json

# Combine with extraction flags
flarecrawl scrape https://example.com --agent-safe --paywall --stealth --json

# Batch mode
flarecrawl scrape --batch urls.txt --agent-safe --workers 5

# All commands support --agent-safe
flarecrawl crawl https://example.com --wait --limit 50 --agent-safe
flarecrawl download https://docs.example.com --limit 50 --agent-safe
flarecrawl extract "Get products" --urls https://shop.example.com --agent-safe --json

# Stdin pipe (sanitises local HTML)
cat page.html | flarecrawl scrape --stdin --agent-safe --json
```

### Two-phase sanitisation pipeline

Content passes through 13 sanitisers in two phases:

**Phase 1 (HTML)** - 8 sanitisers run on the DOM before markdown conversion:

| Sanitiser | Attack Vector | Action |
|-----------|--------------|--------|
| Hidden text | CSS hiding (`display:none`, `opacity:0`, off-screen, `clip-path`, `color:transparent`, etc.) | Removed |
| HTML comments | Instruction-like content in `<!-- -->` comments | Removed |
| Suspicious attributes | Injection patterns in `data-*`, `aria-label`, `alt`, `title` | Removed |
| Unicode tricks | Zero-width characters, bidirectional text overrides | Removed |
| Hidden iframes | `<iframe src="...">` with external content | Removed |
| Hidden inputs | `<input type="hidden" value="...">` with instruction patterns | Removed |
| CSS class hiding | `.d-none`, `.hidden`, `[hidden]` attribute with injection content | Removed |
| Meta injection | Custom `<meta>` tags with adversarial content (standard tags preserved) | Removed |

**Phase 2 (Text)** - 5 sanitisers run on markdown after conversion:

| Sanitiser | Attack Vector | Action |
|-----------|--------------|--------|
| Prompt injection | "ignore previous instructions", "SYSTEM:", role-play, delimiter injection | Removed |
| Semantic manipulation | Urgency clusters, authority claims | **Flagged only** |
| Homoglyph evasion | Cyrillic/Greek lookalike characters bypassing patterns | Removed |
| Markdown exfiltration | Image URLs with suspicious query params (IP addresses, exfil params) | **Flagged only** |
| HTML entity evasion | `&#73;gnore` entity-encoded injection patterns | Removed |

### False positive prevention

| Technique | How it works |
|-----------|-------------|
| Short-line bias | Prompt injection only strips lines <200 chars - articles *about* injection are preserved |
| Accessibility preservation | `.sr-only`/`.visually-hidden` text kept unless it matches injection patterns |
| Standard meta allowlist | `description`, `og:*`, `twitter:*`, `charset` meta tags never touched |
| Mixed-script detection | Homoglyph normalisation only on lines with both Latin and Cyrillic/Greek chars |
| Threshold gates | Hidden elements need 20+ chars; hidden inputs need 50+ chars + pattern match |
| Flag vs remove | Semantic manipulation and exfiltration are flagged, never removed |

### JSON output

When `--json` is used, `metadata.agentSafety` reports what was detected:

```json
{
  "metadata": {
    "agentSafety": {
      "sanitised": true,
      "findings": [
        {"category": "content_injection", "severity": "high",
         "description": "Hidden text via CSS (2 elements)", "action": "removed", "count": 2},
        {"category": "prompt_injection", "severity": "high",
         "description": "Prompt injection patterns removed (1 lines)", "action": "removed", "count": 1},
        {"category": "semantic_manipulation", "severity": "medium",
         "description": "Urgency language clusters (1 lines)", "action": "flagged", "count": 1}
      ],
      "stats": {
        "removed": 3,
        "flagged": 1,
        "byCategory": {
          "content_injection": 2,
          "prompt_injection": 1,
          "semantic_manipulation": 1
        }
      }
    }
  }
}
```

### Extensibility

New attack vectors are added by writing a function and decorating it:

```python
from flarecrawl.sanitise import register_html, register_text, Finding

@register_html
def sanitise_new_vector(soup):
    """HTML-level sanitiser - mutates soup, returns findings."""
    # ... detection and removal logic ...
    return [Finding(category="content_injection", severity="high",
                    description="New vector (N elements)", action="removed", count=n)]

@register_text
def sanitise_new_text_vector(text):
    """Text-level sanitiser - returns (cleaned_text, findings)."""
    # ... detection logic ...
    return cleaned_text, findings
```

Add a corpus fixture to `tests/corpus/attacks/` and the parametrised test
suite picks it up automatically.

### Test corpus

The `tests/corpus/` directory contains 61 fixture files that serve as both
test data and a living catalogue of known attack vectors:

- **`attacks/`** (45 files) - adversarial fixtures across 12 categories
- **`benign/`** (16 files) - false-positive traps: security articles, responsive
  CSS, ARIA accessibility, admin UIs, medical urgency, i18n content, code
  tutorials, system docs, journalism

Parametrised tests validate that every attack file produces findings and
every benign file produces zero removals.

## Pricing Details

| Tier | Browser Time | Concurrent Browsers |
|------|-------------|-------------------|
| **Free** | 10 min/day | Up to 120 (default) |
| **Paid** | 10 hr/month included, then $0.09/hr | Up to 120 (default), higher on request |

Browser time is shared between REST API calls and Workers bindings. Track your usage with `flarecrawl usage`.

## Project Structure

```
flarecrawl/
├── pyproject.toml              # Package config
├── AGENTS.md                   # AI agent context
├── README.md                   # This file
├── src/flarecrawl/
│   ├── __init__.py             # Version
│   ├── authcrawl.py            # Authenticated BFS crawler (session cookie propagation)
│   ├── batch.py                # Batch processing (parse + parallel workers)
│   ├── cache.py                # File-based response cache
│   ├── cli.py                  # Typer CLI (all commands)
│   ├── cdp.py                  # CDP WebSocket client (persistent sessions, DevTools Protocol)
│   ├── client.py               # CF Browser Run REST API client (httpx pooling, HTTP/2)
│   ├── config.py               # Credentials, usage tracking, env-var config, session storage
│   ├── cookies.py              # Cookie loading (Puppeteer/Netscape/Chrome), conversion, validation
│   ├── extract.py              # HTML extraction (main content, images, schema, tags)
│   ├── fetch.py                # Content-type aware download (binary, JSON, HTML)
│   ├── negotiate.py            # Markdown content negotiation (Accept: text/markdown)
│   ├── openapi.py              # OpenAPI/Swagger spec discovery and validation
│   ├── paywall.py              # Paywall bypass cascade (SSR, Referer, Wayback, Jina)
│   ├── rules.py                # Per-site YAML rulesets (load, merge, cache)
│   ├── search.py               # Web search via Jina Search API
│   ├── sanitise.py             # Agent-safety sanitisation (hidden text, injection, manipulation)
│   ├── stealth.py              # Stealth HTTP (curl_cffi TLS impersonation)
│   └── default_rules.yaml      # Shipped per-site header rules
└── tests/
    ├── conftest.py             # Test fixtures
    ├── corpus.py               # Feature test corpus (80 live tests x 8 sites)
    ├── corpus/                 # Agent-safety attack/benign fixture corpus (48 files)
    │   ├── attacks/            # Adversarial fixtures (35 files, 6 categories)
    │   └── benign/             # False-positive traps (13 files)
    ├── test_batch.py           # Batch module tests
    ├── test_cache.py           # Cache module tests
    ├── test_cli.py             # CLI tests
    ├── test_client.py          # Client tests
    ├── test_extract.py         # Extract module tests
    ├── test_paywall.py         # Paywall bypass tests
    ├── test_rules.py           # Per-site rules tests
    ├── test_cdp.py             # CDP client tests (69 tests)
    ├── test_sanitise.py        # Agent-safety tests (137 tests + corpus validation)
    └── test_search.py          # Search module tests
```

## Development

```bash
# Install dev dependencies
uv tool install --editable . --with pytest --with ruff

# Install CDP support (optional — needed for --cdp, interact, webmcp)
uv pip install websockets

# Run unit tests (723 tests, no network)
pytest tests/ -v

# Run live tests against public sites (needs CF auth)
PYTHONPATH=src pytest tests/live/ -v -m live

# Run CDP live tests (needs CF auth + websockets)
PYTHONPATH=src pytest tests/live/ -v -m cdp

# Lint
ruff check src/

# Reinstall after changes
uv tool install --editable .
```

## Forma Protocol

This tool follows the [Forma Protocol](https://github.com/forma-tools/forma).

## License

MIT
