# Changelog

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
