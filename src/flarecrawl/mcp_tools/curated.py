"""T2 Curated tool handlers for the flarecrawl MCP surface.

17 tools: web_search, fetch_url, page_links, urls_discover, page_schema,
page_favicon, page_screenshot, page_pdf, page_interact, tech_detect,
openapi_discover, crawl_start, crawl_status, crawl_results, site_download,
session_list, session_inspect.

All handlers are pure functions returning dicts.  No ``mcp`` package import.
Agent-safe is ON by default for content-returning tools (overridable).
"""

from __future__ import annotations

from typing import Any

from ._exec import _DEFAULT_MAX_CHARS, resolve_binary_output, run_cli

# ---------------------------------------------------------------------------
# T2 Curated handlers
# ---------------------------------------------------------------------------


def web_search_handler(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search the web and return result URLs, titles, and snippets.

    Return web search results without scraping. Faster than research_web
    when you only need to know what URLs exist for a query.

    Use this for: finding URLs, checking if a topic has web coverage,
    building a URL list for downstream scraping. Prefer research_web when
    you need the content of the results.

    Parameters:
      query (str, required) — The search query.
      limit (int, 10) — Maximum results to return.

    Returns:
      {"ok": true, "data": [{"url": str, "title": str, "snippet": str}],
        "meta": {"query": str, "count": int}}

    Default behaviour:
      Uses Jina search if JINA_API_KEY is set, DuckDuckGo otherwise.

    Limitations:
      - No scraping — snippets only, not full page content
      - Result quality varies by search provider
      - JINA_API_KEY improves quality (see permissions_check)

    When to use vs alternatives:
      research_web — search + scrape in one call
      urls_discover — discover URLs on a known site (no search)
    """
    args = ["search", query, "--json", f"--limit={limit}"]
    return run_cli(args, tool_name="web_search", max_chars=None)


def fetch_url_handler(
    url: str,
    output_path: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    stealth: bool = False,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Fetch a URL with automatic routing: binary download, JSON, text, or browser HTML.

    Four-branch routing: binary content-type → download to disk; JSON → parsed;
    raw text (XML/CSV/RSS/YAML) → text; HTML → CF browser render.

    Use this for: downloading files, fetching APIs, reading raw content types.
    Prefer read_page for HTML pages (better extraction pipeline).

    Parameters:
      url (str, required) — The URL to fetch.
      output_path (str, optional) — Save binary output to this path.
        Required if the URL returns binary content.
      max_chars (int, 40000) — Truncate text output.
      stealth (bool, false) — Use TLS impersonation (requires curl_cffi).
      agent_safe (bool, true) — Apply agent-safe sanitisation for HTML.

    Returns:
      Text/JSON: {"ok": true, "data": {...}, "meta": {"content_type": str}}
      Binary: {"ok": true, "data": {"path": str, "bytes": int}}

    Default behaviour:
      Auto-detects content type and routes accordingly.

    Limitations:
      - Binary output requires output_path or writes to temp dir
      - Stealth requires curl_cffi (see permissions_check)
      - CF browser only for HTML — raw types are fetched directly

    When to use vs alternatives:
      read_page — HTML pages with better extraction pipeline
      fetch_raw — full flag fidelity (session, impersonate, all flags)
    """
    # Determine if binary output is expected
    resolved_path = None
    lower_url = url.lower()
    likely_binary = any(
        lower_url.endswith(ext)
        for ext in (".pdf", ".zip", ".xlsx", ".docx", ".png", ".jpg", ".gif", ".mp4", ".csv")
    )

    if likely_binary:
        suffix = "." + lower_url.split(".")[-1] if "." in lower_url.split("/")[-1] else ".bin"
        resolved_path = resolve_binary_output(output_path, prefix="fc-fetch", suffix=suffix)

    args = ["fetch", url, "--json"]
    if stealth:
        args.append("--stealth")
    if resolved_path:
        args.extend(["-o", resolved_path])
    if agent_safe and not resolved_path:
        args.append("--agent-safe")

    return run_cli(
        args,
        tool_name="fetch_url",
        max_chars=max_chars if not resolved_path else None,
        inject_agent_safe=False,
        binary_output_path=resolved_path,
    )


def page_links_handler(
    url: str,
    include_subdomains: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Discover URLs linked from a page.

    Return all links found on a page, optionally including subdomain links.

    Use this for: building a URL list for crawling, checking site structure,
    finding linked resources.

    Parameters:
      url (str, required) — The page URL to map.
      include_subdomains (bool, false) — Include links to subdomains.
      limit (int, 200) — Maximum links to return.

    Returns:
      {"ok": true, "data": [str, ...], "meta": {"count": int, "url": str}}

    Default behaviour:
      Returns all same-domain links found on the page.

    Limitations:
      - Single page only — use crawl_start for multi-page discovery
      - Dynamic content may require JS rendering (use page_links from scrape_raw)

    When to use vs alternatives:
      urls_discover — sitemap + feed + links combined
      crawl_start — multi-page crawl
    """
    args = ["map", url, "--json"]
    if include_subdomains:
        args.append("--include-subdomains")

    result = run_cli(args, tool_name="page_links", max_chars=None)
    if result.get("ok") and isinstance(result.get("data"), list):
        data = result["data"][:limit]
        result["data"] = data
        result.setdefault("meta", {})["count"] = len(data)
    return result


def urls_discover_handler(
    url: str,
    limit: int = 500,
    sitemaps: bool = True,
    feeds: bool = True,
    links: bool = True,
) -> dict[str, Any]:
    """Discover URLs on a site from sitemaps, RSS feeds, and page links.

    Aggregate URL discovery from multiple sources: sitemap.xml, RSS/Atom feeds,
    and page links.

    Use this for: finding all URLs on a site before crawling, discovering
    content feeds, mapping the site URL space.

    Parameters:
      url (str, required) — The site URL.
      limit (int, 500) — Maximum URLs to return.
      sitemaps (bool, true) — Include sitemap.xml discovery.
      feeds (bool, true) — Include RSS/Atom feed discovery.
      links (bool, true) — Include page link discovery.

    Returns:
      {"ok": true, "data": [str, ...], "meta": {"count": int, "sources": [str]}}

    Default behaviour:
      Discovers from all three sources and deduplicates.

    Limitations:
      - Some sites block sitemap access
      - Link discovery is single-page only (root URL)

    When to use vs alternatives:
      page_links — page links only (faster)
      crawl_start — full multi-page discovery + content
    """
    args = ["discover", url, "--json"]
    if not sitemaps:
        args.append("--no-sitemaps")
    if not feeds:
        args.append("--no-feeds")
    if not links:
        args.append("--no-links")

    result = run_cli(args, tool_name="urls_discover", max_chars=None)
    if result.get("ok") and isinstance(result.get("data"), list):
        data = result["data"][:limit]
        result["data"] = data
        result.setdefault("meta", {})["count"] = len(data)
    return result


def page_schema_handler(
    url: str,
    schema_type: str | None = None,
) -> dict[str, Any]:
    """Extract structured data from a page: LD+JSON, OpenGraph, Twitter cards.

    Return structured metadata embedded in the page (schema.org, OG, Twitter).

    Use this for: extracting article metadata, product schema, event data,
    organisation information.

    Parameters:
      url (str, required) — The page URL.
      schema_type (str, optional) — Filter to a schema type. Examples:
        "Product", "Article", "Event", "Organization".

    Returns:
      {"ok": true, "data": {"ldjson": [...], "opengraph": {...},
        "twitter": {...}}, "meta": {}}

    Default behaviour:
      Returns all structured data found (LD+JSON + OG + Twitter).

    Limitations:
      - Only extracts inline metadata — does not follow schema URLs
      - Some sites embed schema in JS-rendered content (use scrape_raw --js)

    When to use vs alternatives:
      extract_data — AI-powered extraction for non-structured content
      page_links — link extraction only
    """
    args = ["schema", url, "--json"]
    if schema_type:
        args.extend(["--type", schema_type])

    return run_cli(args, tool_name="page_schema", max_chars=None)


def page_favicon_handler(
    url: str,
    all_favicons: bool = False,
) -> dict[str, Any]:
    """Find and return the best favicon URL for a site.

    Discover the site's favicon(s), returning the best quality option
    or all available options.

    Use this for: branding research, site identification, building site
    catalogues with icons.

    Parameters:
      url (str, required) — The site URL.
      all_favicons (bool, false) — Return all discovered favicons, not just best.

    Returns:
      {"ok": true, "data": {"url": str, "format": str} | [{"url":..., "size":...}],
        "meta": {}}

    Default behaviour:
      Returns the single best-quality favicon URL.

    Limitations:
      - Apple touch icons and manifest icons may not be discovered on all sites
      - SVG favicons are returned as URLs, not data URIs

    When to use vs alternatives:
      site_overview — comprehensive site profile including favicon
    """
    args = ["favicon", url, "--json"]
    if all_favicons:
        args.append("--all")

    return run_cli(args, tool_name="page_favicon", max_chars=None)


def page_screenshot_handler(
    url: str,
    output_path: str | None = None,
    full_page: bool = False,
    selector: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Take a screenshot of a page. Returns file path, not image data.

    Render a page and save a screenshot to disk. Returns the file path
    and byte size.

    Use this for: visual verification, archiving page appearance, monitoring
    visual regressions.

    Parameters:
      url (str, required) — The page URL.
      output_path (str, optional) — Save to this path. Defaults to temp dir.
      full_page (bool, false) — Capture full scrollable page.
      selector (str, optional) — CSS selector to capture a specific element.
      width (int, optional) — Viewport width in pixels.
      height (int, optional) — Viewport height in pixels.

    Returns:
      {"ok": true, "data": {"path": str, "bytes": int}}
      Note: path is a local file path — read separately if needed.

    Default behaviour:
      Captures viewport screenshot, saves to flarecrawl-mcp temp dir.

    Limitations:
      - Returns file path, not base64 image data — read the file separately
      - Requires CF auth (browser rendering)
      - Dynamic content may need --wait-until flags (use scrape_raw)

    When to use vs alternatives:
      page_pdf — PDF output
      scrape_raw — custom wait/interaction before screenshot
    """
    resolved = resolve_binary_output(output_path, prefix="fc-screenshot", suffix=".png")
    args = ["screenshot", url, "-o", resolved]
    if full_page:
        args.append("--full-page")
    if selector:
        args.extend(["--selector", selector])
    if width:
        args.extend(["--width", str(width)])
    if height:
        args.extend(["--height", str(height)])

    return run_cli(
        args,
        tool_name="page_screenshot",
        max_chars=None,
        binary_output_path=resolved,
    )


def page_pdf_handler(
    url: str,
    output_path: str | None = None,
    landscape: bool = False,
) -> dict[str, Any]:
    """Generate a PDF of a page. Returns file path, not PDF data.

    Render a page and save it as a PDF. Returns the file path and size.

    Use this for: archiving pages, creating printable versions, document
    generation from web content.

    Parameters:
      url (str, required) — The page URL.
      output_path (str, optional) — Save to this path. Defaults to temp dir.
      landscape (bool, false) — Landscape orientation.

    Returns:
      {"ok": true, "data": {"path": str, "bytes": int}}
      Note: path is a local file path — read separately if needed.

    Default behaviour:
      Generates portrait PDF, saves to flarecrawl-mcp temp dir.

    Limitations:
      - Returns file path, not base64 PDF data — read the file separately
      - Requires CF auth (browser rendering)
      - JavaScript-heavy pages may need extra wait time (use scrape_raw)

    When to use vs alternatives:
      page_screenshot — PNG screenshot
      site_download — download entire site as files
    """
    resolved = resolve_binary_output(output_path, prefix="fc-pdf", suffix=".pdf")
    args = ["pdf", url, "-o", resolved]
    if landscape:
        args.append("--landscape")

    return run_cli(
        args,
        tool_name="page_pdf",
        max_chars=None,
        binary_output_path=resolved,
    )


def page_interact_handler(
    url: str,
    fill: list[str] | None = None,
    click: list[str] | None = None,
    screenshot_path: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Fill forms, click elements, and extract content from interactive pages.

    Interact with page elements (fill inputs, click buttons) then extract
    the resulting content.

    Use this for: form submission, navigating pagination, triggering JS
    interactions before reading content.

    Parameters:
      url (str, required) — The page URL.
      fill (list[str], optional) — Fill directives: "selector=value".
        Example: ["#email=test@example.com", "#password=secret"].
      click (list[str], optional) — CSS selectors to click.
        Example: ["button[type=submit]", ".load-more"].
      screenshot_path (str, optional) — Save screenshot after interactions.
      max_chars (int, 40000) — Truncate content output.
      agent_safe (bool, true) — Apply agent-safe sanitisation.

    Returns:
      {"ok": true, "data": {"content": str, "screenshot": str|null}, "meta": {}}

    Default behaviour:
      Fills and clicks in order, extracts final page content.

    Limitations:
      - Not available in read-only mode
      - CAPTCHAs and interactive auth require CLI (see coverage gaps)
      - Requires CF auth (browser rendering)

    When to use vs alternatives:
      read_page — simple read without interaction
      scrape_raw — advanced interaction flags (record, cdp, etc.)
    """
    args = ["interact", url, "--json"]

    for f in (fill or []):
        args.extend(["--fill", f])
    for c in (click or []):
        args.extend(["--click", c])

    screenshot_resolved = None
    if screenshot_path:
        screenshot_resolved = resolve_binary_output(screenshot_path, prefix="fc-interact", suffix=".png")
        args.extend(["--screenshot", screenshot_resolved])

    if agent_safe:
        args.append("--agent-safe")

    return run_cli(args, tool_name="page_interact", max_chars=max_chars)


def tech_detect_handler(
    url: str,
    cdp: bool = False,
    min_confidence: int = 0,
    only_categories: str | None = None,
    exclude_categories: str = "Miscellaneous,Security,Tag managers,RUM",
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Detect technologies used by a website with noise filtering.

    Identify the technology stack of a website: CMS, frameworks, analytics,
    CDN, hosting, etc.

    Use this for: competitive intelligence, determining integration options,
    site profiling.

    Parameters:
      url (str, required) — The site URL.
      cdp (bool, false) — Use CDP/Playwright for JS-heavy sites.
      min_confidence (int, 0) — Minimum confidence score 0-100.
      only_categories (str, optional) — Comma-separated category whitelist.
        Example: "CMS,Frameworks,Ecommerce".
      exclude_categories (str, "Miscellaneous,Security,...") — Categories to
        exclude. Defaults to common noise categories.
      agent_safe (bool, true) — Sanitise HTML before detection.

    Returns:
      {"ok": true, "data": [{"name": str, "category": str, "confidence": int,
        "version": str|null}], "meta": {}}

    Default behaviour:
      Detects all technologies with noise categories excluded.
      See capabilities() for full category list.

    Limitations:
      - Detection based on Wappalyzer fingerprints — may miss custom stacks
      - Confidence < 50 indicates weak signal

    When to use vs alternatives:
      tech_detect_raw — full flag fidelity (stdin HTML, render, custom patterns)
      site_overview — tech detection plus schema/links/favicon in one call
    """
    args = ["tech-detect", url, "--json"]
    if cdp:
        args.append("--cdp")
    if min_confidence > 0:
        args.extend(["--min-confidence", str(min_confidence)])
    if only_categories:
        args.extend(["--only-categories", only_categories])
    if exclude_categories:
        args.extend(["--exclude-categories", exclude_categories])

    return run_cli(args, tool_name="tech_detect", max_chars=None)


def openapi_discover_handler(
    url: str,
    probe: bool = True,
    download_dir: str | None = None,
) -> dict[str, Any]:
    """Discover and optionally download OpenAPI/Swagger specifications for a site.

    Probe common API specification paths and return any discovered specs.

    Use this for: finding if a site has a public API, downloading API specs
    for integration planning.

    Parameters:
      url (str, required) — The site URL.
      probe (bool, true) — Probe common spec paths (/openapi.json,
        /swagger.json, etc.).
      download_dir (str, optional) — Directory to download discovered specs.

    Returns:
      {"ok": true, "data": {"specs": [{"url": str, "format": str}], ...},
        "meta": {}}

    Default behaviour:
      Probes common spec paths and returns discovered URLs.

    Limitations:
      - Only finds specs at standard paths — custom paths not discovered
      - Private/authenticated specs not accessible without session

    When to use vs alternatives:
      site_overview — full site profile including API discovery
      fetch_url — download a known spec URL directly
    """
    args = ["openapi", url, "--json"]
    if probe:
        args.append("--probe")
    if download_dir:
        args.extend(["--download", "-o", download_dir])

    return run_cli(args, tool_name="openapi_discover", max_chars=None)


def crawl_start_handler(
    url: str,
    limit: int = 50,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    no_render: bool = False,
) -> dict[str, Any]:
    """Start an async site crawl. Returns a job_id — poll crawl_status for progress.

    Fire-and-forget multi-page crawl. Returns immediately with a job_id.
    Poll crawl_status(job_id) to check progress, then crawl_results(job_id)
    for content.

    Use this for: crawling documentation sites, building content archives,
    discovering all pages on a site with their content.

    Parameters:
      url (str, required) — The root URL to crawl.
      limit (int, 50) — Maximum pages to crawl.
      include_paths (list[str], optional) — URL path prefixes to include.
        Example: ["/docs", "/api"].
      exclude_paths (list[str], optional) — URL path prefixes to exclude.
        Example: ["/changelog", "/blog"].
      no_render (bool, false) — Skip JS rendering (faster, HTML-only).

    Returns:
      {"ok": true, "data": {"job_id": str, "status": "running"}, "meta": {}}

    Default behaviour:
      Starts crawl with browser rendering, returns job_id immediately.
      Crawl jobs persist for 14 days on Cloudflare.

    Limitations:
      - Async — does not block waiting for completion
      - Requires CF auth (browser rendering)
      - Large crawls consume significant browser quota

    When to use vs alternatives:
      crawl_raw — blocking crawl with --wait, full flag set
      spider_raw — direct HTTP (no browser) for high-volume crawls
    """
    args = ["crawl", url, "--json"]
    if include_paths:
        args.extend(["--include-paths", ",".join(include_paths)])
    if exclude_paths:
        args.extend(["--exclude-paths", ",".join(exclude_paths)])
    if no_render:
        args.append("--no-render")

    return run_cli(args, tool_name="crawl_start", max_chars=None)


def crawl_status_handler(job_id: str) -> dict[str, Any]:
    """Check the status of a running crawl job.

    Poll a crawl job for progress: completed/running/failed, page counts.

    Use this for: checking if a crawl is done before fetching results.
    Poll until status is "completed" or "failed".

    Parameters:
      job_id (str, required) — The job ID returned by crawl_start.

    Returns:
      {"ok": true, "data": {"job_id": str, "status": str, "finished": int,
        "total": int, "browser_seconds": float}, "meta": {}}

    Default behaviour:
      Returns current status snapshot. Poll every 5-30s for long crawls.

    Limitations:
      - Job IDs expire after 14 days
      - Status "running" means crawl is still active

    When to use vs alternatives:
      crawl_results — fetch page content (after status=completed)
      crawl_start — start a new crawl
    """
    args = ["crawl", job_id, "--status", "--json"]
    return run_cli(args, tool_name="crawl_status", max_chars=None)


def crawl_results_handler(
    job_id: str,
    fields: str = "url,markdown",
    limit: int = 20,
    offset: int = 0,
    max_chars: int = 60000,
) -> dict[str, Any]:
    """Fetch paginated results from a completed crawl job.

    Retrieve crawled page content from a completed job, with field selection
    and pagination.

    Use this for: reading crawled content after crawl_status shows "completed".
    Paginate with offset for large crawls.

    Parameters:
      job_id (str, required) — The job ID from crawl_start.
      fields (str, "url,markdown") — Comma-separated field list.
        Options: url, markdown, html, links, screenshot, metadata.
      limit (int, 20) — Pages per page (pagination).
      offset (int, 0) — Page offset for pagination.
      max_chars (int, 60000) — Total content truncation limit.

    Returns:
      {"ok": true, "data": {"job_id": str, "records": [...], ...},
        "meta": {"count": int, "total": int, "truncated": bool, "_next_page": {}}}

    Default behaviour:
      Returns first 20 results with URL and markdown.

    Limitations:
      - max_chars applies across all records in the response
      - Paginate with offset for crawls > 20 pages
      - Screenshots return paths, not image data

    When to use vs alternatives:
      crawl_raw with --ndjson — streaming all results without pagination
    """
    args = ["crawl", job_id, "--json", f"--fields={fields}"]

    result = run_cli(args, tool_name="crawl_results", max_chars=max_chars)

    # Inject pagination meta
    if result.get("ok"):
        data = result.get("data", {})
        records = []
        if isinstance(data, dict):
            records = data.get("records", data.get("data", []))
        elif isinstance(data, list):
            records = data

        # Apply pagination
        paginated = records[offset:offset + limit]
        total = len(records)

        result["data"] = {"job_id": job_id, "records": paginated}
        result.setdefault("meta", {}).update({
            "count": len(paginated),
            "total": total,
            "offset": offset,
            "limit": limit,
        })
        if offset + limit < total:
            result["meta"]["_next_page"] = {"offset": offset + limit, "limit": limit}
            result["meta"]["truncated"] = True

    return result


def site_download_handler(
    url: str,
    limit: int = 50,
    output_format: str = "markdown",
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Download an entire site as files. Returns a file manifest.

    Crawl and download a site's pages as local files (markdown or HTML).

    Use this for: creating offline site archives, bulk content extraction,
    documentation backup.

    Parameters:
      url (str, required) — The root URL to download.
      limit (int, 50) — Maximum pages to download.
      output_format (str, "markdown") — Output format: "markdown" or "html".
      output_dir (str, optional) — Directory to save files. Defaults to
        temp dir.

    Returns:
      {"ok": true, "data": {"output_dir": str, "files": [str], "count": int},
        "meta": {}}

    Default behaviour:
      Downloads to flarecrawl-mcp temp dir as markdown files.

    Limitations:
      - Not available in read-only mode
      - Large sites consume significant browser quota
      - Binary assets (images, PDFs) are not downloaded

    When to use vs alternatives:
      crawl_results — content in memory without disk I/O
      crawl_start/status/results — async pipeline for large sites
    """
    import tempfile
    from pathlib import Path

    if not output_dir:
        base = Path(tempfile.gettempdir()) / "flarecrawl-mcp" / "downloads"
        base.mkdir(parents=True, exist_ok=True)
        output_dir = str(base)

    args = ["download", url, "--json", f"--limit={limit}", f"--format={output_format}",
            f"--output={output_dir}"]

    return run_cli(args, tool_name="site_download", max_chars=None)


def session_list_handler() -> dict[str, Any]:
    """List saved browser sessions (cookie jars).

    Return all named browser sessions stored by flarecrawl, with metadata.

    Use this for: checking what sessions are available before a P6 mint/replay
    workflow, auditing stored credentials.

    Parameters:
      (none)

    Returns:
      {"ok": true, "data": [{"name": str, "path": str, "modified": str}],
        "meta": {"count": int}}

    Default behaviour:
      Lists all sessions in the flarecrawl session store.

    Limitations:
      - Sessions may be stale — use session_inspect to check freshness

    When to use vs alternatives:
      session_inspect — check a specific session's freshness
      p6_raw — use a session for mint→replay
    """
    return run_cli(["session", "list", "--json"], tool_name="session_list", max_chars=None)


def session_inspect_handler(name_or_path: str) -> dict[str, Any]:
    """Inspect a saved session's cookie freshness and validity.

    Return a freshness verdict (fresh/stale/expired) for a named session,
    without making network requests.

    Use this for: verifying a session is still valid before using it in p6_raw
    or scrape_raw with --session.

    Parameters:
      name_or_path (str, required) — Session name (e.g. "mysite") or path.
        Prefix with @ for named sessions: "@mysite".

    Returns:
      {"ok": true, "data": {"name": str, "verdict": "fresh|stale|expired",
        "cookie_count": int, "domains": [str], "oldest_cookie_days": float},
        "meta": {}}

    Default behaviour:
      Checks cookie expiry and age offline (no network call).

    Limitations:
      - Offline check only — does not verify session against live site
      - Exit code != 0 unless session is fresh

    When to use vs alternatives:
      session_list — list all sessions
      p6_raw — use session for hard-target bypass
    """
    # Ensure @ prefix for named sessions
    if not name_or_path.startswith("@") and not name_or_path.startswith("/") and not name_or_path.startswith("."):
        name_or_path = f"@{name_or_path}"

    return run_cli(
        ["session", "inspect", name_or_path, "--json"],
        tool_name="session_inspect",
        max_chars=None,
    )
