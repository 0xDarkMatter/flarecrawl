You are a Forma Architect agent (Upgrade mode).
Working directory: X:/Forma/forma/flarecrawl

Read X:/Forma/forma/flarecrawl/AGENTS.md for architecture and conventions.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/__init__.py for current version.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/cli.py for existing commands and patterns.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/client.py for API client patterns.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/config.py for config/credential patterns.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/stealth.py for curl_cffi session patterns.
Read X:/Forma/forma/flarecrawl/src/flarecrawl/extract.py for extraction patterns.

## Upgrade Specification

Tool: flarecrawl (v0.13.0 -> v0.14.0)
Package: flarecrawl
Source: X:/Forma/forma/flarecrawl/src/flarecrawl/

## Change Plan

### Phase 1: Cookie Management Module (Foundation)

1.1 Create src/flarecrawl/cookies.py -- cookie loading, format conversion, validation
    - What: New module for cookie handling across all auth features
    - Functions:
      * load_cookies(path: Path) -> list[dict] -- Load from JSON (Puppeteer format with name/value/domain/path keys, Netscape/Mozilla format, Chrome DevTools export format). Auto-detect format.
      * cookies_to_httpx(cookies: list[dict]) -> httpx.Cookies -- Convert Puppeteer-style cookie dicts to httpx.Cookies for direct HTTP requests
      * cookies_to_header(cookies: list[dict], domain: str) -> str -- Build Cookie: header string, filtering by domain match
      * validate_cookies(cookies: list[dict], url: str) -> dict -- Test cookies against a URL with HEAD request, return {valid: bool, status_code: int, redirected_to: str|None}
    - Pattern: Follow config.py style for module structure. Use httpx for validation.
    - Also refactor existing cookie loading in cli.py (_session_cookies logic around line 1300-1347) to use this module

1.2 Add session persistence to config.py
    - What: get_sessions_dir() -> Path, save/load named sessions
    - Functions:
      * get_sessions_dir() -> Path -- returns <config_dir>/sessions/, creates if needed
      * save_session(name: str, cookies: list[dict]) -> Path -- save to sessions/<name>.json
      * load_session(name: str) -> list[dict] -- load from sessions/<name>.json
      * list_sessions() -> list[str] -- list saved session names
      * delete_session(name: str) -> bool -- delete a saved session

### Phase 2: Binary File Download (Core Feature)

2.1 Create src/flarecrawl/fetch.py -- content-type aware downloading
    - What: New module for binary/streaming downloads that bypass CF Browser Rendering
    - Functions:
      * detect_content_type(url: str, session: httpx.Client | None, headers: dict | None) -> ContentInfo dataclass -- HEAD request returning content_type, size, filename (from Content-Disposition), is_binary
      * download_binary(url: str, session: httpx.Client, output_path: Path, progress_callback: Callable | None) -> DownloadResult dataclass -- Stream binary download with chunked writing
      * build_session(cookies: list[dict] | None, auth: tuple | None, headers: dict | None, proxy: str | None) -> httpx.Client -- Build configured httpx client from cookie/auth/header/proxy options
    - Dataclasses:
      * ContentInfo(content_type: str, size: int | None, filename: str | None, is_binary: bool, is_json: bool)
      * DownloadResult(path: Path, content_type: str, size: int, elapsed: float, filename: str)
    - Pattern: Use httpx streaming (response.stream()) for large files. Derive filename from Content-Disposition header or URL path. For stealth mode, use curl_cffi sessions following stealth.py patterns.

2.2 Add "fetch" command to cli.py
    - What: New top-level command for content-type-aware fetching
    - Signature: flarecrawl fetch URL [--session FILE] [--auth USER:PASS] [--headers K:V] [--output/-o PATH] [--stealth] [--proxy URL] [--json] [--overwrite]
    - Behavior:
      1. HEAD request to detect content type via fetch.py
      2. If HTML -> fall through to existing _scrape_single() for markdown conversion
      3. If binary (ZIP, PDF, etc.) -> stream download to --output path with rich progress bar
      4. If JSON -> download and pretty-print (or save to --output)
      5. If no --output and binary -> derive filename from URL/Content-Disposition, save to CWD
    - Support --session @NAME syntax: if session value starts with @, resolve from saved sessions dir
    - Progress: Use rich.progress.Progress with DownloadColumn and TransferSpeedColumn for files > 1MB
    - JSON output envelope: {"data": {"path": str, "content_type": str, "size": int, "elapsed": float}, "meta": {"url": str}}
    - Pattern: Follow existing command patterns in cli.py -- same console, exit codes, _error(), _output_json()

### Phase 3: OpenAPI/Swagger Auto-Discovery

3.1 Create src/flarecrawl/openapi.py -- spec discovery and download
    - What: New module for finding OpenAPI/Swagger specs on documentation sites
    - Functions:
      * discover_specs(html: str, base_url: str) -> list[SpecDiscovery] -- Parse HTML for API spec links from 5 sources:
        a. <a href> links matching swagger.json, openapi.json, openapi.yaml, api-docs, *.json with swagger/openapi in path
        b. <script> tags containing SwaggerUI config (find url: property pointing to spec)
        c. <link> or <meta> tags with API documentation rels
        d. Text content matching "Download OpenAPI", "API Specification", "Swagger" near links
      * probe_common_paths(base_url: str, session: httpx.Client | None) -> list[SpecDiscovery] -- HEAD-check common spec paths: /swagger/v1/swagger.json, /swagger.json, /openapi.json, /openapi.yaml, /api-docs, /v2/api-docs, /v3/api-docs, /api/swagger.json, /api/openapi.json, /_api/swagger.json
      * validate_spec(content: str | dict) -> SpecValidation -- Quick check for openapi/swagger top-level keys, extract version/title/endpoint count
      * download_spec(url: str, session: httpx.Client | None, output_path: Path | None) -> SpecResult -- Download, validate, optionally save
    - Dataclasses:
      * SpecDiscovery(url: str, source: str, format: str, confidence: float) -- source is "link"|"swagger-ui"|"common-path"|"text-match"
      * SpecValidation(valid: bool, version: str | None, title: str | None, endpoint_count: int | None)
      * SpecResult(url: str, validation: SpecValidation, path: Path | None, size: int)
    - Pattern: Use beautifulsoup4 (already a dependency) for HTML parsing. Use httpx for probing.

3.2 Add "openapi" command to cli.py
    - What: New top-level command for OpenAPI spec discovery
    - Signature: flarecrawl openapi URL [--download/-d] [--output/-o DIR] [--probe] [--session FILE] [--json]
    - Behavior:
      1. Scrape the URL (via _scrape_single with format=html) to get page HTML
      2. Run discover_specs() on the HTML
      3. If --probe, also run probe_common_paths()
      4. Display found specs as rich table (URL | Source | Format | Confidence)
      5. If --download, download all discovered specs to --output dir (default: ./specs/)
      6. If --json, output as {"data": [...specs], "meta": {"url": str, "count": int}}
    - Pattern: Follow discover command pattern for display

3.3 Add --openapi flag to existing "discover" command
    - What: Extend discover command to include OpenAPI spec discovery
    - Where: cli.py discover command (find it by searching for discover_app or the discover function)
    - Add: openapi: bool = typer.Option(False, "--openapi", help="Also discover OpenAPI/Swagger specs")
    - When --openapi is set, after normal discovery (sitemap/feeds/links), also run discover_specs() on the page HTML and probe_common_paths(), merge results into the discovery output under an "openapi" key

### Phase 4: Authenticated Crawl (Client-Side)

4.1 Create src/flarecrawl/authcrawl.py -- client-side authenticated crawler
    - What: BFS crawler that carries session cookies across pages, using existing scrape capabilities per page
    - Classes:
      * CrawlConfig dataclass: seed_url, cookies, max_depth (default 3), max_pages (default 50), include_patterns (list[str] | None), exclude_patterns (list[str] | None), format (default "markdown"), workers (default 3), delay (default 1.0), output_dir (Path | None)
      * AuthenticatedCrawler:
        - __init__(self, client: Client, config: CrawlConfig)
        - crawl(self, progress_callback: Callable | None = None) -> Iterator[CrawlResult]
        - Internal: BFS queue, visited set, depth tracking, URL filtering by domain + patterns
    - CrawlResult dataclass: url, depth, content, content_type, links_found (int), elapsed (float), error (str | None)
    - Key behavior:
      * Start at seed URL, scrape with cookies via client
      * Extract links from each page (use format="links" or parse from HTML)
      * Filter: same domain only, respect include/exclude glob patterns, skip already-visited
      * BFS with depth tracking, stop at max_depth or max_pages
      * Politeness delay between requests
      * Yield results as iterator for NDJSON streaming
      * Workers: use asyncio semaphore for concurrent requests (follow batch.py patterns)
    - Pattern: Follow batch.py for concurrency patterns. Follow client.py for API interaction.

4.2 Wire --session into existing crawl/download commands in cli.py
    - What: When --session is provided to crawl or download, switch from CF server-side crawl to client-side AuthenticatedCrawler
    - Where: crawl command and download command in cli.py
    - Logic: if session cookies are provided -> instantiate AuthenticatedCrawler with those cookies -> use client-side crawl instead of CF /crawl endpoint
    - Add --delay flag (float, default 1.0) to crawl/download for politeness interval
    - Add --include-pattern and --exclude-pattern flags for URL filtering
    - When saving files (download command), preserve URL path structure in output directory
    - Generate index.json manifest in output directory: {url: local_path} mapping

### Phase 5: Session Management Sub-App

5.1 Add "session" sub-app to cli.py
    - What: New typer sub-app for cookie/session management
    - Commands:
      * flarecrawl session save NAME --file cookies.json -- save cookies to named session
      * flarecrawl session list [--json] -- list saved sessions with metadata (name, domain count, cookie count, created date)
      * flarecrawl session show NAME [--json] -- display session contents (domains, cookie names, expiry)
      * flarecrawl session delete NAME -- delete a saved session
      * flarecrawl session validate NAME URL [--json] -- test saved session cookies against a URL
    - Pattern: Follow auth_app pattern for sub-app structure

### Phase 6: Tests

6.1 Create tests/test_cookies.py
    - Test load_cookies with Puppeteer format, Netscape format, Chrome export format
    - Test cookies_to_httpx conversion
    - Test cookies_to_header with domain filtering
    - Test format auto-detection

6.2 Create tests/test_fetch.py
    - Test ContentInfo detection from various content types
    - Test filename derivation from Content-Disposition and URL
    - Test build_session with various auth options

6.3 Create tests/test_openapi.py
    - Test discover_specs with HTML containing swagger-ui, direct links, meta tags
    - Test probe_common_paths path list
    - Test validate_spec with valid/invalid specs

6.4 Create tests/test_authcrawl.py
    - Test CrawlConfig defaults
    - Test URL filtering (domain, patterns, visited)
    - Test depth limiting

### Phase 7: Documentation

7.1 Update __init__.py version to "0.14.0"
7.2 Update pyproject.toml version to "0.14.0"
7.3 Create CHANGELOG.md with v0.14.0 entry documenting all new features
7.4 Update AGENTS.md command reference with new commands (fetch, openapi, session)
7.5 Update README.md with new command examples and features

## Upgrade Rules

### Reading first
1. Read AGENTS.md to understand architecture, conventions, key functions
2. Read the source files you will modify to understand existing patterns
3. Read existing tests to understand test patterns and what are covered
4. Follow the existing code style -- do not introduce new patterns

### Implementation
5. Make changes incrementally -- one feature group at a time
6. Reuse existing helpers (_error, _output_json, _scrape_single, etc.)
7. Follow the parameter naming conventions of adjacent commands
8. Add --json support to any new command that outputs data
9. New modules follow existing module patterns (imports, docstrings, error handling)

### Client changes
10. New utility modules (cookies.py, fetch.py, openapi.py) are standalone -- they import from flarecrawl but do not modify existing modules
11. authcrawl.py uses Client as-is, no modifications to client.py needed
12. Cookie loading refactor in cli.py replaces inline code with cookies.py calls

### Testing
13. Write tests for new modules (mock httpx responses where needed)
14. Run full test suite -- ALL existing tests must still pass
15. Target: existing tests + new tests covering every new feature

### Documentation
17. Update __init__.py and pyproject.toml version to 0.14.0
18. Add CHANGELOG.md entry for v0.14.0
19. Update AGENTS.md with new command reference entries
20. Update README.md with new command examples

### Commits
21. Feature commit: "feat: add binary download, OpenAPI discovery, authenticated crawl, session management (v0.14.0)"
22. Docs commit: separate commit for version bump + docs updates
23. Do NOT push to remote
24. Git identity: 0xDarkMatter. No Co-Authored-By.

## Pre-Commit Checks

Before committing, verify:
- [ ] All existing tests pass
- [ ] New tests pass
- [ ] No import errors (python -c "from flarecrawl.cli import app")
- [ ] --help shows new commands (fetch, openapi, session)
- [ ] Version bumped in __init__.py and pyproject.toml
- [ ] CHANGELOG.md created/updated
- [ ] No absolute drive paths leaked into README or AGENTS.md
