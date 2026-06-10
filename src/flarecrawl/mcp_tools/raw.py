"""T3 Raw tool handlers for the flarecrawl MCP surface.

9 tools: scrape_raw, fetch_raw, crawl_raw, extract_raw, tech_detect_raw,
spider_raw, p6_raw, recipe_run_raw, design_extract_raw.

T3 tools accept the core args explicitly plus ``options: dict`` for any
remaining CLI flag (e.g. ``{"wait_until": "networkidle2"}``).  Agent-safe is
OFF by default (CLI parity).  No max_chars cap by default.  T3 returns the CLI
envelope verbatim.

All handlers are pure functions returning dicts.  No ``mcp`` package import.
"""

from __future__ import annotations

from typing import Any

from ._exec import _options_to_flags, run_cli

# ---------------------------------------------------------------------------
# T3 Raw handlers
# ---------------------------------------------------------------------------


def scrape_raw_handler(
    url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scrape a URL with full CLI flag access (~50 flags). Use for full API fidelity.

    Pass all flarecrawl scrape flags via the options dict. Keys use underscore
    (converted to --hyphen-flags automatically). Boolean True = bare flag,
    list = repeated flag.

    Use this for: hard targets requiring TLS impersonation, CDP sessions,
    HAR capture, JS eval, capture-pattern XHR download, paywall bypass with
    custom settings. Use read_page for standard HTML reading.

    Parameters:
      url (str, required) — The URL to scrape.
      options (dict, optional) — Additional CLI flags as a dict.
        Keys: any scrape flag without '--' prefix, underscores OK.
        Examples: {"stealth": True, "js": True, "wait_until": "networkidle2",
          "capture_pattern": "*.csv,*.json", "agent_safe": True,
          "format": "html", "timeout": 60}.

    Returns:
      CLI envelope verbatim: {"data": {"url": str, "content": str, ...},
        "meta": {...}}

    Default behaviour:
      Runs with --json. agent_safe is OFF (CLI default). No max_chars cap.

    Limitations:
      - --interactive/--live-view/--headed require a human at a browser (gap 10)
      - Full flag list: flarecrawl scrape --help

    When to use vs alternatives:
      read_page — standard HTML reading with automatic routing
    """
    explicit_keys = {"url"}
    args = ["scrape", url, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="scrape_raw", max_chars=None, inject_agent_safe=False)


def fetch_raw_handler(
    url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch a URL with full CLI flag access. Use for full API fidelity.

    Pass all flarecrawl fetch flags via the options dict.

    Use this for: TLS impersonation downloads, session-authenticated fetches,
    explicit content-type handling. Use fetch_url for standard fetching.

    Parameters:
      url (str, required) — The URL to fetch.
      options (dict, optional) — Additional CLI flags.
        Examples: {"stealth": True, "session": "@mysite",
          "output": "/path/to/file", "agent_safe": True}.

    Returns:
      CLI envelope verbatim: {"data": {...}, "meta": {}}

    Default behaviour:
      Runs with --json. agent_safe is OFF (CLI default). No max_chars cap.

    Limitations:
      - Binary output requires --output flag in options

    When to use vs alternatives:
      fetch_url — standard fetching with sensible defaults
    """
    explicit_keys = {"url"}
    args = ["fetch", url, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="fetch_raw", max_chars=None, inject_agent_safe=False)


def crawl_raw_handler(
    url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crawl a site with full CLI flag access including --wait blocking mode. Use for full API fidelity.

    Pass all flarecrawl crawl flags via the options dict. Supports blocking
    mode via ``{"wait": True}`` for short crawls.

    Use this for: crawls requiring custom path filtering, webhook delivery,
    deduplication, agent-safe content, or blocking wait mode. Use
    crawl_start/status/results for the standard async pattern.

    Parameters:
      url (str, required) — The root URL to crawl.
      options (dict, optional) — Additional CLI flags.
        Examples: {"wait": True, "limit": 100, "include_paths": "/docs",
          "no_render": True, "ndjson": True, "deduplicate": True,
          "agent_safe": True, "webhook": "https://hooks.example.com"}.

    Returns:
      CLI envelope verbatim. With wait=True: blocking result with records.
      Without wait: {"data": {"job_id": str, "status": "running"}}

    Default behaviour:
      Fire-and-forget. agent_safe is OFF (CLI default). No max_chars cap.

    Limitations:
      - With wait=True, may block for minutes on large crawls
      - Full flag list: flarecrawl crawl --help

    When to use vs alternatives:
      crawl_start — standard async crawl
    """
    explicit_keys = {"url"}
    args = ["crawl", url, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="crawl_raw", max_chars=None, inject_agent_safe=False)


def extract_raw_handler(
    prompt: str,
    urls: list[str] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run AI data extraction with full CLI flag access. Use for full API fidelity.

    Pass all flarecrawl extract flags via the options dict.

    Use this for: batch extraction with schema files, custom Workers AI models,
    stdin HTML input. Use extract_data for standard extraction.

    Parameters:
      prompt (str, required) — Natural-language extraction instruction.
      urls (list[str], optional) — URLs to extract from. Or pass in options
        as batch_file for file-based URL lists.
      options (dict, optional) — Additional CLI flags.
        Examples: {"schema_file": "schema.json", "batch": "urls.txt",
          "workers": 5, "agent_safe": True}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      agent_safe is OFF (CLI default). No max_chars cap.

    Limitations:
      - Full flag list: flarecrawl extract --help

    When to use vs alternatives:
      extract_data — standard extraction with agent-safe default
    """
    explicit_keys = {"prompt", "urls"}
    args = ["extract", prompt, "--json"]
    if urls:
        for url in urls:
            args.extend(["--urls", url])
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="extract_raw", max_chars=None, inject_agent_safe=False)


def tech_detect_raw_handler(
    url: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect technologies with full CLI flag access. Use for full API fidelity.

    Pass all flarecrawl tech-detect flags via the options dict. Supports
    stdin HTML input, Playwright rendering, custom confidence thresholds.

    Use this for: stdin HTML detection, Playwright-rendered detection,
    batch detection from file. Use tech_detect for standard detection.

    Parameters:
      url (str, optional) — The URL. Omit for --stdin HTML input.
      options (dict, optional) — Additional CLI flags.
        Examples: {"stdin": True, "render": True, "min_confidence": 50,
          "only_categories": "CMS,Frameworks", "workers": 10,
          "input": "urls.txt"}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      agent_safe is OFF (CLI default). All categories included (no filter).

    Limitations:
      - Full flag list: flarecrawl tech-detect --help

    When to use vs alternatives:
      tech_detect — standard detection with noise filtering
    """
    explicit_keys = {"url"}
    args = ["tech-detect", "--json"]
    if url:
        args.insert(1, url)
        args[1], args[0] = args[0], args[1]
        args = ["tech-detect", url, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="tech_detect_raw", max_chars=None, inject_agent_safe=False)


def spider_raw_handler(
    url: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Direct-HTTP high-volume spider with full CLI flag access. Use for full API fidelity.

    Pass all flarecrawl spider flags. Uses direct HTTP (no browser rendering)
    for high-volume crawling with adaptive delay and resume support.

    Use this for: high-volume spidering (500-10000 pages), resumable jobs,
    cookie-authenticated crawls, adaptive per-host rate limiting.

    Parameters:
      url (str, required) — The root URL.
      options (dict, optional) — Additional CLI flags.
        Examples: {"limit": 1000, "workers": 10, "rate_limit": 5,
          "adaptive_delay": True, "resume": "JOB_ID",
          "cookies": "session.json"}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      No browser time consumed. agent_safe is OFF (CLI default).

    Limitations:
      - No JS rendering — static HTML only
      - Not available in read-only mode
      - Full flag list: flarecrawl spider --help

    When to use vs alternatives:
      crawl_start — browser-rendered crawl (slower, higher quality)
    """
    explicit_keys = {"url"}
    args = ["spider", url, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="spider_raw", max_chars=None, inject_agent_safe=False)


def p6_raw_handler(
    mint_url: str,
    targets: list[str] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mint a session token and replay against targets (P6 protocol). Use for full API fidelity.

    Pass all flarecrawl p6 flags. Mints a fresh browser session (cookie jar)
    against the mint URL, then replays it against target URLs.

    Use this for: hard targets protected by Akamai/Cloudflare/DataDome that
    block direct browser access. Mint once, replay many times.

    Parameters:
      mint_url (str, required) — URL to mint the session token against.
      targets (list[str], optional) — Target URLs for replay.
      options (dict, optional) — Additional CLI flags.
        Examples: {"jar": "./jar.json", "targets_from": "targets.txt",
          "output_dir": "./out", "cool_down": 5,
          "max_retries": 3, "stealth": True}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      agent_safe is OFF (CLI default). Not available in read-only mode.

    Limitations:
      - Requires CF auth
      - --headed flag requires local Playwright (gap 10)
      - Not available in read-only mode
      - Full flag list: flarecrawl p6 --help

    When to use vs alternatives:
      scrape_raw with stealth=True — simpler stealth without session replay
    """
    explicit_keys = {"mint_url", "targets"}
    args = ["p6", mint_url, "--json"]
    if targets:
        for t in targets:
            args.extend(["--target", t])
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="p6_raw", max_chars=None, inject_agent_safe=False)


def recipe_run_raw_handler(
    recipe_file: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a YAML recipe (headless steps only). Use for full API fidelity.

    Execute a flarecrawl YAML recipe file. Headless-compatible steps only —
    headed/interactive steps are a declared gap (require human at browser).

    Use this for: complex multi-step workflows defined in YAML, parameterised
    scraping pipelines, dry-run validation.

    Parameters:
      recipe_file (str, required) — Path to the YAML recipe file.
      options (dict, optional) — Additional CLI flags.
        Examples: {"dry_run": True, "resume": True,
          "params": "param1=val1"}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      agent_safe is OFF (CLI default). Not available in read-only mode.

    Limitations:
      - Headed/interactive steps require a human at a browser (see gap 10)
      - Requires pyyaml installed (see permissions_check)
      - Not available in read-only mode
      - Full flag list: flarecrawl recipe --help

    When to use vs alternatives:
      Multiple sequential MCP tool calls — if no recipe file exists
    """
    explicit_keys = {"recipe_file"}
    args = ["recipe", recipe_file, "--json"]
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="recipe_run_raw", max_chars=None, inject_agent_safe=False)


def design_extract_raw_handler(
    url: str,
    mode: str = "extract",
    url2: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract design system, run coherence analysis, or diff two sites. Use for full API fidelity.

    Run flarecrawl design operations: extract (design system extraction),
    coherence (score), or diff (compare two sites).

    Use this for: design system extraction, branding analysis, comparing
    visual consistency between two sites.

    Parameters:
      url (str, required) — The site URL.
      mode (str, "extract") — Operation mode: "extract", "coherence", "diff".
      url2 (str, optional) — Second URL for diff mode.
      options (dict, optional) — Additional CLI flags.
        Examples: {"full": True, "preview": True,
          "output": "DESIGN.md"}.

    Returns:
      CLI envelope verbatim.

    Default behaviour:
      extract mode: returns design system as markdown.
      coherence mode: returns a JSON coherence score.
      diff mode: returns a JSON diff of two sites.

    Limitations:
      - Full flag list: flarecrawl design --help

    When to use vs alternatives:
      tech_detect — technology stack without design analysis
    """
    valid_modes = ("extract", "coherence", "diff")
    if mode not in valid_modes:
        from ._errors import validation_error
        return validation_error(
            f"mode must be one of {valid_modes}, got '{mode}'",
            tool_name="design_extract_raw",
        )

    explicit_keys = {"url", "mode", "url2"}
    args = ["design", mode, url, "--json"]
    if url2 and mode == "diff":
        args.append(url2)
    if options:
        args.extend(_options_to_flags(options, explicit_keys))

    return run_cli(args, tool_name="design_extract_raw", max_chars=None, inject_agent_safe=False)
