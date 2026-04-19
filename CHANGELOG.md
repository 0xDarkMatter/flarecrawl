# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.21.0] - 2026-04-20

### Added

- `--browser-cookies chrome|firefox` flag on `scrape`, `interact`, `design extract` (parity with `videos`)
- `--session` flag on `crawl` for authenticated crawls (was missing)
- Live test suite for design extraction (`tests/live/test_design_live.py`)
- `frontier` listed in `[tool.forma].resources`; `status = "experimental"` field

### Fixed

- `--ignore-robots` on `crawl` no longer silently fails — prints warning pointing at `spider`/`authcrawl` (CF `/crawl` API has no robots bypass parameter)
- Design extract file writes now use UTF-8 encoding (fixes UnicodeEncodeError on Windows from block chars in coherence bars)

## [0.20.0] - 2026-04-20

### Added

- `_enrich_cdp_error()` helper that detects known CDP failure patterns (bot detection, timeouts, redirects, network errors, WebSocket issues, auth failures) and appends actionable `Suggestions:` block with CLI flags to try
- Applied CDP error enrichment at all CDP call sites: scrape, interact, design extract, design coherence, design diff, and videos commands
- 18 unit tests for CDP error enrichment

### Changed

- CHANGELOG.md restored as source of truth — README Recent Updates trimmed to last 5 releases

## [0.19.0] - 2026-04-19

### Added

- `flarecrawl videos` command — discover video URLs on web pages (mp4, webm, m3u8, YouTube, Vimeo embeds, OpenGraph `og:video`, JSON-LD `VideoObject`)
- Video discovery across 21 platforms including YouTube, Vimeo, Dailymotion, Twitch, Wistia, Brightcove, Vidyard, Loom, and more
- `--export-cookies` flag for yt-dlp Netscape cookie format export
- `--browser-cookies chrome|firefox` flag for local browser cookie extraction via rookiepy
- `--download` and `--download-dir` flags for direct yt-dlp integration
- `--depth N` for multi-page video discovery
- `spider` command alias for `authcrawl`

### Fixed

- 7 broken `console.print(err=True)` calls corrected to `console.print(..., stderr=True)`

## [0.18.0] - 2026-04-18

### Fixed

- Path-traversal vulnerability on `--resume JOB_ID` — job IDs are now sanitised
- Non-http(s) URLs (`file:`, `javascript:`, `data:`) blocked from entering the crawler
- Robots.txt and sitemap downloads capped to prevent hostile server OOM

### Changed

- Crawl loop refactored to ~half its previous size
- PEP 561 `py.typed` marker added for downstream type hint support
- 1027 tests

## [0.17.0] - 2026-04-18

### Added

- `flarecrawl authcrawl` command — industrial-scale authenticated BFS crawler for millions of URLs across tens of thousands of domains
- `--resume JOB_ID` to resume interrupted crawls, picking up exactly where you left off
- `--refresh-days N` weekly-refresh mode — only re-fetch pages changed since last run (ETag/Last-Modified)
- Fair round-robin scheduling across domains
- `--adaptive-delay` automatic politeness tuning based on server response times
- Auto-retry with exponential backoff and dead-letter inspector (`flarecrawl frontier dead-letter JOB_ID`)
- Circuit breaker — pauses domains after 10 consecutive failures
- Robots.txt compliance via protego
- `--tracing console|json|otlp` OpenTelemetry tracing for production observability
- URL canonicalisation with tracking-param stripping (`?utm_source=...` deduplication)

### Changed

- selectolax parser (10-30x faster than BeautifulSoup on hot paths), orjson, uvloop, tuned httpx pool — typical crawls 2-5x faster end-to-end
- 967 tests

## [0.16.0] - 2026-04-17

### Added

- `flarecrawl design extract` — generates DESIGN.md from any website with colors, typography, spacing, shadows, radii, layout, CSS variables, media queries, z-index
- 9-category Design Coherence scoring (A-F grades)
- `flarecrawl design coherence` for standalone scoring
- `flarecrawl design diff` for side-by-side design comparison
- HTML preview with visual swatches (`--preview`)
- `--session` support for authenticated design extraction
- CDP-backed live computed style extraction

## [0.15.0] - 2026-04-17

### Added

- `flarecrawl webmcp discover` and `flarecrawl webmcp call` for structured tool discovery on WebMCP-enabled sites
- `flarecrawl interact` command with `--fill`, `--click`, `--select` and human-like timing (Bezier mouse curves, variable keystroke delays)
- `flarecrawl cdp connect` — prints WebSocket URL for Playwright/Puppeteer integration
- `FLARECRAWL_CDP_ENDPOINT` env var for custom CDP backends (Oxylabs, Bright Data, local Chrome)
- `--tabs` flag for multi-URL session reuse
- `--stagehand` stub for future AI element finding
- Free tier warnings when approaching daily limits

### Fixed

- Live View URLs corrected to use `live.browser.run` hosted UI
- Session listing/close now uses real CF REST API
- Recording retrieval via `/recording/{session_id}`
- `keep_alive` capped at 600s (CF maximum)
- 740 tests

## [0.14.1] - 2026-04-16

### Added

- CDP WebSocket integration — `--cdp` flag for persistent browser sessions via Chrome DevTools Protocol
- `--interactive` human-in-the-loop auth flow (login in DevTools, cookies auto-saved)
- `--live-view` real-time browser debugging via Chrome DevTools
- Proper `--js-eval` via `Runtime.evaluate` (replaces addScriptTag hack)
- Real `--har` network capture via `Network.enable`
- `--record` session recordings (rrweb format)
- `--keep-alive N` persistent sessions with cross-invocation reuse
- `--save-cookies`/`--load-cookies` for authenticated scraping
- `--ignore-robots` on crawl
- `flarecrawl cdp sessions` and `flarecrawl cdp close` session management commands

### Changed

- Workers max increased from 10 to 50 (CF now supports 120 concurrent browsers)
- Rebranded to Cloudflare Browser Run
- 723 tests

---

## v0.14.0 — 2026-04-16

### New Modules

- **`cookies.py`** — Cookie file loading with auto-detection of Puppeteer JSON, Chrome DevTools export, and Netscape text format. Includes `cookies_to_httpx()`, `cookies_to_header()` (domain-filtered), and `validate_cookies()` (HEAD request test).
- **`fetch.py`** — Content-type aware downloading. `detect_content_type()` does a HEAD probe, `download_binary()` streams large files with chunk-based progress, `build_session()` constructs an authenticated `httpx.Client`. `ContentInfo` and `DownloadResult` dataclasses.
- **`openapi.py`** — OpenAPI/Swagger spec discovery. `discover_specs()` finds specs from `<a>` links, SwaggerUI `<script>` configs, and `<link>` tags. `probe_common_paths()` HEAD-checks `/swagger.json`, `/openapi.json`, `/openapi.yaml`, and 7 other common paths. `validate_spec()` checks for `openapi`/`swagger` top-level keys and counts endpoints. `download_spec()` fetches and validates.
- **`authcrawl.py`** — Authenticated BFS crawler. `AuthenticatedCrawler` carries cookies through every request, respects `max_depth`/`max_pages`, supports `include_patterns`/`exclude_patterns` (regex or substring), yields `CrawlResult` async iterator. Uses asyncio semaphore (same pattern as `batch.py`).

### New CLI Commands

- **`flarecrawl fetch URL`** — Content-type aware fetch. HTML → markdown (via CF), binary → stream download, JSON → pretty-print. Supports `--session FILE`, `--session @NAME`, `--auth`, `--headers`, `--output`, `--stealth`, `--proxy`, `--overwrite`, `--json`. Rich progress bar for files > 1 MB.
- **`flarecrawl openapi URL`** — Discover and optionally download OpenAPI/Swagger specs. Flags: `--download/-d`, `--output/-o`, `--probe`, `--session`, `--json`.
- **`flarecrawl session save NAME --file FILE`** — Save cookies to a named session.
- **`flarecrawl session list`** — List saved sessions.
- **`flarecrawl session show NAME`** — Show cookies in a saved session.
- **`flarecrawl session delete NAME`** — Delete a saved session.
- **`flarecrawl session validate NAME URL`** — HEAD-test cookies against a URL.

### Enhancements

- **`discover --openapi`** — New flag that additionally probes for OpenAPI specs and includes them in the JSON output under `meta.api_specs`.
- **`scrape --session`** — Refactored to use `cookies.py` instead of inline JSON parsing; now supports Netscape format and Chrome DevTools exports in addition to Puppeteer arrays.
- **`config.py`** — `get_sessions_dir()`, `save_session()`, `load_session()`, `list_sessions()`, `delete_session()` session persistence functions.

### Tests

- `tests/test_cookies.py` — Format loading, conversion, domain filtering (26 tests)
- `tests/test_fetch.py` — Content-type detection, filename derivation, build_session (20 tests)
- `tests/test_openapi.py` — Spec discovery from HTML, validation logic (18 tests)
- `tests/test_authcrawl.py` — Config defaults, URL filtering, depth limits (17 tests)

---

## v0.13.0 — 2026-04-14

Performance: optimize sanitise pipeline — 51% faster via keyword pre-checks. Extend `--agent-safe` with 7 new attack vector sanitisers (v0.12.1).

## v0.12.1 — 2026-04-06

Extended `--agent-safe` attack vector coverage: hidden iframes, hidden form inputs, CSS class hiding, meta tag injection, homoglyph evasion (Cyrillic/Greek), markdown exfiltration detection, HTML entity evasion. 13 sanitisers total, 61-file corpus, 564 tests.

## v0.12.0 — 2026-04-06

`--agent-safe` flag for adversarial content sanitisation.

## v0.11.0 — 2026-04-03

`search` command (Jina Search), `--proxy` flag, `--clean`, per-site YAML rulesets, 378 tests.

## v0.10.0 — 2026-04-02

Enhanced content extraction (`--paywall`), stealth mode (`--stealth`), 343 tests.

## v0.9.0 — 2026-03-26

Markdown content negotiation, domain capability cache, 278 tests.

## v0.8.0 — 2026-03-20

`--scroll`, `--query`, `--precision`/`--recall`, `--deduplicate`, `--session`, `flarecrawl batch`, 215 tests.
