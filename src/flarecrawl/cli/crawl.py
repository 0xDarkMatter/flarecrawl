"""crawl, map, download commands."""

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
def crawl(
    url_or_job_id: Annotated[str, typer.Argument(help="URL to crawl or job ID to check")],
    wait: Annotated[bool, typer.Option("--wait", help="Wait for completion")] = False,
    poll_interval: Annotated[int, typer.Option("--poll-interval", help="Poll interval in seconds")] = 5,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Timeout in seconds")] = None,
    progress: Annotated[bool, typer.Option("--progress", help="Show progress")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Max pages to crawl")] = None,
    max_depth: Annotated[int | None, typer.Option("--max-depth", help="Max crawl depth")] = None,
    exclude_paths: Annotated[str | None, typer.Option("--exclude-paths", help="Comma-separated exclude patterns")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    include_paths: Annotated[str | None, typer.Option("--include-paths", help="Comma-separated include patterns")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    allow_external: Annotated[bool, typer.Option("--allow-external-links", help="Follow external links")] = False,
    allow_subdomains: Annotated[bool, typer.Option("--allow-subdomains", help="Follow subdomains")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Output format: markdown, html, json")] = "markdown",
    no_render: Annotated[bool, typer.Option("--no-render", help="Skip JS rendering (faster)")] = False,
    source: Annotated[str | None, typer.Option("--source", help="URL source: all, sitemaps, links")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = True,
    ndjson: Annotated[bool, typer.Option("--ndjson", help="Stream one JSON record per line")] = False,
    fields: Annotated[str | None, typer.Option("--fields", help="Comma-separated fields per record")] = None,
    status_check: Annotated[bool, typer.Option("--status", help="Check status of existing job")] = False,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    only_main_content: Annotated[bool, typer.Option("--only-main-content", help="Keep main content only")] = False,
    exclude_tags: Annotated[str | None, typer.Option("--exclude-tags", help="CSS selectors to remove")] = None,
    include_tags: Annotated[str | None, typer.Option("--include-tags", help="CSS selectors to keep")] = None,
    webhook: Annotated[str | None, typer.Option("--webhook", help="POST results to this URL on completion")] = None,
    webhook_headers: Annotated[list[str] | None, typer.Option("--webhook-headers", help="Headers for webhook")] = None,
    deduplicate: Annotated[bool, typer.Option("--deduplicate", help="Skip duplicate content")] = False,
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
    ignore_robots: Annotated[bool, typer.Option("--ignore-robots", help="Ignore robots.txt and AI Crawl Control directives")] = False,
    session: Annotated[str | None, typer.Option("--session", help="Cookie file or @NAME for saved session")] = None,
    tech_detect: Annotated[bool, typer.Option("--tech-detect", help="Wappalyzer tech detection on each crawled record's HTML. Header- and cookie-only fingerprints don't fire here (CF crawl doesn't surface upstream response headers per record).")] = False,
):
    """Crawl a website. Returns JSON by default (like firecrawl).

    Start a new crawl or check status of an existing job.

    Example:
        flarecrawl crawl https://example.com --wait --limit 10
        flarecrawl crawl https://example.com --wait --progress --limit 50
        flarecrawl crawl https://example.com --wait --limit 50 --auth admin:secret
        flarecrawl crawl JOB_ID --status
        flarecrawl crawl JOB_ID --ndjson --fields url,markdown
    """
    client = _get_client(json_output)

    # Parse content filtering
    _inc = [s.strip() for s in include_tags.split(",")] if include_tags else None
    _exc = [s.strip() for s in exclude_tags.split(",")] if exclude_tags else None

    # If it looks like a job ID (UUID-like), check status
    is_job_id = not url_or_job_id.startswith("http") or status_check

    if is_job_id:
        try:
            if status_check:
                result = client.crawl_status(url_or_job_id)
            else:
                result = client.crawl_get(url_or_job_id)
            _output_json({"data": result, "meta": {}})
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
        return

    # Start new crawl
    _validate_url(url_or_job_id, json_output)

    # Resolve session cookies
    _session_cookies = None
    if session:
        if session.startswith("@"):
            from ..config import load_session as _load_session
            try:
                _session_cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(f"Session not found: {session[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        else:
            from ..cookies import load_cookies
            try:
                _session_cookies = load_cookies(Path(session))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _error(f"Cannot read session file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    raw_body = _parse_body(body, json_output)
    auth_dict = _parse_auth(auth, json_output)
    custom_headers = _parse_headers(headers, json_output)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    if raw_body:
        raw_body.setdefault("url", url_or_job_id)
        try:
            result = client.post_raw("crawl", raw_body)
            job_id = result.get("result", "")
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return
    else:
        kwargs = {}
        if limit is not None:
            kwargs["limit"] = limit
        if max_depth is not None:
            kwargs["depth"] = max_depth
        if format:
            kwargs["formats"] = [format]
        if no_render:
            kwargs["render"] = False
        if source:
            kwargs["source"] = source
        if allow_external:
            kwargs["include_external"] = True
        if allow_subdomains:
            kwargs["include_subdomains"] = True
        if include_paths:
            kwargs["include_patterns"] = [p.strip() for p in include_paths.split(",")]
        if exclude_paths:
            kwargs["exclude_patterns"] = [p.strip() for p in exclude_paths.split(",")]
        if auth_dict:
            kwargs.update(auth_dict)
        if _session_cookies:
            kwargs["cookies"] = _session_cookies
        if ignore_robots:
            # CF /crawl always respects robots.txt â€” no API parameter exists
            console.print(
                "[yellow]Warning:[/yellow] CF /crawl always respects robots.txt (blocked URLs get status 'disallowed').\n"
                "  To crawl ignoring robots.txt, use:\n"
                f"    flarecrawl spider {url_or_job_id} --ignore-robots --limit {limit or 50}\n"
                f"    flarecrawl authcrawl {url_or_job_id} --ignore-robots",
            )

        try:
            job_id = client.crawl_start(url_or_job_id, **kwargs)
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return

    if not wait:
        result = {"job_id": job_id, "status": "running", "url": url_or_job_id}
        if json_output:
            _output_json({"data": result, "meta": {}})
        else:
            console.print(f"Crawl started: [cyan]{job_id}[/cyan]")
            console.print(f"Check status: flarecrawl crawl {job_id} --status")
        return

    # Wait for completion
    try:
        if progress:
            with Live(Spinner("dots", text="Starting crawl..."), console=console, refresh_per_second=4) as live:
                def update_progress(status):
                    finished = status.get("finished", 0)
                    total = status.get("total", "?")
                    state = status.get("status", "running")
                    live.update(Spinner("dots", text=f"Crawling... {finished}/{total} pages [{state}]"))

                final_status = client.crawl_wait(
                    job_id, timeout=timeout or 600, poll_interval=poll_interval,
                    callback=update_progress,
                )
        else:
            final_status = client.crawl_wait(
                job_id, timeout=timeout or 600, poll_interval=poll_interval,
            )
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    # Fetch results
    try:
        if ndjson:
            # Stream mode: output one record per line as they come
            count = 0
            _ndjson_hashes: set[str] = set()
            for record in client.crawl_get_all(job_id):
                record = _filter_record_content(record, only_main_content, _inc, _exc, agent_safe=agent_safe)
                if deduplicate:
                    import hashlib
                    ct = record.get("markdown", "") or record.get("html", "")
                    h = hashlib.md5(ct.encode()).hexdigest()
                    if h in _ndjson_hashes:
                        continue
                    _ndjson_hashes.add(h)
                if fields:
                    record = _filter_fields(record, fields)
                _output_ndjson(record)
                count += 1
            if client.browser_ms_used:
                console.print(f"[dim]Browser time: {client.browser_ms_used}ms ({count} records)[/dim]")
            return

        _seen_hashes: set[str] = set()
        records = []
        for r in client.crawl_get_all(job_id):
            r = _filter_record_content(r, only_main_content, _inc, _exc, agent_safe=agent_safe)
            if deduplicate:
                import hashlib
                content_text = r.get("markdown", "") or r.get("html", "")
                h = hashlib.md5(content_text.encode()).hexdigest()
                if h in _seen_hashes:
                    continue
                _seen_hashes.add(h)
            records.append(r)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    if tech_detect:
        _apply_tech_detection(records, emit_summary=not json_output)

    result = {
        "job_id": job_id,
        "status": final_status.get("status"),
        "total": final_status.get("total", len(records)),
        "browser_seconds": final_status.get("browserSecondsUsed"),
        "records": records,
    }

    if fields:
        result["records"] = _filter_fields(result["records"], fields)

    # Webhook: POST results to URL on completion
    if webhook:
        import httpx as _httpx
        wh_headers = _parse_headers(webhook_headers) or {}
        wh_headers.setdefault("Content-Type", "application/json")
        payload = {"data": result, "meta": {"count": len(records)}}
        try:
            resp = _httpx.post(webhook, json=payload, headers=wh_headers, timeout=30)
            console.print(f"[dim]Webhook: POST {webhook} â†’ {resp.status_code}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Webhook failed:[/yellow] {e}")

    if output:
        output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        console.print(f"Results saved to {output} ({len(records)} pages)")
    elif json_output:
        _output_json({"data": result, "meta": {"count": len(records)}})
    else:
        _output_json(result)


# ------------------------------------------------------------------
# map â€” matches firecrawl map
# ------------------------------------------------------------------


@_cmd.command("map")
def map_urls(
    url: Annotated[str, typer.Argument(help="URL to map")],
    limit: Annotated[int | None, typer.Option("--limit", help="Max URLs to discover")] = None,
    include_subdomains: Annotated[bool, typer.Option("--include-subdomains", help="Include subdomains")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """Discover all URLs on a website.

    Uses the /links endpoint for quick single-page discovery.
    For deep discovery, use 'flarecrawl crawl' with --format links.

    Example:
        flarecrawl map https://example.com
        flarecrawl map https://example.com --json
        flarecrawl map https://example.com --include-subdomains
        flarecrawl map https://intranet.example.com --auth user:pass
    """
    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output, cache_ttl=cache_ttl)
    _validate_url(url, json_output)
    raw_body = _parse_body(body, json_output)
    auth_dict = _parse_auth(auth, json_output)
    custom_headers = _parse_headers(headers, json_output)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    try:
        if raw_body:
            raw_body.setdefault("url", url)
            result = client.post_raw("links", raw_body)
            links = result.get("result", result)
        else:
            kwargs = {}
            if include_subdomains:
                kwargs["internal_only"] = False
            else:
                kwargs["internal_only"] = True
            if auth_dict:
                kwargs.update(auth_dict)
            if user_agent:
                kwargs["user_agent"] = user_agent
            links = client.get_links(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    if not isinstance(links, list):
        links = [links]

    # Apply limit
    if limit and len(links) > limit:
        links = links[:limit]

    if output:
        output.write_text("\n".join(links), encoding="utf-8")
        console.print(f"Saved {len(links)} URLs to {output}")
    elif json_output:
        _output_json({"data": links, "meta": {"count": len(links)}})
    else:
        for link in links:
            _output_text(link)


# ------------------------------------------------------------------
# download â€” matches firecrawl download
# ------------------------------------------------------------------


@_cmd.command()
def download(
    url: Annotated[str, typer.Argument(help="URL to download")],
    limit: Annotated[int | None, typer.Option("--limit", help="Max pages")] = None,
    include_paths: Annotated[str | None, typer.Option("--include-paths", help="Include path patterns (comma-separated)")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    exclude_paths: Annotated[str | None, typer.Option("--exclude-paths", help="Exclude path patterns (comma-separated)")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    allow_subdomains: Annotated[bool, typer.Option("--allow-subdomains", help="Include subdomains")] = False,
    format: Annotated[str, typer.Option("--format", "-f", help="Format: markdown, html")] = "markdown",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    only_main_content: Annotated[bool, typer.Option("--only-main-content", help="Keep main content only")] = False,
    exclude_tags: Annotated[str | None, typer.Option("--exclude-tags", help="CSS selectors to remove")] = None,
    include_tags: Annotated[str | None, typer.Option("--include-tags", help="CSS selectors to keep")] = None,
    backup_dir: Annotated[Path | None, typer.Option("--backup-dir", help="Save raw HTML to this directory")] = None,
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
):
    """Download a site into .flarecrawl/ as files.

    Crawls the site and saves each page as a file in a nested directory structure.

    Example:
        flarecrawl download https://example.com --limit 20
        flarecrawl download https://docs.example.com -f html --limit 50
        flarecrawl download https://intranet.example.com --limit 20 --auth user:pass
    """
    client = _get_client(json_output)
    _validate_url(url, json_output)
    auth_dict = _parse_auth(auth, json_output)
    custom_headers = _parse_headers(headers, json_output)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    parsed = urlparse(url)
    site_name = parsed.netloc.replace(":", "-")
    output_dir = Path(".flarecrawl") / site_name
    ext = ".md" if format == "markdown" else ".html"

    # Confirmation
    if not yes:
        console.print(f"Will crawl [cyan]{url}[/cyan] and save to [cyan]{output_dir}/[/cyan]")
        if limit:
            console.print(f"Limit: {limit} pages")
        if not typer.confirm("Proceed?", default=True):
            raise typer.Exit(0)

    # Start crawl
    kwargs = {"formats": [format]}
    if limit:
        kwargs["limit"] = limit
    if allow_subdomains:
        kwargs["include_subdomains"] = True
    if include_paths:
        kwargs["include_patterns"] = [p.strip() for p in include_paths.split(",")]
    if exclude_paths:
        kwargs["exclude_patterns"] = [p.strip() for p in exclude_paths.split(",")]
    if auth_dict:
        kwargs.update(auth_dict)

    try:
        job_id = client.crawl_start(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    # Wait with progress
    console.print(f"Crawl started: [cyan]{job_id}[/cyan]")
    with Live(Spinner("dots", text="Crawling..."), console=console, refresh_per_second=4) as live:
        def update(status):
            f = status.get("finished", 0)
            t = status.get("total", "?")
            live.update(Spinner("dots", text=f"Crawling... {f}/{t} pages"))

        try:
            client.crawl_wait(job_id, timeout=3600, callback=update)
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return

    # Parse content filtering
    _inc = [s.strip() for s in include_tags.split(",")] if include_tags else None
    _exc = [s.strip() for s in exclude_tags.split(",")] if exclude_tags else None

    # Save results
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    errors = 0

    for record in client.crawl_get_all(job_id, status="completed"):
        record = _filter_record_content(record, only_main_content, _inc, _exc, agent_safe=agent_safe)
        page_url = record.get("url", "")
        content_key = format  # "markdown" or "html"
        content = record.get(content_key, "")

        if not content:
            errors += 1
            continue

        filename = _sanitize_filename(page_url) + ext
        filepath = output_dir / filename
        filepath.write_text(content, encoding="utf-8")

        # Backup raw HTML alongside extracted content
        if backup_dir:
            backup_dir.mkdir(parents=True, exist_ok=True)
            raw_html = record.get("html", "")
            if raw_html:
                (backup_dir / (_sanitize_filename(page_url) + ".html")).write_text(
                    raw_html, encoding="utf-8",
                )
        saved += 1

    summary = {
        "directory": str(output_dir),
        "saved": saved,
        "errors": errors,
        "format": format,
    }

    if json_output:
        _output_json({"data": summary, "meta": {}})
    else:
        console.print(f"\n[green]Downloaded {saved} pages[/green] to {output_dir}/")
        if errors:
            console.print(f"[yellow]{errors} pages had no content[/yellow]")


# ------------------------------------------------------------------
# extract â€” matches firecrawl agent
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('crawl')(crawl)
    app.command('map')(map_urls)
    app.command('download')(download)
