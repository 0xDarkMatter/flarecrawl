"""screenshot, pdf, favicon commands."""

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
def screenshot(
    url: Annotated[str, typer.Argument(help="URL to screenshot")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output file")] = Path("screenshot.png"),
    full_page: Annotated[bool, typer.Option("--full-page", help="Capture full page")] = False,
    format: Annotated[str, typer.Option("--format", help="Image format: png, jpeg")] = "png",
    width: Annotated[int | None, typer.Option("--width", help="Viewport width")] = None,
    height: Annotated[int | None, typer.Option("--height", help="Viewport height")] = None,
    selector: Annotated[str | None, typer.Option("--selector", help="CSS selector to capture")] = None,
    wait_for: Annotated[str | None, typer.Option("--wait-for", help="CSS selector to wait for")] = None,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Timeout in ms")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON (base64)")] = False,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    mobile: Annotated[bool, typer.Option("--mobile", help="Emulate mobile device (iPhone 14 Pro viewport)")] = False,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """Capture a screenshot of a web page.

    Example:
        flarecrawl screenshot https://example.com
        flarecrawl screenshot https://example.com -o hero.png --full-page
        flarecrawl screenshot https://example.com --selector "main" -o main.png
        flarecrawl screenshot https://intranet.example.com --auth user:pass
    """
    client = _get_client(json_output)
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
            data, _ = client._post_binary("screenshot", raw_body)
        else:
            kwargs = {}
            if full_page:
                kwargs["full_page"] = True
            if format != "png":
                kwargs["image_type"] = format
            if width:
                kwargs["width"] = width
            if height:
                kwargs["height"] = height
            if selector:
                kwargs["selector"] = selector
            if wait_for:
                kwargs["wait_for"] = wait_for
            if timeout:
                kwargs["timeout"] = timeout
            if mobile:
                kwargs.update(MOBILE_PRESET)
            if auth_dict:
                kwargs.update(auth_dict)
            if user_agent:
                kwargs["user_agent"] = user_agent
            data = client.take_screenshot(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    if json_output:
        _output_json({
            "data": {
                "screenshot": base64.b64encode(data).decode(),
                "encoding": "base64",
                "format": format,
                "size": len(data),
            },
            "meta": {"url": url},
        })
    else:
        output.write_bytes(data)
        console.print(f"Screenshot saved: [cyan]{output}[/cyan] ({len(data):,} bytes)")


# ------------------------------------------------------------------
# pdf â€” bonus command (CF has this, firecrawl doesn't)
# ------------------------------------------------------------------


@_cmd.command()
def pdf(
    url: Annotated[str, typer.Argument(help="URL to render as PDF")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output file")] = Path("page.pdf"),
    landscape: Annotated[bool, typer.Option("--landscape", help="Landscape orientation")] = False,
    format: Annotated[str, typer.Option("--format", help="Paper format: letter, a4")] = "letter",
    print_background: Annotated[bool, typer.Option("--print-background", help="Include background")] = True,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Timeout in ms")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON (base64)")] = False,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    mobile: Annotated[bool, typer.Option("--mobile", help="Emulate mobile device (iPhone 14 Pro viewport)")] = False,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """Render a web page as PDF.

    Example:
        flarecrawl pdf https://example.com
        flarecrawl pdf https://example.com -o report.pdf --landscape
        flarecrawl pdf https://intranet.example.com --auth user:pass
    """
    client = _get_client(json_output)
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
            data, _ = client._post_binary("pdf", raw_body)
        else:
            kwargs = {}
            if landscape:
                kwargs["landscape"] = True
            if format != "letter":
                kwargs["paper_format"] = format
            if print_background:
                kwargs["print_background"] = True
            if timeout:
                kwargs["timeout"] = timeout
            if mobile:
                kwargs.update(MOBILE_PRESET)
            if auth_dict:
                kwargs.update(auth_dict)
            if user_agent:
                kwargs["user_agent"] = user_agent
            data = client.render_pdf(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    if json_output:
        _output_json({
            "data": {
                "pdf": base64.b64encode(data).decode(),
                "encoding": "base64",
                "size": len(data),
            },
            "meta": {"url": url},
        })
    else:
        output.write_bytes(data)
        console.print(f"PDF saved: [cyan]{output}[/cyan] ({len(data):,} bytes)")


# ------------------------------------------------------------------
# favicon â€” extract favicon URL
# ------------------------------------------------------------------


def _extract_favicons(html: str, base_url: str) -> list[dict]:
    """Parse <link rel="icon"> and related tags from HTML."""
    from html.parser import HTMLParser
    from urllib.parse import urljoin

    favicons: list[dict] = []

    class FaviconParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag != "link":
                return
            attr_dict = dict(attrs)
            rel = (attr_dict.get("rel") or "").lower()
            href = attr_dict.get("href")
            if not href:
                return
            icon_rels = {"icon", "shortcut icon", "apple-touch-icon", "apple-touch-icon-precomposed"}
            if rel not in icon_rels:
                return
            sizes = attr_dict.get("sizes", "")
            # Parse size to integer for sorting (e.g., "192x192" â†’ 192)
            size = 0
            if sizes and "x" in sizes.lower():
                try:
                    size = int(sizes.lower().split("x")[0])
                except ValueError:
                    pass
            favicons.append({
                "url": urljoin(base_url, href),
                "rel": rel,
                "sizes": sizes or None,
                "size": size,
                "type": attr_dict.get("type"),
            })

    FaviconParser().feed(html)

    # Sort: largest first, apple-touch-icon preferred at equal size
    favicons.sort(key=lambda f: (f["size"], "apple" in f["rel"]), reverse=True)
    return favicons


@_cmd.command()
def favicon(
    url: Annotated[str, typer.Argument(help="URL to extract favicon from")],
    all_icons: Annotated[bool, typer.Option("--all", help="Show all found icons, not just the best")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Timeout in ms")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """Extract favicon URL from a web page.

    Renders the page, parses <link rel="icon"> and apple-touch-icon tags,
    and returns the largest/best favicon found.

    Example:
        flarecrawl favicon https://example.com
        flarecrawl favicon https://example.com --all --json
    """
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
        # Reject images/media/fonts to speed up â€” we only need HTML
        kwargs["reject_resources"] = ["image", "media", "font", "stylesheet"]
        if auth_dict:
            kwargs.update(auth_dict)
        if user_agent:
            kwargs["user_agent"] = user_agent
        html = client.get_content(url, **kwargs)
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
        return

    favicons = _extract_favicons(html, url)

    if not favicons:
        # Fallback: try /favicon.ico
        from urllib.parse import urlparse
        parsed = urlparse(url)
        fallback = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        favicons = [{"url": fallback, "rel": "icon", "sizes": None, "size": 0, "type": None}]
        if not json_output:
            console.print(f"[yellow]No <link> icons found, falling back to:[/yellow] {fallback}")

    if all_icons:
        # Strip internal sort key
        output_data = [{k: v for k, v in f.items() if k != "size"} for f in favicons]
    else:
        best = favicons[0]
        output_data = {k: v for k, v in best.items() if k != "size"}

    if json_output:
        meta = {"url": url, "count": len(favicons)}
        _output_json({"data": output_data, "meta": meta})
    else:
        if all_icons:
            for f in favicons:
                size_str = f" ({f['sizes']})" if f.get("sizes") else ""
                console.print(f"[cyan]{f['url']}[/cyan]{size_str} [{f['rel']}]")
        else:
            best = favicons[0]
            _output_text(best["url"])


# ------------------------------------------------------------------
# recipe â€” declarative multi-step browser flows (v0.25.0 P3.1)
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('screenshot')(screenshot)
    app.command('pdf')(pdf)
    app.command('favicon')(favicon)
