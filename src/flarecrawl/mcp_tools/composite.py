"""T1 Composite tool handlers for the flarecrawl MCP surface.

Five composites: read_page, research_web, site_overview, extract_data,
check_page_changes.

All handlers are pure functions returning dicts.  No ``mcp`` package import.
Agent-safe is ON by default for all T1 tools (overridable).
"""

from __future__ import annotations

from typing import Any

from ._exec import _DEFAULT_MAX_CHARS, run_cli

# ---------------------------------------------------------------------------
# T1 Composite handlers
# ---------------------------------------------------------------------------


def read_page_handler(
    url: str,
    js: bool = False,
    max_chars: int = _DEFAULT_MAX_CHARS,
    fresh: bool = False,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Read any URL as clean markdown with automatic routing and paywall retry.

    Read a URL as clean markdown. Auto-routes: content negotiation (zero
    browser time) → browser render → paywall cascade on extraction failure,
    reporting the winning strategy in meta.source.

    Use this for: reading articles, documentation, any public web page. Prefer
    research_web when you have a search query rather than a known URL. Prefer
    fetch_url for binary downloads or raw content types (JSON/XML/CSV).

    Parameters:
      url (str, required) — The URL to read.
      js (bool, false) — Force JavaScript rendering. Default: negotiate first.
      max_chars (int, 40000) — Truncate content to this many characters.
      fresh (bool, false) — Bypass response cache.
      agent_safe (bool, true) — Apply agent-safe sanitisation (removes ads,
        cookie banners, nav — optimises for LLM consumption).

    Returns:
      {"ok": true, "data": {"url": str, "content": str, "title": str,
        "elapsed": float, ...}, "meta": {"source": "negotiate|browser|paywall",
        "truncated": bool, "chars_total": int}}

    Default behaviour:
      Tries content negotiation first (no browser time), falls back to browser
      render, then paywall cascade on extraction failure.

    Limitations:
      - Hard CF 1020 blocks are non-bypassable — meta.blocked.terminal=true
      - Paywall retry adds latency (~3-5s extra)
      - max_chars truncates at line boundary

    When to use vs alternatives:
      research_web — search + read in one call
      fetch_url — raw content types, binary downloads
      scrape_raw — full flag fidelity (50+ flags)
    """
    args = ["scrape", url, "--json"]
    if js:
        args.append("--js")
    if fresh:
        args.append("--no-cache")
    if not agent_safe:
        pass  # agent-safe is injected by run_cli via inject_agent_safe
    else:
        args.append("--agent-safe")

    result = run_cli(
        args,
        tool_name="read_page",
        max_chars=max_chars,
        inject_agent_safe=False,  # already added above
    )

    if result.get("ok") is False:
        # On blocked/empty: retry once with --paywall
        err_code = result.get("error", {}).get("code", "")
        if err_code == "BLOCKED" or (not result.get("data", {}).get("content")):
            paywall_args = ["scrape", url, "--json", "--paywall"]
            if agent_safe:
                paywall_args.append("--agent-safe")
            retry = run_cli(
                paywall_args,
                tool_name="read_page",
                max_chars=max_chars,
                inject_agent_safe=False,
            )
            if retry.get("ok"):
                retry.setdefault("meta", {})["source"] = "paywall"
                return retry
        return result

    result.setdefault("meta", {})["source"] = "browser" if js else "negotiate"
    return result


def research_web_handler(
    query: str,
    top_n: int = 5,
    scrape: bool = True,
    max_chars_per_result: int = 15000,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Search the web and read the top N results in one call.

    Search the web for a query and optionally scrape + digest the top results,
    returning per-result markdown digests in a single response.

    Use this for: research tasks, fact-finding, digesting news or documentation
    from a search query. Prefer web_search when you only need URLs/snippets.

    Parameters:
      query (str, required) — The search query.
      top_n (int, 5) — Number of results to return/scrape (max 10).
      scrape (bool, true) — Scrape and return markdown content for each result.
      max_chars_per_result (int, 15000) — Per-result content truncation.
      agent_safe (bool, true) — Apply agent-safe sanitisation to scraped content.

    Returns:
      {"ok": true, "data": [{"url": str, "title": str, "snippet": str,
        "content": str, ...}], "meta": {"query": str, "count": int}}

    Default behaviour:
      Searches with Jina/DuckDuckGo, scrapes top 5 results, returns digests.

    Limitations:
      - Scraping each result adds latency (parallel, but still ~2-5s total)
      - Paywalled results may return partial content
      - JINA_API_KEY improves search quality but is optional

    When to use vs alternatives:
      web_search — search only, no scraping (faster)
      read_page — single known URL
      extract_data — structured data extraction from known URLs
    """
    args = ["search", query, "--json", f"--limit={top_n}"]
    if scrape:
        args.append("--scrape")

    result = run_cli(
        args,
        tool_name="research_web",
        max_chars=None,  # we apply per-result truncation below
        inject_agent_safe=agent_safe,
    )

    if result.get("ok") is False:
        return result

    # Apply per-result truncation
    data = result.get("data", [])
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for field in ("content", "markdown"):
                    if field in item and isinstance(item[field], str):
                        if len(item[field]) > max_chars_per_result:
                            item[field] = item[field][:max_chars_per_result]
                        break

    result.setdefault("meta", {})["query"] = query
    return result


def site_overview_handler(
    url: str,
    include: list[str] | None = None,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Profile a site: tech stack, structured data, links, favicon, and API specs.

    Run multiple probes against a URL and aggregate into a single site profile:
    technology detection, structured data (LD+JSON/OG), link map, favicon,
    and OpenAPI spec discovery.

    Use this for: site intelligence tasks, profiling a company's web presence,
    checking what tech a site uses, discovering API endpoints.

    Parameters:
      url (str, required) — The site URL to profile.
      include (list, ["tech","schema","links","favicon","openapi"]) — Which
        sections to include. Omit to get all sections.
      agent_safe (bool, true) — Apply agent-safe sanitisation where applicable.

    Returns:
      {"ok": true, "data": {"tech": {...}, "schema": {...}, "links": {...},
        "favicon": {...}, "openapi": {...}}, "meta": {...},
        "_errors": [...]}  # partial failures accumulate here

    Default behaviour:
      Runs all 5 probes in sequence. Each section is independently
      try/except — surviving sections returned even if some fail.

    Limitations:
      - Runs 2-5 CLI calls sequentially; slower than single-URL tools
      - Tech detection confidence varies by site
      - OpenAPI probe may return false negatives (spec at non-standard path)

    When to use vs alternatives:
      tech_detect — technology detection only
      page_schema — structured data only
      page_links — link discovery only
      openapi_discover — OpenAPI discovery only
    """
    sections_to_include = set(include or ["tech", "schema", "links", "favicon", "openapi"])
    sections: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []

    if "tech" in sections_to_include:
        try:
            r = run_cli(
                ["tech-detect", url, "--json", "--exclude-categories",
                 "Miscellaneous,Security,Tag managers,RUM"],
                tool_name="site_overview/tech",
                max_chars=None,
            )
            sections["tech"] = r.get("data") if r.get("ok") else None
            if not r.get("ok"):
                errors.append({"section": "tech", "error": r.get("error", {})})
        except Exception as exc:  # noqa: BLE001
            sections["tech"] = None
            errors.append({"section": "tech", "error": str(exc)})

    if "schema" in sections_to_include:
        try:
            r = run_cli(["schema", url, "--json"], tool_name="site_overview/schema", max_chars=None)
            sections["schema"] = r.get("data") if r.get("ok") else None
            if not r.get("ok"):
                errors.append({"section": "schema", "error": r.get("error", {})})
        except Exception as exc:  # noqa: BLE001
            sections["schema"] = None
            errors.append({"section": "schema", "error": str(exc)})

    if "links" in sections_to_include:
        try:
            r = run_cli(["map", url, "--json"], tool_name="site_overview/links", max_chars=None)
            sections["links"] = r.get("data") if r.get("ok") else None
            if not r.get("ok"):
                errors.append({"section": "links", "error": r.get("error", {})})
        except Exception as exc:  # noqa: BLE001
            sections["links"] = None
            errors.append({"section": "links", "error": str(exc)})

    if "favicon" in sections_to_include:
        try:
            r = run_cli(["favicon", url, "--json"], tool_name="site_overview/favicon", max_chars=None)
            sections["favicon"] = r.get("data") if r.get("ok") else None
            if not r.get("ok"):
                errors.append({"section": "favicon", "error": r.get("error", {})})
        except Exception as exc:  # noqa: BLE001
            sections["favicon"] = None
            errors.append({"section": "favicon", "error": str(exc)})

    if "openapi" in sections_to_include:
        try:
            r = run_cli(
                ["openapi", url, "--probe", "--json"],
                tool_name="site_overview/openapi",
                max_chars=None,
            )
            sections["openapi"] = r.get("data") if r.get("ok") else None
            if not r.get("ok"):
                errors.append({"section": "openapi", "error": r.get("error", {})})
        except Exception as exc:  # noqa: BLE001
            sections["openapi"] = None
            errors.append({"section": "openapi", "error": str(exc)})

    result: dict[str, Any] = {
        "ok": True,
        "data": sections,
        "meta": {"url": url, "sections_requested": list(sections_to_include)},
    }
    if errors:
        result["_errors"] = errors
    return result


def extract_data_handler(
    urls: list[str],
    prompt: str,
    json_schema: dict[str, Any] | None = None,
    max_urls: int = 10,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Extract structured data from URLs with an AI prompt or JSON schema.

    Run AI-powered data extraction against one or more URLs, returning
    structured results per URL.

    Use this for: pulling product information, prices, tables, article
    metadata, or any structured data from web pages with a natural-language
    extraction prompt.

    Parameters:
      urls (list[str], required) — URLs to extract from (max 10).
      prompt (str, required) — Natural-language extraction instruction.
        Example: "Extract product names, prices, and availability".
      json_schema (dict, optional) — JSON Schema to guide extraction shape.
      max_urls (int, 10) — Hard cap on number of URLs processed.
      agent_safe (bool, true) — Apply agent-safe sanitisation to content.

    Returns:
      {"ok": true, "data": [{"url": str, "extracted": {...}, "status": str}],
        "meta": {"count": int, "prompt": str}}

    Default behaviour:
      Extracts from all provided URLs sequentially (up to max_urls).

    Limitations:
      - Requires CF auth (uses Workers AI)
      - Quality depends on page content and prompt specificity
      - max_urls cap to prevent accidental large bills

    When to use vs alternatives:
      page_schema — LD+JSON/OG metadata extraction (no AI, free)
      scrape_raw — full page content without extraction
      research_web — search + read pipeline
    """
    capped_urls = urls[:max_urls]
    results = []
    errors = []

    for url in capped_urls:
        args = ["extract", prompt, "--urls", url, "--json"]
        if agent_safe:
            args.append("--agent-safe")

        r = run_cli(args, tool_name="extract_data", max_chars=None)
        if r.get("ok"):
            data = r.get("data", {})
            results.append({"url": url, "extracted": data, "status": "ok"})
        else:
            results.append({
                "url": url,
                "extracted": None,
                "status": "error",
                "error": r.get("error", {}),
            })
            errors.append({"url": url, "error": r.get("error", {})})

    result: dict[str, Any] = {
        "ok": True,
        "data": results,
        "meta": {
            "count": len(results),
            "prompt": prompt,
            "urls_requested": len(urls),
            "urls_processed": len(capped_urls),
        },
    }
    if errors:
        result["_errors"] = errors
    return result


def check_page_changes_handler(
    url: str,
    max_chars: int = 10000,
    agent_safe: bool = True,
) -> dict[str, Any]:
    """Check whether a page changed since it was last cached.

    Scrape a URL and diff it against the cached version, returning a
    changed/unchanged verdict plus a diff summary.

    Use this for: monitoring pages for content changes, detecting when
    pricing/availability updates, periodic change detection.

    Parameters:
      url (str, required) — The URL to check.
      max_chars (int, 10000) — Truncate diff output to this many characters.
      agent_safe (bool, true) — Apply agent-safe sanitisation.

    Returns:
      {"ok": true, "data": {"changed": bool, "url": str, "diff": str,
        "summary": str}, "meta": {}}

    Default behaviour:
      Scrapes with --diff flag; compares against response cache. Returns
      changed=false if no cache entry exists (first-time check).

    Limitations:
      - Requires prior scrape in response cache to diff against
      - Diff is line-based; formatting changes may produce false positives
      - Dynamic content (timestamps, ad IDs) may always show as changed

    When to use vs alternatives:
      read_page — read current content without diffing
      scrape_raw — full diff options (custom diff algorithms)
    """
    args = ["scrape", url, "--json", "--diff"]
    if agent_safe:
        args.append("--agent-safe")

    result = run_cli(
        args,
        tool_name="check_page_changes",
        max_chars=max_chars,
    )

    if result.get("ok") is False:
        return result

    # Normalise the diff envelope shape
    data = result.get("data", {})
    if isinstance(data, dict):
        changed = data.get("changed", data.get("has_diff", False))
        result.setdefault("data", {})["changed"] = changed

    return result
