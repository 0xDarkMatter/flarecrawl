"""discover, schema, usage, openapi commands."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time as _time
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from .. import __version__
from ..batch import parse_batch_file, process_batch
from ..client import MOBILE_PRESET, Client, FlareCrawlError
from ..config import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_WORKERS,
    clear_cdp_session,
    clear_credentials,
    get_account_id,
    get_api_token,
    get_auth_status,
    get_usage,
    list_cdp_sessions,
    load_cdp_session,
    save_cdp_session,
    save_credentials,
)
from ._common import (
    EXIT_AUTH_REQUIRED,
    EXIT_ERROR,
    EXIT_FORBIDDEN,
    EXIT_NOT_FOUND,
    EXIT_RATE_LIMITED,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
    _apply_browser_cookies,
    _apply_tech_detection,
    _attach_tech,
    _classify_url_for_organize,
    _collect_response_signals,
    _enrich_cdp_error,
    _error,
    _filter_detections,
    _filter_fields,
    _filter_record_content,
    _get_cdp_client,
    _get_client,
    _handle_api_error,
    _output_json,
    _output_ndjson,
    _output_text,
    _parse_auth,
    _parse_body,
    _parse_category_list,
    _parse_headers,
    _require_auth,
    _run_then_fetch,
    _sanitize_filename,
    _validate_url,
    console,
)


# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command()
def discover(
    url: Annotated[str, typer.Argument(help="Base URL to discover content from")],
    sitemap: Annotated[bool, typer.Option("--sitemap", help="Check XML sitemaps")] = True,
    feed: Annotated[bool, typer.Option("--feed", help="Check RSS/Atom feeds")] = True,
    links: Annotated[bool, typer.Option("--links", help="Discover page links")] = True,
    limit: Annotated[int | None, typer.Option("--limit", help="Max URLs to return")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    openapi_flag: Annotated[bool, typer.Option("--openapi", help="Also discover OpenAPI/Swagger specs")] = False,
):
    """Discover all URLs on a site via sitemaps, RSS feeds, and page links.

    Combines XML sitemap parsing, RSS/Atom feed discovery, and page link
    extraction into a single unified URL list. Use --openapi to also
    probe for API specs.

    Example:
        flarecrawl discover https://example.com --json
        flarecrawl discover https://example.com --sitemap --no-feed --no-links
        flarecrawl discover https://example.com --limit 100
        flarecrawl discover https://example.com --openapi --json
    """
    from urllib.parse import urljoin, urlparse

    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output, cache_ttl=cache_ttl)
    _validate_url(url, json_output)
    auth_dict = _parse_auth(auth, json_output)
    custom_headers = _parse_headers(headers, json_output)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    discovered: dict[str, str] = {}  # url -> source

    kwargs = {}
    kwargs["reject_resources"] = ["image", "media", "font", "stylesheet"]
    if auth_dict:
        kwargs.update(auth_dict)
    if user_agent:
        kwargs["user_agent"] = user_agent

    def _extract_locs_from_xml(html_or_xml: str) -> tuple[list[str], list[str]]:
        """Extract <loc> URLs from sitemap/feed XML (may be wrapped in HTML by CF).

        Returns (page_urls, sub_sitemap_urls).
        """
        from selectolax.parser import HTMLParser
        # CF renders XML as HTML â€” use selectolax to extract text of <loc> tags
        tree = HTMLParser(html_or_xml)
        pages, sub_sitemaps = [], []
        for loc in tree.css("loc"):
            text = loc.text(strip=True)
            if not text or not text.startswith("http"):
                continue
            if text.endswith(".xml") or "sitemap" in text.lower():
                sub_sitemaps.append(text)
            else:
                pages.append(text)
        return pages, sub_sitemaps

    # 1. XML Sitemap
    if sitemap:
        console.print("[dim]Checking sitemaps...[/dim]")
        sitemap_queue = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
        visited_sitemaps: set[str] = set()

        # Check robots.txt for sitemap directives
        try:
            robots_html = client.get_content(f"{base}/robots.txt", **kwargs)
            for line in robots_html.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("sitemap:"):
                    sm_url = stripped.split(":", 1)[1].strip()
                    # robots.txt rendered by CF may have extra "Sitemap" prefix
                    if sm_url.startswith("http") and sm_url not in sitemap_queue:
                        sitemap_queue.append(sm_url)
        except FlareCrawlError:
            pass

        # Process sitemap queue (handles sitemap indexes recursively)
        while sitemap_queue:
            sm_url = sitemap_queue.pop(0)
            if sm_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sm_url)
            try:
                sm_html = client.get_content(sm_url, **kwargs)
                pages, sub_sitemaps = _extract_locs_from_xml(sm_html)
                for page_url in pages:
                    discovered[page_url] = "sitemap"
                # Queue sub-sitemaps for recursive processing (limit depth)
                if len(visited_sitemaps) < 20:
                    for sub in sub_sitemaps:
                        if sub not in visited_sitemaps:
                            sitemap_queue.append(sub)
            except FlareCrawlError:
                pass
        console.print(f"[dim]Sitemaps: {sum(1 for v in discovered.values() if v == 'sitemap')} URLs[/dim]")

    # 2. RSS/Atom feeds
    if feed:
        console.print("[dim]Checking feeds...[/dim]")
        try:
            html = client.get_content(url, **kwargs)
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            feed_urls = []
            # Find <link> tags with RSS/Atom types
            for link_tag in tree.css("link"):
                link_type = (link_tag.attributes.get("type") or "").lower()
                if "rss" in link_type or "atom" in link_type:
                    href = link_tag.attributes.get("href")
                    if href:
                        feed_urls.append(urljoin(url, href))
            # Also try common feed paths
            for feed_path in ["/feed", "/rss", "/atom.xml", "/feed.xml", "/rss.xml",
                              "/feed/", "/rss/", "/index.xml"]:
                feed_urls.append(f"{base}{feed_path}")

            for feed_url in dict.fromkeys(feed_urls):  # dedupe, preserve order
                try:
                    feed_html = client.get_content(feed_url, **kwargs)
                    # CF renders XML as HTML â€” use selectolax to find link elements
                    feed_tree = HTMLParser(feed_html)
                    # RSS: <item><link>URL</link></item>
                    for item in feed_tree.css("item"):
                        link_el = item.css_first("link")
                        if link_el:
                            href = link_el.text(strip=True)
                            # Fallback: CF/lxml sometimes turns self-closing
                            # <link/> into a sibling text node containing the URL.
                            if not href:
                                nxt = link_el.next
                                if nxt is not None and nxt.tag == "-text":
                                    href = (nxt.text() or "").strip()
                            if href and isinstance(href, str) and href.strip().startswith("http"):
                                discovered.setdefault(href.strip(), "feed")
                    # Atom: <entry><link href="URL"/></entry>
                    for entry in feed_tree.css("entry"):
                        for link_el in entry.css("link"):
                            href = link_el.attributes.get("href")
                            if href and href.startswith("http"):
                                discovered.setdefault(href.strip(), "feed")
                except FlareCrawlError:
                    pass
        except FlareCrawlError:
            pass
        console.print(f"[dim]Feeds: {sum(1 for v in discovered.values() if v == 'feed')} URLs[/dim]")

    # 3. Page links
    if links:
        console.print("[dim]Discovering page links...[/dim]")
        try:
            page_links = client.get_links(url, **kwargs)
            for link in page_links:
                if isinstance(link, str):
                    if not link.startswith("http"):
                        link = urljoin(url, link)
                    discovered.setdefault(link, "links")
        except FlareCrawlError:
            pass
        console.print(f"[dim]Links: {sum(1 for v in discovered.values() if v == 'links')} URLs[/dim]")

    # 4. OpenAPI spec discovery (optional)
    api_specs: list[dict] = []
    if openapi_flag:
        console.print("[dim]Checking for OpenAPI/Swagger specs...[/dim]")
        try:
            from ..openapi import discover_specs, probe_common_paths
            page_html = client.get_content(url, **kwargs)
            for spec in discover_specs(page_html, url):
                api_specs.append({"url": spec.url, "source": spec.source, "format": spec.format})
            for spec in probe_common_paths(url):
                if spec.url not in {s["url"] for s in api_specs}:
                    api_specs.append({"url": spec.url, "source": spec.source, "format": spec.format})
            console.print(f"[dim]API specs: {len(api_specs)} found[/dim]")
        except FlareCrawlError:
            pass

    # Apply limit
    all_urls = list(discovered.items())
    if limit:
        all_urls = all_urls[:limit]

    # Output
    if json_output:
        data = [{"url": u, "source": s} for u, s in all_urls]
        meta = {
            "url": url,
            "total": len(all_urls),
            "by_source": {
                "sitemap": sum(1 for _, s in all_urls if s == "sitemap"),
                "feed": sum(1 for _, s in all_urls if s == "feed"),
                "links": sum(1 for _, s in all_urls if s == "links"),
            },
        }
        if api_specs:
            meta["api_specs"] = api_specs
        _output_json({"data": data, "meta": meta})
    else:
        for u, s in all_urls:
            _output_text(f"{u}  [{s}]")
        if api_specs:
            console.print("\n[bold]API Specs:[/bold]")
            for spec in api_specs:
                console.print(f"  [{spec['source']}] {spec['url']}")
        console.print(f"\n[dim]Total: {len(all_urls)} URLs[/dim]")


# ------------------------------------------------------------------
# schema â€” structured data extraction
# ------------------------------------------------------------------


@_cmd.command()
def schema(
    url: Annotated[str, typer.Argument(help="URL to extract structured data from")],
    type_filter: Annotated[str, typer.Option("--type", help="Filter: ld-json, opengraph, twitter, all")] = "all",
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Timeout in ms")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """Extract structured data (LD+JSON, OpenGraph, Twitter Cards) from a page.

    Parses <script type="application/ld+json">, <meta property="og:*">,
    and <meta name="twitter:*"> tags from the rendered HTML.

    Example:
        flarecrawl schema https://example.com --json
        flarecrawl schema https://example.com --type ld-json --json
        flarecrawl schema https://example.com --type opengraph
    """
    from ..extract import extract_structured_data

    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output, cache_ttl=cache_ttl)
    _validate_url(url, json_output)
    auth_dict = _parse_auth(auth, json_output)
    custom_headers = _parse_headers(headers, json_output)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    try:
        kwargs = {}
        if timeout:
            kwargs["timeout"] = timeout
        kwargs["reject_resources"] = ["image", "media", "font", "stylesheet"]
        if auth_dict:
            kwargs.update(auth_dict)
        if user_agent:
            kwargs["user_agent"] = user_agent
        html = client.get_content(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    data = extract_structured_data(html)

    # Apply type filter
    if type_filter != "all":
        filter_map = {
            "ld-json": "ld_json",
            "opengraph": "opengraph",
            "twitter": "twitter_card",
        }
        key = filter_map.get(type_filter)
        if key:
            data = {key: data[key]}
        else:
            _error(
                f"Invalid --type: {type_filter}. Use: ld-json, opengraph, twitter, all",
                "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
            )

    if json_output:
        _output_json({"data": data, "meta": {"url": url, "type": type_filter}})
    else:
        _output_json(data)


# ------------------------------------------------------------------
# usage â€” browser time tracking
# ------------------------------------------------------------------


@_cmd.command()
def usage(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show browser time usage (tracked locally).

    Tracks the X-Browser-Ms-Used header from each API response.
    Free tier: 600,000ms (10 min) per day.

    Example:
        flarecrawl usage
        flarecrawl usage --json
    """
    from datetime import date

    usage_data = get_usage()
    today = date.today().isoformat()
    today_ms = usage_data.get(today, 0)
    total_ms = sum(usage_data.values())

    daily_limit_ms = 600_000  # 10 minutes free tier
    today_pct = (today_ms / daily_limit_ms * 100) if daily_limit_ms else 0
    cost_estimate = total_ms / 3_600_000 * 0.09  # $0.09/hr

    result = {
        "today_ms": today_ms,
        "today_seconds": round(today_ms / 1000, 1),
        "today_percent_of_free": round(today_pct, 1),
        "total_ms": total_ms,
        "total_seconds": round(total_ms / 1000, 1),
        "estimated_cost": round(cost_estimate, 4),
        "daily_history": usage_data,
    }

    if json_output:
        _output_json({"data": result, "meta": {}})
        return

    console.print(f"[bold]Today[/bold] ({today})")
    console.print(f"  Browser time: [cyan]{today_ms / 1000:.1f}s[/cyan] / 600s free ({today_pct:.1f}%)")

    if today_pct < 50:
        console.print("  Status: [green]well within free tier[/green]")
    elif today_pct < 90:
        console.print("  Status: [yellow]approaching daily limit[/yellow]")
    else:
        console.print("  Status: [red]at/over free tier limit[/red]")

    if len(usage_data) > 1:
        console.print()
        console.print("[bold]History[/bold]")
        table = Table()
        table.add_column("Date")
        table.add_column("Seconds", justify="right")
        table.add_column("% Free", justify="right")
        for day in sorted(usage_data.keys(), reverse=True)[:7]:
            ms = usage_data[day]
            pct = ms / daily_limit_ms * 100
            table.add_row(day, f"{ms / 1000:.1f}", f"{pct:.1f}%")
        console.print(table)

    console.print()
    console.print(f"[dim]Total tracked: {total_ms / 1000:.1f}s | Est. cost: ${cost_estimate:.4f}[/dim]")
    console.print("[dim]Pricing: Free 10 min/day, then $0.09/hr[/dim]")


# ------------------------------------------------------------------
# openapi â€” OpenAPI/Swagger spec discovery
# ------------------------------------------------------------------


@_cmd.command()
def openapi(
    url: Annotated[str, typer.Argument(help="URL to scan for API specs")],
    download: Annotated[bool, typer.Option("--download", "-d", help="Download discovered specs")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output directory for downloads")] = None,
    probe: Annotated[bool, typer.Option("--probe", help="Probe common spec paths (HEAD requests)")] = True,
    session: Annotated[str | None, typer.Option("--session", help="Cookie file or @NAME for saved session")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
):
    """Discover and download OpenAPI/Swagger specs from a URL.

    Scans the page HTML for spec links, checks SwaggerUI configs, and
    optionally probes common spec paths (e.g. /openapi.json, /swagger.json).

    Example:
        flarecrawl openapi https://petstore.swagger.io --json
        flarecrawl openapi https://api.example.com --download -o ./specs
        flarecrawl openapi https://api.example.com --probe --json
    """
    from ..openapi import discover_specs, download_spec, probe_common_paths

    _validate_url(url, json_output)
    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output, cache_ttl=cache_ttl)

    # Load session cookies for HTTP probing
    _cookies = None
    if session:
        if session.startswith("@"):
            from ..config import load_session as _load_session
            try:
                _cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(f"Session not found: {session[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        else:
            from ..cookies import load_cookies
            try:
                _cookies = load_cookies(Path(session))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _error(f"Cannot read session file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    # Fetch page HTML via CF Browser Rendering
    try:
        html = client.get_content(url, reject_resources=["image", "media", "font", "stylesheet"])
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    # Discover specs in HTML
    discovered = discover_specs(html, url)
    if not json_output:
        console.print(f"[dim]Found {len(discovered)} spec link(s) in page HTML[/dim]")

    # Probe common paths
    if probe:
        import httpx as _httpx
        probe_session = None
        if _cookies:
            from ..cookies import cookies_to_httpx
            probe_session = _httpx.Client(
                cookies=cookies_to_httpx(_cookies),
                follow_redirects=True, timeout=10,
            )
        try:
            probed = probe_common_paths(url, session=probe_session)
            if not json_output:
                console.print(f"[dim]Found {len(probed)} spec(s) via path probing[/dim]")
            for p in probed:
                if p.url not in {d.url for d in discovered}:
                    discovered.append(p)
        finally:
            if probe_session:
                probe_session.close()

    if not discovered:
        if json_output:
            _output_json({"data": [], "meta": {"url": url, "total": 0}})
        else:
            console.print("[yellow]No API specs found[/yellow]")
        return

    out_dir = output or Path(".")
    results = []

    for spec in discovered:
        entry: dict = {
            "url": spec.url,
            "source": spec.source,
            "format": spec.format,
            "confidence": spec.confidence,
        }

        if download:
            ext = ".yaml" if spec.format == "yaml" else ".json"
            filename = spec.url.rstrip("/").rsplit("/", 1)[-1]
            if not filename.endswith((".json", ".yaml", ".yml")):
                filename = f"openapi{ext}"
            out_path = out_dir / filename
            try:
                result = download_spec(spec.url, output_path=out_path)
                entry["downloaded"] = str(result.path)
                entry["size"] = result.size
                entry["validation"] = {
                    "valid": result.validation.valid,
                    "version": result.validation.version,
                    "title": result.validation.title,
                    "endpoint_count": result.validation.endpoint_count,
                }
                if not json_output:
                    v = result.validation
                    status = "[green]valid[/green]" if v.valid else "[yellow]invalid[/yellow]"
                    console.print(f"  {status} {spec.url} â†’ {out_path}")
                    if v.title:
                        console.print(f"    Title: {v.title}, Endpoints: {v.endpoint_count}")
            except Exception as e:
                entry["error"] = str(e)
                if not json_output:
                    console.print(f"  [red]Error downloading {spec.url}:[/red] {e}")
        else:
            if not json_output:
                console.print(f"  [{spec.source}] {spec.url} (confidence: {spec.confidence:.0%})")

        results.append(entry)

    if json_output:
        _output_json({"data": results, "meta": {"url": url, "total": len(results)}})


# ------------------------------------------------------------------
# session â€” saved session management
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('discover')(discover)
    app.command('schema')(schema)
    app.command('usage')(usage)
    app.command('openapi')(openapi)
