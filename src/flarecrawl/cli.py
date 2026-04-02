"""Flarecrawl CLI - Firecrawl-compatible CLI backed by Cloudflare Browser Rendering."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import re
import sys
import time as _time
from datetime import UTC
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from . import __version__
from .batch import parse_batch_file, process_batch
from .client import MOBILE_PRESET, Client, FlareCrawlError
from .config import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_WORKERS,
    clear_credentials,
    get_account_id,
    get_api_token,
    get_auth_status,
    get_usage,
    save_credentials,
)

app = typer.Typer(
    name="flarecrawl",
    help="Cloudflare Browser Rendering CLI — drop-in firecrawl replacement, much cheaper.",
    no_args_is_help=True,
)

# stderr for human output (stdout is sacred)
console = Console(stderr=True)

# Fabric Protocol exit codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_AUTH_REQUIRED = 2
EXIT_NOT_FOUND = 3
EXIT_VALIDATION = 4
EXIT_FORBIDDEN = 5
EXIT_RATE_LIMITED = 7


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _output_json(data) -> None:
    """Output JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def _output_ndjson(record: dict) -> None:
    """Output single JSON record (newline-delimited)."""
    print(json.dumps(record, default=str))


def _output_text(text: str) -> None:
    """Output raw text to stdout."""
    print(text)


def _filter_fields(data, fields: str | None):
    """Filter JSON output to only include specified fields."""
    if not fields:
        return data
    keep = {f.strip() for f in fields.split(",")}
    if isinstance(data, list):
        return [{k: v for k, v in item.items() if k in keep} for item in data]
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in keep}
    return data


def _error(
    message: str,
    code: str = "ERROR",
    exit_code: int = EXIT_ERROR,
    details: dict | None = None,
    as_json: bool = False,
) -> None:
    """Output error and exit."""
    error_obj = {"error": {"code": code, "message": message}}
    if details:
        error_obj["error"]["details"] = details

    if as_json:
        _output_json(error_obj)
    else:
        console.print(f"[red]Error:[/red] {message}")

    raise typer.Exit(exit_code)


def _require_auth(as_json: bool = False) -> None:
    """Check authentication, exit if not authenticated."""
    if not get_account_id() or not get_api_token():
        _error(
            "Not authenticated. Run: flarecrawl auth login",
            "AUTH_REQUIRED",
            EXIT_AUTH_REQUIRED,
            as_json=as_json,
        )


def _handle_api_error(e: FlareCrawlError, as_json: bool = False) -> None:
    """Map API error to Fabric exit code."""
    code_map = {
        "AUTH_REQUIRED": EXIT_AUTH_REQUIRED,
        "NOT_FOUND": EXIT_NOT_FOUND,
        "VALIDATION_ERROR": EXIT_VALIDATION,
        "FORBIDDEN": EXIT_FORBIDDEN,
        "RATE_LIMITED": EXIT_RATE_LIMITED,
    }
    exit_code = code_map.get(e.code, EXIT_ERROR)
    _error(str(e), e.code, exit_code, as_json=as_json)


def _validate_url(url: str, as_json: bool = False) -> None:
    """Validate URL format."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        _error(
            f"Invalid URL: {url} (must include scheme, e.g. https://)",
            "VALIDATION_ERROR",
            EXIT_VALIDATION,
            {"url": url},
            as_json,
        )


def _parse_body(body_str: str | None, as_json: bool = False) -> dict | None:
    """Parse --body JSON string."""
    if not body_str:
        return None
    try:
        return json.loads(body_str)
    except json.JSONDecodeError as e:
        _error(
            f"Invalid --body JSON: {e}",
            "VALIDATION_ERROR",
            EXIT_VALIDATION,
            as_json=as_json,
        )
    return None  # unreachable


def _parse_auth(auth_str: str | None, as_json: bool = False) -> dict | None:
    """Parse --auth user:pass into auth kwargs for CF Browser Rendering API.

    Returns a dict with both 'authenticate' and 'extra_headers' keys.
    - authenticate: Puppeteer page.authenticate() — responds to 401 challenges
    - extra_headers: setExtraHTTPHeaders — proactive Authorization on every request

    Both are sent; the API uses whichever works for the target site.
    CF-proxied targets may reject setExtraHTTPHeaders (422), so authenticate
    is the primary mechanism. For non-proxied origins behind redirects,
    setExtraHTTPHeaders survives redirect hops.
    """
    if not auth_str:
        return None
    if ":" not in auth_str:
        _error(
            "Invalid --auth format. Expected user:password",
            "VALIDATION_ERROR",
            EXIT_VALIDATION,
            as_json=as_json,
        )
    username, password = auth_str.split(":", 1)
    return {
        "authenticate": {"username": username, "password": password},
        "extra_headers": {"Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}"},
    }


def _parse_headers(headers: list[str] | None, as_json: bool = False) -> dict | None:
    """Parse --headers values into a dict for setExtraHTTPHeaders.

    Accepts:
      - "Key: Value" (curl-style, split on first colon)
      - '{"Key": "Value"}' (JSON object)
    Multiple values are merged into a single dict.
    """
    if not headers:
        return None
    result: dict[str, str] = {}
    for h in headers:
        h = h.strip()
        if h.startswith("{"):
            try:
                parsed = json.loads(h)
                result.update(parsed)
            except json.JSONDecodeError as e:
                _error(
                    f"Invalid --headers JSON: {e}",
                    "VALIDATION_ERROR", EXIT_VALIDATION, as_json=as_json,
                )
        elif ":" in h:
            key, value = h.split(":", 1)
            result[key.strip()] = value.strip()
        else:
            _error(
                f"Invalid --headers format: {h!r} (expected 'Key: Value' or JSON)",
                "VALIDATION_ERROR", EXIT_VALIDATION, as_json=as_json,
            )
    return result if result else None



def _sanitize_filename(url: str) -> str:
    """Convert URL to safe filename, preserving query params for uniqueness."""
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    # Include query params in filename to avoid collisions
    # /search?q=test&page=2 -> search--q-test-page-2
    if parsed.query:
        path = f"{path}--{parsed.query}"
    # Replace path separators and unsafe chars
    name = re.sub(r'[^\w\-.]', '-', path)
    name = re.sub(r'-+', '-', name).strip('-')
    # Truncate to avoid filesystem path limits (255 chars max for filename)
    if len(name) > 200:
        import hashlib
        suffix = hashlib.md5(name.encode()).hexdigest()[:8]
        name = f"{name[:190]}--{suffix}"
    return name or "index"


def _filter_record_content(
    record: dict,
    only_main_content: bool = False,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> dict:
    """Apply content filtering to a crawl/download record in-place."""
    if not (only_main_content or include_tags or exclude_tags):
        return record
    for key in ("markdown", "html"):
        content = record.get(key)
        if not content or not isinstance(content, str):
            continue
        from .extract import extract_main_content, filter_tags, html_to_markdown
        # For markdown, we need to work with the HTML version
        # But crawl records may only have markdown. In that case, skip HTML-based filtering.
        if key == "html" or "<" in content[:100]:
            html = content
            if only_main_content:
                html = extract_main_content(html)
            if include_tags:
                html = filter_tags(html, include=include_tags)
            if exclude_tags:
                html = filter_tags(html, exclude=exclude_tags)
            if key == "html":
                record[key] = html
            else:
                record[key] = html_to_markdown(html)
    return record


def _get_client(as_json: bool = False, cache_ttl: int = 3600) -> Client:
    """Get authenticated client."""
    _require_auth(as_json)
    return Client(cache_ttl=cache_ttl)


# ------------------------------------------------------------------
# Version callback
# ------------------------------------------------------------------


def version_callback(value: bool):
    if value:
        print(f"flarecrawl {__version__}")
        raise typer.Exit()


def status_callback(value: bool):
    if value:
        status = get_auth_status()
        console.print(f"flarecrawl {__version__}")
        console.print()
        if status.get("authenticated"):
            console.print(f"Auth: [green]authenticated[/green] (source: {status.get('source')})")
            console.print(f"Account: [cyan]{status.get('account_id')}[/cyan]")
        else:
            console.print("Auth: [red]not authenticated[/red]")
            console.print("Run: flarecrawl auth login")
        console.print()
        console.print("[dim]Pricing: Free 10 min/day, then $0.09/hr[/dim]")
        console.print("[dim]Limits: Free 3 concurrent, Paid 10 concurrent browsers[/dim]")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", callback=version_callback, is_eager=True),
    ] = None,
    status: Annotated[
        bool | None,
        typer.Option("--status", callback=status_callback, is_eager=True,
                     help="Show version, auth status, and usage info"),
    ] = None,
):
    """Cloudflare Browser Rendering CLI — drop-in firecrawl replacement."""


# ------------------------------------------------------------------
# Auth commands
# ------------------------------------------------------------------

auth_app = typer.Typer(help="Authentication")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
def auth_login(
    account_id: Annotated[
        str | None, typer.Option("--account-id", help="Cloudflare account ID")
    ] = None,
    token: Annotated[
        str | None, typer.Option("--token", help="Cloudflare API token")
    ] = None,
):
    """Authenticate with Cloudflare Browser Rendering.

    Opens the Cloudflare dashboard in your browser to create a token,
    then prompts for your account ID and token.

    Example:
        flarecrawl auth login
        flarecrawl auth login --account-id abc123 --token cftoken
    """
    import webbrowser

    if not account_id or not token:
        console.print("\n[bold]Cloudflare Browser Rendering Setup[/bold]\n")

    if not account_id:
        console.print("1. Open [cyan]https://dash.cloudflare.com[/cyan]")
        console.print("   Copy your [bold]Account ID[/bold] from the right sidebar\n")
        if typer.confirm("Open Cloudflare dashboard in browser?", default=True):
            webbrowser.open("https://dash.cloudflare.com")
        account_id = typer.prompt("Account ID")

    if not token:
        console.print("\n2. Create an API token with [bold]Browser Rendering - Edit[/bold] permission")
        console.print("   Custom Token → Account → Browser Rendering → Edit\n")
        if typer.confirm("Open token creation page in browser?", default=True):
            webbrowser.open("https://dash.cloudflare.com/profile/api-tokens")
        token = typer.prompt("API Token", hide_input=True)

    # Validate credentials with a lightweight test
    console.print("Validating credentials...", style="dim")
    try:
        client = Client(account_id=account_id, api_token=token, cache_ttl=0)
        client.get_content(html="<h1>test</h1>")
        console.print("[green]Credentials valid[/green]")
    except FlareCrawlError as e:
        code = getattr(e, "code", "")
        status = getattr(e, "status_code", None)
        if code == "AUTH_REQUIRED" or status == 401 or "authentication" in str(e).lower():
            console.print("[red]Authentication failed:[/red] Invalid API token")
            console.print("Check your token at: https://dash.cloudflare.com/profile/api-tokens")
        elif code == "FORBIDDEN" or status == 403:
            console.print("[red]Permission denied:[/red] Token missing 'Browser Rendering - Edit' permission")
            console.print("Edit your token at: https://dash.cloudflare.com/profile/api-tokens")
            console.print("Add: Account > Browser Rendering > Edit")
        elif "route" in str(e).lower() or status == 404:
            console.print("[red]Account not found:[/red] Check your account ID")
            console.print("Find it at: https://dash.cloudflare.com > Overview > Account ID")
        else:
            console.print(f"[yellow]Validation warning:[/yellow] {e}")
            console.print("This may be a temporary issue. Credentials saved -- try a scrape to verify.")

    save_credentials(account_id, token)
    console.print("[green]Credentials saved[/green]")


@auth_app.command("status")
def auth_status(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Check authentication status.

    Example:
        flarecrawl auth status
        flarecrawl auth status --json
    """
    status = get_auth_status()

    if json_output:
        _output_json({"data": status, "meta": {}})
        return

    if status.get("authenticated"):
        console.print("Authenticated: [green]yes[/green]")
        console.print(f"Source: [cyan]{status.get('source')}[/cyan]")
        console.print(f"Account: [cyan]{status.get('account_id')}[/cyan]")
    else:
        console.print("Authenticated: [red]no[/red]")
        missing = status.get("missing", [])
        if missing:
            console.print(f"Missing: {', '.join(missing)}")
        console.print("Run: flarecrawl auth login")


@auth_app.command("logout")
def auth_logout():
    """Clear stored credentials.

    Example:
        flarecrawl auth logout
    """
    clear_credentials()
    console.print("[green]Logged out[/green]")


# ------------------------------------------------------------------
# cache — manage response cache
# ------------------------------------------------------------------

cache_app = typer.Typer(help="Response cache management")
app.add_typer(cache_app, name="cache")


@cache_app.command("clear")
def cache_clear():
    """Clear all cached responses.

    Example:
        flarecrawl cache clear
    """
    from . import cache
    count = cache.clear()
    console.print(f"Cleared {count} cached response{'s' if count != 1 else ''}")


@cache_app.command("status")
def cache_status(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show cache statistics.

    Example:
        flarecrawl cache status
        flarecrawl cache status --json
    """
    from . import cache
    cache_dir = cache._cache_dir()
    entries = list(cache_dir.glob("*.json"))
    total_bytes = sum(f.stat().st_size for f in entries)

    data = {
        "entries": len(entries),
        "size_bytes": total_bytes,
        "size_human": f"{total_bytes / 1024:.1f} KB" if total_bytes > 0 else "0 KB",
        "path": str(cache_dir),
    }

    if json_output:
        _output_json({"data": data, "meta": {}})
        return

    console.print(f"Entries: [cyan]{data['entries']}[/cyan]")
    console.print(f"Size: [cyan]{data['size_human']}[/cyan]")
    console.print(f"Path: [dim]{data['path']}[/dim]")


# ------------------------------------------------------------------
# negotiate — domain cache management
# ------------------------------------------------------------------


negotiate_app = typer.Typer(help="Markdown negotiate domain cache management")
app.add_typer(negotiate_app, name="negotiate")


@negotiate_app.command("status")
def negotiate_status(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show markdown negotiation domain cache.

    Example:
        flarecrawl negotiate status
        flarecrawl negotiate status --json
    """
    from .negotiate import _cache_path, _load_domain_cache
    cache = _load_domain_cache()
    supporting = [d for d, v in cache.items() if v.get("supports")]
    non_supporting = [d for d, v in cache.items() if not v.get("supports")]

    data = {
        "total": len(cache),
        "supporting": len(supporting),
        "non_supporting": len(non_supporting),
        "domains_supporting": supporting,
        "path": str(_cache_path()),
    }

    if json_output:
        _output_json({"data": data, "meta": {}})
        return

    console.print(f"Domains cached: [cyan]{data['total']}[/cyan]")
    console.print(f"Supporting markdown: [green]{data['supporting']}[/green]")
    console.print(f"Not supporting: [dim]{data['non_supporting']}[/dim]")
    if supporting:
        console.print(f"Domains: [green]{', '.join(supporting)}[/green]")
    console.print(f"Path: [dim]{data['path']}[/dim]")


@negotiate_app.command("clear")
def negotiate_clear():
    """Clear the domain capability cache.

    Example:
        flarecrawl negotiate clear
    """
    from .negotiate import clear_domain_cache
    count = clear_domain_cache()
    console.print(f"Cleared {count} domain cache entr{'ies' if count != 1 else 'y'}")


# ------------------------------------------------------------------
# scrape — matches firecrawl scrape
# ------------------------------------------------------------------


def _scrape_single(client: Client, url: str, format: str, wait_for: int | None,
                   screenshot: bool, full_page_screenshot: bool,
                   raw_body: dict | None, timeout_ms: int | None,
                   wait_until: str | None = None,
                   auth_kwargs: dict | None = None,
                   mobile: bool = False,
                   only_main_content: bool = False,
                   include_tags: list[str] | None = None,
                   exclude_tags: list[str] | None = None,
                   user_agent: str | None = None,
                   wait_for_selector: str | None = None,
                   css_selector: str | None = None,
                   js_expression: str | None = None,
                   archived: bool = False,
                   magic: bool = False,
                   scroll: bool = False,
                   query: str | None = None,
                   precision: bool = False,
                   recall: bool = False,
                   no_negotiate: bool = False,
                   negotiate_headers: dict | None = None,
                   negotiate_session: "httpx.Client | None" = None,
                   paywall: bool = False,
                   paywall_session: "httpx.Client | None" = None,
                   stealth: bool = False) -> dict:
    """Scrape a single URL. Returns result dict. Used for concurrent scraping."""
    start = _time.time()

    # ------------------------------------------------------------------
    # Markdown content negotiation (fast path — no browser rendering)
    # ------------------------------------------------------------------
    # Try Accept: text/markdown before spinning up headless Chromium.
    # Only for simple markdown scrapes with no browser-specific flags.
    _browser_needed = any([
        raw_body, screenshot, full_page_screenshot, css_selector,
        js_expression, wait_for_selector, wait_until, scroll, magic,
        format != "markdown",
    ])
    if not no_negotiate and not _browser_needed:
        from .negotiate import try_negotiate
        neg_headers = dict(negotiate_headers or {})
        if user_agent:
            neg_headers["User-Agent"] = user_agent
        if auth_kwargs and "authenticate" in auth_kwargs:
            import base64 as _b64
            _creds = auth_kwargs["authenticate"]
            _basic = _b64.b64encode(
                f"{_creds['username']}:{_creds['password']}".encode()
            ).decode()
            neg_headers["Authorization"] = f"Basic {_basic}"

        # NOTE: do NOT pass client._session — it carries CF API auth
        # headers that must not leak to arbitrary target sites.
        # Use negotiate_session if provided (batch mode reuse).
        neg_result = try_negotiate(
            url,
            session=negotiate_session,
            extra_headers=neg_headers or None,
            stealth=stealth,
        )
        if neg_result is not None:
            content = neg_result.content
            # Apply post-processing that works on markdown text
            if query:
                from .extract import filter_by_query
                content = filter_by_query(content, query)
            from .extract import clean_content
            content = clean_content(content)

            elapsed = _time.time() - start
            result = {"url": url, "content": content, "elapsed": round(elapsed, 2)}

            # Build metadata
            metadata = {}
            metadata["source"] = "content-negotiation"
            metadata["browserTimeMs"] = 0
            if neg_result.tokens is not None:
                metadata["markdownTokens"] = neg_result.tokens
            if neg_result.content_signal:
                metadata["contentSignal"] = neg_result.content_signal
            if isinstance(content, str):
                metadata["contentLength"] = len(content)
                metadata["wordCount"] = len(content.split())
                metadata["headingCount"] = len(re.findall(r"^#{1,6}\s+", content, re.MULTILINE))
                metadata["linkCount"] = len(re.findall(r"\[.*?\]\(.*?\)", content))
                title_match = re.search(r"^#{1,2}\s+(.+?)$", content, re.MULTILINE)
                if title_match:
                    metadata["title"] = title_match.group(1).strip()
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and not stripped.startswith("[") and len(stripped) > 20:
                        metadata["description"] = stripped[:200]
                        break
            metadata["sourceURL"] = url
            metadata["format"] = format
            metadata["elapsed"] = result["elapsed"]
            metadata["cacheHit"] = False
            result["metadata"] = metadata
            return result

    # ------------------------------------------------------------------
    # Paywall bypass cascade
    # ------------------------------------------------------------------
    # When CF auth is available, only run the stealth tier (curl_cffi with
    # browser TLS fingerprint) — other tiers use the user's IP directly.
    # When no CF auth, run the full cascade.
    if paywall and not _browser_needed:
        pw_headers = dict(negotiate_headers or {})
        if user_agent:
            pw_headers["User-Agent"] = user_agent
        if auth_kwargs and "authenticate" in auth_kwargs:
            import base64 as _b64pw
            _creds_pw = auth_kwargs["authenticate"]
            _basic_pw = _b64pw.b64encode(
                f"{_creds_pw['username']}:{_creds_pw['password']}".encode()
            ).decode()
            pw_headers["Authorization"] = f"Basic {_basic_pw}"

        if client is not None:
            # CF auth available: only run stealth tier (curl_cffi) — other
            # tiers would expose the user's IP. If stealth fails, fall
            # through to browser rendering with site rules.
            from .paywall import _try_stealth_fetch
            pw_result = _try_stealth_fetch(url, None, pw_headers)
        else:
            # No CF auth: run full cascade (all tiers use user's IP anyway)
            from .paywall import try_bypass
            pw_result = try_bypass(
                url,
                session=paywall_session,
                extra_headers=pw_headers or None,
            )
        if pw_result is not None:
            content = pw_result.content
            if query:
                from .extract import filter_by_query
                content = filter_by_query(content, query)
            from .extract import clean_content
            content = clean_content(content)

            elapsed = _time.time() - start
            result = {"url": url, "content": content, "elapsed": round(elapsed, 2)}

            metadata = {}
            metadata["source"] = f"paywall-bypass-{pw_result.tier}"
            metadata["browserTimeMs"] = 0
            if isinstance(content, str):
                metadata["contentLength"] = len(content)
                metadata["wordCount"] = len(content.split())
                metadata["headingCount"] = len(re.findall(r"^#{1,6}\s+", content, re.MULTILINE))
                metadata["linkCount"] = len(re.findall(r"\[.*?\]\(.*?\)", content))
                title_match = re.search(r"^#{1,2}\s+(.+?)$", content, re.MULTILINE)
                if title_match:
                    metadata["title"] = title_match.group(1).strip()
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and not stripped.startswith("[") and len(stripped) > 20:
                        metadata["description"] = stripped[:200]
                        break
            metadata["sourceURL"] = url
            metadata["format"] = format
            metadata["elapsed"] = result["elapsed"]
            metadata["cacheHit"] = False
            metadata.update(pw_result.metadata)
            result["metadata"] = metadata
            return result

    # If paywall bypass was attempted but failed and we have no CF client,
    # we can't fall through to browser rendering.
    if client is None:
        return {
            "url": url,
            "error": "Paywall bypass failed and no Cloudflare credentials configured. Run: flarecrawl auth login",
            "elapsed": round(_time.time() - start, 2),
        }

    kwargs = {}
    if wait_for:
        kwargs["timeout"] = wait_for
    if timeout_ms:
        kwargs["timeout"] = timeout_ms
    if wait_until:
        kwargs["wait_until"] = wait_until
    if auth_kwargs:
        kwargs.update(auth_kwargs)
    if mobile:
        kwargs.update(MOBILE_PRESET)
    # --paywall: inject per-site headers into browser rendering request
    # (Googlebot UA, cookie clearing, referer spoofing per publisher)
    if paywall:
        from .paywall import _get_site_headers
        site_headers = _get_site_headers(url)
        if site_headers:
            existing = kwargs.get("extra_headers", {})
            kwargs["extra_headers"] = {**site_headers, **existing}
            # If site rules specify a User-Agent, use it for the browser too
            if "User-Agent" in site_headers and not user_agent:
                kwargs["user_agent"] = site_headers["User-Agent"]
    if user_agent:
        kwargs["user_agent"] = user_agent
    if wait_for_selector:
        kwargs["wait_for"] = wait_for_selector
    if scroll:
        # Inject JS to scroll page to bottom for lazy-loaded content
        _scroll_js = (
            "async function __flarecrawlScroll() {"
            "  const delay = ms => new Promise(r => setTimeout(r, ms));"
            "  let prev = 0;"
            "  for (let i = 0; i < 20; i++) {"
            "    window.scrollTo(0, document.body.scrollHeight);"
            "    await delay(300);"
            "    if (document.body.scrollHeight === prev) break;"
            "    prev = document.body.scrollHeight;"
            "  }"
            "  window.scrollTo(0, 0);"
            "}"
            "__flarecrawlScroll();"
        )
        # Will be applied via addScriptTag in the body builder
        kwargs.setdefault("_scroll_script", _scroll_js)
    if magic:
        # Hide common cookie banners, GDPR modals, newsletter popups
        kwargs["style_tag"] = (
            "[class*='cookie'],[class*='Cookie'],[id*='cookie'],[id*='Cookie'],"
            "[class*='consent'],[class*='Consent'],[id*='consent'],"
            "[class*='gdpr'],[class*='GDPR'],"
            "[class*='banner'],[id*='banner'],"
            "[class*='modal'],[class*='overlay'],"
            "[class*='popup'],[class*='Popup'],"
            "[class*='newsletter'],[class*='Newsletter'],"
            "[class*='onetrust'],[id*='onetrust'],"
            "[class*='cc-window'],[class*='cc-banner'],"
            "[id*='CybotCookiebotDialog'],"
            "[aria-label*='cookie'],[aria-label*='consent']"
            "{ display: none !important; visibility: hidden !important; }"
        )

    # --selector: use CF /scrape endpoint for CSS element extraction
    if css_selector:
        result_data = client.scrape(url, [css_selector], **kwargs)
        elapsed = _time.time() - start
        return {"url": url, "content": result_data, "elapsed": round(elapsed, 2)}

    # --js: inject JS that writes result to DOM, then scrape it back
    if js_expression:
        js_code = f"""
        try {{
            const __result = eval({json.dumps(js_expression)});
            const __el = document.createElement('pre');
            __el.id = '__flarecrawl_js_result';
            __el.textContent = typeof __result === 'object' ? JSON.stringify(__result) : String(__result);
            document.body.appendChild(__el);
        }} catch(e) {{
            const __el = document.createElement('pre');
            __el.id = '__flarecrawl_js_result';
            __el.textContent = JSON.stringify({{error: e.message}});
            document.body.appendChild(__el);
        }}
        """
        scrape_kwargs = {**kwargs}
        scrape_kwargs["style_tag"] = ""  # ensure page loads
        body = client._build_body(url=url, **kwargs)
        body["addScriptTag"] = [{"content": js_code}]
        body["elements"] = [{"selector": "#__flarecrawl_js_result"}]
        result_data = client._post_json("scrape", body)
        raw = result_data.get("result", [])
        # Extract the text from the injected element
        js_result = ""
        if isinstance(raw, list) and raw:
            results = raw[0].get("results", [])
            if results:
                js_result = results[0].get("text", "")
        # Try to parse as JSON
        try:
            content = json.loads(js_result)
        except (json.JSONDecodeError, TypeError):
            content = js_result
        elapsed = _time.time() - start
        return {"url": url, "content": content, "elapsed": round(elapsed, 2)}

    # Archived fallback: wrap URL for Wayback Machine on failure
    _fetch_url = url
    _archive_attempted = False

    # Extract scroll script from kwargs (not a CF API field)
    _scroll_script = kwargs.pop("_scroll_script", None)

    if raw_body:
        body_copy = {**raw_body, "url": _fetch_url}
        endpoint = "markdown" if format == "markdown" else "content"
        result_data = client.post_raw(endpoint, body_copy)
        content = result_data.get("result", result_data)
    elif format == "links":
        content = client.get_links(url, **kwargs)
    elif format == "json":
        # Route to /json endpoint for AI extraction
        content = client.extract_json(url, prompt="Extract the main content as structured data", **kwargs)
    elif format == "screenshot" or screenshot or full_page_screenshot:
        if full_page_screenshot:
            kwargs["full_page"] = True
        binary = client.take_screenshot(url, **kwargs)
        content = {
            "screenshot": base64.b64encode(binary).decode(),
            "encoding": "base64",
            "size": len(binary),
        }
    elif format == "images":
        from .extract import extract_images
        html = client.get_content(url, **kwargs)
        content = extract_images(html, url)
    elif format == "summary":
        if only_main_content or include_tags or exclude_tags:
            from .extract import extract_main_content as _mc
            from .extract import filter_tags as _ft
            from .extract import html_to_markdown as _md
            html = client.get_content(url, **kwargs)
            if only_main_content:
                html = _mc(html)
            if include_tags:
                html = _ft(html, include=include_tags)
            if exclude_tags:
                html = _ft(html, exclude=exclude_tags)
            text = _md(html)
            content = client.extract_json(
                url,
                prompt=f"Summarize this content in 2-3 concise paragraphs:\n\n{text[:8000]}",
                **kwargs,
            )
        else:
            content = client.extract_json(
                url,
                prompt="Summarize the main content in 2-3 concise paragraphs. Focus on key takeaways.",
                **kwargs,
            )
    elif format == "schema":
        from .extract import extract_structured_data
        html = client.get_content(url, **kwargs)
        content = extract_structured_data(html)
    elif format == "accessibility":
        from .extract import extract_accessibility_tree
        html = client.get_content(url, **kwargs)
        content = extract_accessibility_tree(html)
    elif format == "html":
        if _scroll_script:
            body = client._build_body(url=url, **kwargs)
            body.setdefault("addScriptTag", []).append({"content": _scroll_script})
            result_data = client._post_json("content", body)
            content = result_data.get("result", "")
        else:
            content = client.get_content(url, **kwargs)
    else:
        if _scroll_script:
            body = client._build_body(url=url, **kwargs)
            body.setdefault("addScriptTag", []).append({"content": _scroll_script})
            result_data = client._post_json("markdown", body)
            content = result_data.get("result", "")
        else:
            content = client.get_markdown(url, **kwargs)

    # Archived fallback: if content is empty/error and --archived, try Wayback Machine
    if archived and not _archive_attempted:
        is_empty = (isinstance(content, str) and len(content.strip()) < 50)
        is_404 = (isinstance(content, str) and "404" in content[:200] and "not found" in content[:500].lower())
        if is_empty or is_404:
            _archive_attempted = True
            wb_url = f"https://web.archive.org/web/{url}"
            try:
                if format == "html":
                    content = client.get_content(wb_url, **kwargs)
                else:
                    content = client.get_markdown(wb_url, **kwargs)
            except FlareCrawlError:
                pass  # Keep original content

    # Post-processing: main content extraction and tag filtering
    if isinstance(content, str) and (only_main_content or precision or recall or include_tags or exclude_tags):
        from .extract import extract_main_content as _extract_main
        from .extract import extract_main_content_precision as _prec
        from .extract import extract_main_content_recall as _rec
        from .extract import filter_tags as _filter
        from .extract import html_to_markdown as _h2m
        # Need HTML for filtering
        if format not in ("html",):
            html = client.get_content(url, **kwargs)
        else:
            html = content

        if precision:
            html = _prec(html)
        elif recall:
            html = _rec(html)
        elif only_main_content:
            html = _extract_main(html)

        if include_tags:
            html = _filter(html, include=include_tags)
        if exclude_tags:
            html = _filter(html, exclude=exclude_tags)

        content = _h2m(html) if format == "markdown" else html

    # Post-processing: relevance filter
    if query and isinstance(content, str):
        from .extract import filter_by_query
        content = filter_by_query(content, query)

    # Post-processing: clean ad/nav cruft from markdown output
    if isinstance(content, str) and format == "markdown":
        from .extract import clean_content
        content = clean_content(content)

    elapsed = _time.time() - start
    result = {"url": url, "content": content, "elapsed": round(elapsed, 2)}

    # Extract metadata from content (zero extra API calls)
    metadata = {}
    if isinstance(content, str):
        # Extract title from first markdown heading
        title_match = re.search(r"^#{1,2}\s+(.+?)$", content, re.MULTILINE)
        if title_match:
            metadata["title"] = title_match.group(1).strip()
        metadata["contentLength"] = len(content)
        # Word count (split on whitespace)
        metadata["wordCount"] = len(content.split())
        # Heading count
        metadata["headingCount"] = len(re.findall(r"^#{1,6}\s+", content, re.MULTILINE))
        # Link count
        metadata["linkCount"] = len(re.findall(r"\[.*?\]\(.*?\)", content))
        # Description (first non-heading, non-empty paragraph)
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("[") and len(stripped) > 20:
                metadata["description"] = stripped[:200]
                break
    elif isinstance(content, list):
        metadata["count"] = len(content)
    metadata["source"] = "browser-rendering"
    metadata["sourceURL"] = url
    metadata["browserTimeMs"] = client.browser_ms_used
    metadata["format"] = format
    metadata["elapsed"] = result["elapsed"]
    metadata["cacheHit"] = client.browser_ms_used == 0 and result["elapsed"] < 2
    result["metadata"] = metadata

    return result


@app.command()
def scrape(
    urls: Annotated[list[str], typer.Argument(help="URL(s) to scrape")] = None,
    format: Annotated[
        str,
        typer.Option("--format", "-f", help="markdown html links screenshot json images summary schema accessibility"),
    ] = "markdown",
    wait_for: Annotated[int | None, typer.Option("--wait-for", help="Wait time in ms")] = None,
    wait_until: Annotated[str | None, typer.Option("--wait-until", help="Page load event: load, domcontentloaded, networkidle0, networkidle2")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    screenshot: Annotated[bool, typer.Option("--screenshot", help="Take screenshot")] = False,
    full_page_screenshot: Annotated[bool, typer.Option("--full-page-screenshot", help="Full page screenshot")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file path")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    timing: Annotated[bool, typer.Option("--timing", help="Show timing info")] = False,
    timeout: Annotated[int | None, typer.Option("--timeout", help="Request timeout in ms")] = None,
    fields: Annotated[str | None, typer.Option("--fields", help="Comma-separated fields to include in JSON")] = None,
    input_file: Annotated[Path | None, typer.Option("--input", "-i", help="File with URLs (one per line)")] = None,
    batch: Annotated[Path | None, typer.Option("--batch", "-b", help="Batch input file (JSON array, NDJSON, or text)")] = None,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for batch (max 10)")] = 3,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body (overrides all flags)")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    js: Annotated[bool, typer.Option("--js", help="Wait for JS rendering (networkidle0, slower but captures dynamic content)")] = False,  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501  # noqa: E501
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    mobile: Annotated[bool, typer.Option("--mobile", help="Emulate mobile device (iPhone 14 Pro viewport)")] = False,
    only_main_content: Annotated[bool, typer.Option("--only-main-content", help="Keep main content only")] = False,
    include_tags: Annotated[str | None, typer.Option("--include-tags", help="CSS selectors to keep")] = None,
    exclude_tags: Annotated[str | None, typer.Option("--exclude-tags", help="CSS selectors to remove")] = None,
    diff: Annotated[bool, typer.Option("--diff", help="Show diff against cached version")] = False,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    wait_for_selector: Annotated[str | None, typer.Option("--wait-for-selector", help="Wait for CSS selector")] = None,
    selector: Annotated[str | None, typer.Option("--selector", help="Extract content from CSS selector")] = None,
    js_expression: Annotated[str | None, typer.Option("--js-eval", help="Run JS expression, return result")] = None,
    stdin_mode: Annotated[bool, typer.Option("--stdin", help="Read HTML from stdin (no API call)")] = False,
    har_output: Annotated[Path | None, typer.Option("--har", help="Save request metadata to HAR file")] = None,
    backup_dir: Annotated[Path | None, typer.Option("--backup-dir", help="Save raw HTML to this directory")] = None,
    archived: Annotated[bool, typer.Option("--archived", help="Fallback to Internet Archive on 404/error")] = False,
    language: Annotated[str | None, typer.Option("--language", help="Accept-Language header (e.g. de, fr, ja)")] = None,
    magic: Annotated[bool, typer.Option("--magic", help="Remove cookie banners and overlays")] = False,
    scroll: Annotated[bool, typer.Option("--scroll", help="Auto-scroll page for lazy-loaded content")] = False,
    query: Annotated[str | None, typer.Option("--query", help="Filter content by relevance to query")] = None,
    precision: Annotated[bool, typer.Option("--precision", help="Aggressive content extraction")] = False,
    recall: Annotated[bool, typer.Option("--recall", help="Conservative content extraction")] = False,
    session: Annotated[Path | None, typer.Option("--session", help="Load cookies from session file")] = None,
    no_negotiate: Annotated[bool, typer.Option("--no-negotiate", help="Skip markdown content negotiation, force browser rendering")] = False,
    paywall: Annotated[bool, typer.Option("--paywall", help="Attempt paywall bypass cascade before browser rendering")] = False,
    stealth: Annotated[bool, typer.Option("--stealth", help="Use browser TLS fingerprint for direct HTTP requests (requires curl_cffi)")] = False,
):
    """Scrape one or more URLs. Default output is markdown.

    Multiple URLs are scraped concurrently. Use --batch for file input
    with NDJSON output and configurable workers. Responses are cached
    for 1 hour by default (use --no-cache to bypass).

    Example:
        flarecrawl scrape https://example.com
        flarecrawl scrape https://example.com --format html --json
        flarecrawl scrape https://a.com https://b.com --json
        flarecrawl scrape --batch urls.txt --workers 5
        flarecrawl scrape --only-main-content --json
        flarecrawl scrape --exclude-tags "nav,footer" --json
        flarecrawl scrape --format images --json
        flarecrawl scrape --format schema --json
    """
    # Stdin mode: process local HTML without API call
    if stdin_mode:
        from .extract import (
            extract_images,
            extract_main_content,
            extract_structured_data,
            filter_tags,
            html_to_markdown,
        )
        html = sys.stdin.read()
        if only_main_content:
            html = extract_main_content(html)
        if include_tags:
            html = filter_tags(html, include=[s.strip() for s in include_tags.split(",")])
        if exclude_tags:
            html = filter_tags(html, exclude=[s.strip() for s in exclude_tags.split(",")])
        if format == "images":
            content = extract_images(html, "")
        elif format == "schema":
            content = extract_structured_data(html)
        elif format == "html":
            content = html
        else:
            content = html_to_markdown(html)
        result = {"url": "(stdin)", "content": content}
        if json_output:
            _output_json({"data": result, "meta": {"format": format, "source": "stdin"}})
        elif isinstance(content, str):
            _output_text(content)
        else:
            _output_json(content)
        return

    # Validate --batch and --input are not both provided
    if batch and input_file:
        _error(
            "Cannot use both --batch and --input. Use --batch (preferred).",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    # Validate --include-tags and --exclude-tags are not both provided
    if include_tags and exclude_tags:
        _error(
            "Cannot use both --include-tags and --exclude-tags.",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    # Parse tag lists
    _include = [s.strip() for s in include_tags.split(",")] if include_tags else None
    _exclude = [s.strip() for s in exclude_tags.split(",")] if exclude_tags else None

    # Validate --precision and --recall are not both provided
    if precision and recall:
        _error(
            "Cannot use both --precision and --recall.",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    # Load session cookies (after auth_dict is parsed below)
    _session_cookies = None
    if session:
        try:
            session_data = json.loads(session.read_text())
            _session_cookies = session_data if isinstance(session_data, list) else session_data.get("cookies", [])
        except (OSError, json.JSONDecodeError) as e:
            _error(f"Cannot read session file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    # Resolve batch file (--batch takes precedence, --input is backward compat)
    batch_file = batch or input_file
    is_batch_mode = batch is not None

    # --js implies networkidle0 (unless --wait-until explicitly set)
    if js and not wait_until:
        wait_until = "networkidle0"

    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    # Defer auth when --paywall is set: bypass uses direct HTTP, not CF API.
    # Client is created only if credentials exist (needed as fallback).
    if paywall:
        _has_creds = get_account_id() and get_api_token()
        client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl) if _has_creds else None
    else:
        client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl)
    raw_body = _parse_body(body, json_output or is_batch_mode)
    auth_dict = _parse_auth(auth, json_output or is_batch_mode)
    custom_headers = _parse_headers(headers, json_output or is_batch_mode)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    # Language: set Accept-Language header
    if language:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        existing.setdefault("Accept-Language", language)
        auth_dict["extra_headers"] = existing

    # Apply session cookies
    if _session_cookies:
        if auth_dict is None:
            auth_dict = {}
        auth_dict["cookies"] = _session_cookies

    # Load URLs
    all_urls = list(urls or [])
    if batch_file:
        try:
            file_urls = parse_batch_file(batch_file)
            # parse_batch_file returns strings for plain text, ensure we have URL strings
            all_urls.extend(str(u) for u in file_urls)
        except OSError as e:
            _error(f"Cannot read file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output or is_batch_mode)

    if not all_urls:
        _error(
            "Provide at least one URL as argument or via --batch/--input.",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output or is_batch_mode,
        )

    for url in all_urls:
        _validate_url(url, json_output or is_batch_mode)

    # Build negotiate headers from auth/custom headers for content negotiation
    _neg_headers = {}
    if auth_dict and "extra_headers" in auth_dict:
        _neg_headers.update(auth_dict["extra_headers"])
    if language:
        _neg_headers["Accept-Language"] = language

    # ------------------------------------------------------------------
    # Batch mode: asyncio + NDJSON output
    # ------------------------------------------------------------------
    if is_batch_mode:
        capped_workers = min(workers, DEFAULT_MAX_WORKERS)

        # Shared sessions for batch mode (connection reuse)
        from .negotiate import get_negotiate_session
        _neg_session = get_negotiate_session() if not no_negotiate else None
        from .paywall import get_paywall_session
        _pw_session = get_paywall_session() if paywall else None

        async def _scrape_one(url: str) -> dict:
            return await asyncio.to_thread(
                _scrape_single, client, url, format, wait_for,
                screenshot, full_page_screenshot, raw_body, timeout,
                wait_until, auth_dict, mobile,
                only_main_content, _include, _exclude, user_agent,
                wait_for_selector, selector, js_expression,
                archived, magic, scroll, query, precision, recall,
                no_negotiate, _neg_headers or None, _neg_session,
                paywall, _pw_session, stealth,
            )

        def _on_progress(completed: int, total: int, errors: int):
            console.print(f"[dim]{completed}/{total} (errors: {errors})[/dim]")

        console.print(f"[dim]Scraping {len(all_urls)} URLs with {capped_workers} workers...[/dim]")
        try:
            results = asyncio.run(
                process_batch(all_urls, _scrape_one, workers=capped_workers, on_progress=_on_progress)
            )
        finally:
            if _neg_session:
                _neg_session.close()
            if _pw_session:
                _pw_session.close()

        has_errors = any(r["status"] == "error" for r in results)
        for r in sorted(results, key=lambda x: x["index"]):
            _output_ndjson(r)

        errors = sum(1 for r in results if r["status"] == "error")
        console.print(f"[dim]Done: {len(results) - errors} ok, {errors} errors[/dim]")
        if has_errors:
            raise typer.Exit(EXIT_ERROR)
        return

    # ------------------------------------------------------------------
    # Non-batch: existing behavior
    # ------------------------------------------------------------------

    # Single URL: binary screenshot can go to stdout/file directly
    if len(all_urls) == 1 and (format == "screenshot" or screenshot or full_page_screenshot) and not json_output:
        url = all_urls[0]
        kwargs = {}
        if full_page_screenshot:
            kwargs["full_page"] = True
        if wait_for:
            kwargs["timeout"] = wait_for
        if timeout:
            kwargs["timeout"] = timeout
        if mobile:
            kwargs.update(MOBILE_PRESET)
        if auth_dict:
            kwargs.update(auth_dict)
        if user_agent:
            kwargs["user_agent"] = user_agent
        try:
            binary = client.take_screenshot(url, **kwargs)
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return
        if output:
            output.write_bytes(binary)
            console.print(f"Screenshot saved: {output}")
        else:
            sys.stdout.buffer.write(binary)
        return

    # Concurrent scraping for multiple URLs
    results = []
    if len(all_urls) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, DEFAULT_MAX_WORKERS)) as pool:
            future_to_url = {
                pool.submit(
                    _scrape_single, client, url, format, wait_for,
                    screenshot, full_page_screenshot, raw_body, timeout,
                    wait_until, auth_dict, mobile,
                    only_main_content, _include, _exclude, user_agent,
                    wait_for_selector, selector, js_expression,
                    archived, magic, scroll, query, precision, recall,
                    no_negotiate, _neg_headers or None, None,
                    paywall, None, stealth,
                ): url
                for url in all_urls
            }
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    if timing:
                        console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
                    results.append(result)
                except FlareCrawlError as e:
                    console.print(f"[red]Failed:[/red] {url}: {e}")
                    results.append({"url": url, "error": str(e)})
        # Sort by original URL order
        url_order = {u: i for i, u in enumerate(all_urls)}
        results.sort(key=lambda r: url_order.get(r.get("url", ""), 0))
    else:
        # Single URL
        url = all_urls[0]
        try:
            result = _scrape_single(client, url, format, wait_for, screenshot,
                                    full_page_screenshot, raw_body, timeout,
                                    wait_until=wait_until,
                                    auth_kwargs=auth_dict,
                                    mobile=mobile,
                                    only_main_content=only_main_content,
                                    include_tags=_include,
                                    exclude_tags=_exclude,
                                    user_agent=user_agent,
                                    wait_for_selector=wait_for_selector,
                                    css_selector=selector,
                                    js_expression=js_expression,
                                    archived=archived,
                                    magic=magic,
                                    scroll=scroll,
                                    query=query,
                                    precision=precision,
                                    recall=recall,
                                    no_negotiate=no_negotiate,
                                    negotiate_headers=_neg_headers or None,
                                    paywall=paywall,
                                    stealth=stealth)
            if timing:
                console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
            results.append(result)
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return

    # Show browser time if timing enabled
    if timing and client and client.browser_ms_used:
        console.print(f"[dim]Browser time: {client.browser_ms_used}ms[/dim]")

    # Diff mode: compare against cached version
    if diff and results:
        import difflib

        from . import cache as _cache
        for r in results:
            content_str = r.get("content", "")
            if not isinstance(content_str, str):
                content_str = json.dumps(content_str, indent=2)
            endpoint = "markdown" if format == "markdown" else "content"
            cache_body = {"url": r.get("url", ""), "format": format}
            cached = _cache.get(endpoint + ":diff", cache_body, ttl=86400 * 30)
            if cached:
                old_lines = cached.splitlines(keepends=True)
                new_lines = content_str.splitlines(keepends=True)
                diff_text = "".join(difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile="cached", tofile="current", lineterm="",
                ))
                added = sum(1 for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
                removed = sum(1 for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
                r["diff"] = {"added": added, "removed": removed, "diff": diff_text}
            else:
                r["diff"] = {"added": 0, "removed": 0, "diff": "(no cached version to compare)"}
            # Store current version for next diff
            _cache.put(endpoint + ":diff", cache_body, content_str)

    # Backup: save raw HTML alongside output
    if backup_dir and results and client:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            page_url = r.get("url", "")
            if not page_url:
                continue
            try:
                html = client.get_content(page_url)
                filename = _sanitize_filename(page_url) + ".html"
                (backup_dir / filename).write_text(html, encoding="utf-8")
            except FlareCrawlError:
                pass
        console.print(f"[dim]HTML backup saved to {backup_dir}/[/dim]")

    # HAR capture: save request metadata
    if har_output and results:
        from datetime import datetime
        har_data = {
            "log": {
                "version": "1.2",
                "creator": {"name": "flarecrawl", "version": __version__},
                "entries": [
                    {
                        "startedDateTime": datetime.now(UTC).isoformat(),
                        "request": {"method": "POST", "url": r.get("url", "")},
                        "response": {
                            "status": 200,
                            "content": {
                                "size": len(r.get("content", "")) if isinstance(r.get("content"), str) else 0,
                                "mimeType": "text/html",
                            },
                        },
                        "time": int(r.get("elapsed", 0) * 1000),
                    }
                    for r in results
                ],
            }
        }
        har_output.write_text(json.dumps(har_data, indent=2), encoding="utf-8")
        console.print(f"[dim]HAR saved: {har_output} ({len(results)} entries)[/dim]")

    # Handle paywall bypass failure (error dict instead of content)
    if len(results) == 1 and "error" in results[0] and "content" not in results[0]:
        err_msg = results[0]["error"]
        if json_output:
            _output_json({"error": {"code": "PAYWALL_BYPASS_FAILED", "message": err_msg}})
        else:
            console.print(f"[red]Error:[/red] {err_msg}")
        raise typer.Exit(EXIT_ERROR)

    # Output
    if json_output:
        data = results if len(results) > 1 else results[0]
        if fields:
            data = _filter_fields(data, fields)
        meta = {"format": format}
        if len(results) > 1:
            meta["count"] = len(results)
        # Surface metadata from scrape results
        if len(results) == 1 and "metadata" in results[0]:
            meta.update(results[0]["metadata"])
        _output_json({"data": data, "meta": meta})
    elif output:
        out_content = "\n\n".join(
            r.get("content", "") if isinstance(r.get("content"), str) else json.dumps(r.get("content", ""), indent=2)
            for r in results if "content" in r
        )
        output.write_text(out_content, encoding="utf-8")
        console.print(f"Saved to {output}")
    else:
        for r in results:
            content = r.get("content", "")
            if isinstance(content, str):
                _output_text(content)
            else:
                _output_json(content)


# ------------------------------------------------------------------
# crawl — matches firecrawl crawl
# ------------------------------------------------------------------


@app.command()
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
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    deduplicate: Annotated[bool, typer.Option("--deduplicate", help="Skip duplicate content")] = False,
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
        if user_agent:
            kwargs["user_agent"] = user_agent

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
                record = _filter_record_content(record, only_main_content, _inc, _exc)
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
            r = _filter_record_content(r, only_main_content, _inc, _exc)
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
            console.print(f"[dim]Webhook: POST {webhook} → {resp.status_code}[/dim]")
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
# map — matches firecrawl map
# ------------------------------------------------------------------


@app.command("map")
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
# download — matches firecrawl download
# ------------------------------------------------------------------


@app.command()
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
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    backup_dir: Annotated[Path | None, typer.Option("--backup-dir", help="Save raw HTML to this directory")] = None,
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
    if user_agent:
        kwargs["user_agent"] = user_agent

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
        record = _filter_record_content(record, only_main_content, _inc, _exc)
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
# extract — matches firecrawl agent
# ------------------------------------------------------------------


@app.command()
def extract(
    prompt: Annotated[str, typer.Argument(help="Natural language prompt for extraction")],
    urls: Annotated[str | None, typer.Option("--urls", help="Comma-separated URLs")] = None,
    schema: Annotated[str | None, typer.Option("--schema", help="JSON schema (inline string)")] = None,
    schema_file: Annotated[Path | None, typer.Option("--schema-file", help="Path to JSON schema file")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    batch: Annotated[Path | None, typer.Option("--batch", "-b", help="Batch input file with URLs")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for batch (max 10)")] = 3,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
):
    """AI-powered structured data extraction from web pages.

    Uses Cloudflare Workers AI to extract structured data based on a prompt.
    Use --batch for parallel extraction with NDJSON output.

    Example:
        flarecrawl extract "Extract all product names and prices" --urls https://shop.example.com --json
        flarecrawl extract "Get article title and date" --urls https://blog.example.com --schema-file schema.json
        flarecrawl extract "Get page title" --batch urls.txt --workers 5
        flarecrawl extract "Get credentials" --urls https://intranet.example.com --auth user:pass --json
    """
    is_batch_mode = batch is not None
    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl)
    raw_body = _parse_body(body, json_output or is_batch_mode)
    auth_dict = _parse_auth(auth, json_output or is_batch_mode)
    custom_headers = _parse_headers(headers, json_output or is_batch_mode)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    # Parse URLs from --urls flag
    url_list = []
    if urls:
        url_list = [u.strip() for u in urls.split(",")]

    # Load URLs from --batch file
    if batch:
        try:
            batch_urls = parse_batch_file(batch)
            url_list.extend(str(u) for u in batch_urls)
        except OSError as e:
            _error(f"Cannot read batch file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=True)

    if not url_list and not raw_body:
        _error(
            "Provide at least one URL with --urls or --batch",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output or is_batch_mode,
        )

    # Parse schema
    response_format = None
    if schema_file:
        try:
            response_format = json.loads(schema_file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            _error(f"Invalid schema file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output or is_batch_mode)
    elif schema:
        try:
            response_format = json.loads(schema)
        except json.JSONDecodeError as e:
            _error(f"Invalid --schema JSON: {e}", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output or is_batch_mode)

    target_urls = url_list if not raw_body else [raw_body.get("url", "")]

    for url in target_urls:
        _validate_url(url, json_output or is_batch_mode)

    # ------------------------------------------------------------------
    # Batch mode: asyncio + NDJSON output
    # ------------------------------------------------------------------
    if is_batch_mode:
        capped_workers = min(workers, DEFAULT_MAX_WORKERS)

        extra_kwargs = {}
        if auth_dict:
            extra_kwargs.update(auth_dict)
        if user_agent:
            extra_kwargs["user_agent"] = user_agent

        async def _extract_one(url: str) -> dict:
            return await asyncio.to_thread(
                client.extract_json, url, prompt, response_format, **extra_kwargs,
            )

        def _on_progress(completed: int, total: int, errors: int):
            console.print(f"[dim]{completed}/{total} (errors: {errors})[/dim]")

        console.print(f"[dim]Extracting from {len(target_urls)} URLs with {capped_workers} workers...[/dim]")
        results = asyncio.run(
            process_batch(target_urls, _extract_one, workers=capped_workers, on_progress=_on_progress)
        )

        has_errors = any(r["status"] == "error" for r in results)
        for r in sorted(results, key=lambda x: x["index"]):
            _output_ndjson(r)

        error_count = sum(1 for r in results if r["status"] == "error")
        console.print(f"[dim]Done: {len(results) - error_count} ok, {error_count} errors[/dim]")
        if has_errors:
            raise typer.Exit(EXIT_ERROR)
        return

    # ------------------------------------------------------------------
    # Non-batch: existing sequential behavior
    # ------------------------------------------------------------------
    results = []
    for url in target_urls:
        try:
            if raw_body:
                raw_body.setdefault("url", url)
                result = client.post_raw("json", raw_body)
                extracted = result.get("result", result)
            else:
                extra = auth_dict if auth_dict else {}
                extracted = client.extract_json(url, prompt, response_format, **extra)
            results.append({"url": url, "data": extracted})
        except FlareCrawlError as e:
            if len(target_urls) == 1:
                _handle_api_error(e, json_output)
                return
            results.append({"url": url, "error": str(e)})

    if output:
        output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        console.print(f"Saved to {output}")
    elif json_output:
        if len(results) == 1:
            _output_json({"data": results[0], "meta": {}})
        else:
            _output_json({"data": results, "meta": {"count": len(results)}})
    else:
        _output_json(results)


# ------------------------------------------------------------------
# screenshot — convenience command
# ------------------------------------------------------------------


@app.command()
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
# pdf — bonus command (CF has this, firecrawl doesn't)
# ------------------------------------------------------------------


@app.command()
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
# favicon — extract favicon URL
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
            # Parse size to integer for sorting (e.g., "192x192" → 192)
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


@app.command()
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
        # Reject images/media/fonts to speed up — we only need HTML
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
# batch — YAML config batch operations
# ------------------------------------------------------------------


@app.command("batch")
def batch_config(
    config_file: Annotated[Path, typer.Argument(help="YAML config file")],
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers")] = 3,
):
    """Run batch operations from a YAML config file.

    Config format (list of scrape jobs):
        - url: https://example.com
          format: markdown
          output: example.md
        - url: https://other.com
          format: images
          selector: main
          json: true

    Example:
        flarecrawl batch config.yml
        flarecrawl batch config.yml --workers 5
    """
    try:
        import yaml
    except ImportError:
        _error("PyYAML required for batch config. Install: pip install pyyaml",
               "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    try:
        jobs = yaml.safe_load(config_file.read_text())
    except (OSError, yaml.YAMLError) as e:
        _error(f"Cannot read config: {e}", "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    if not isinstance(jobs, list):
        _error("Config must be a YAML list of jobs", "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    client = _get_client(True)

    console.print(f"[dim]Running {len(jobs)} jobs from {config_file}...[/dim]")

    for i, job in enumerate(jobs):
        if not isinstance(job, dict) or "url" not in job:
            console.print(f"[yellow]Job {i}: missing 'url', skipping[/yellow]")
            continue

        url = job["url"]
        fmt = job.get("format", "markdown")
        out_file = job.get("output")

        console.print(f"[dim]{i + 1}/{len(jobs)} {url} ({fmt})[/dim]")

        try:
            result = _scrape_single(
                client, url, fmt,
                wait_for=None, screenshot=False, full_page_screenshot=False,
                raw_body=None, timeout_ms=job.get("timeout"),
                wait_until=job.get("wait_until"),
                css_selector=job.get("selector"),
                only_main_content=job.get("only_main_content", False),
            )

            content = result.get("content", "")

            if out_file:
                Path(out_file).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    Path(out_file).write_text(content, encoding="utf-8")
                else:
                    Path(out_file).write_text(
                        json.dumps(content, indent=2, default=str), encoding="utf-8"
                    )
                console.print(f"  [green]Saved: {out_file}[/green]")
            elif job.get("json"):
                _output_ndjson({"index": i, "status": "ok", "data": result})
            else:
                if isinstance(content, str):
                    _output_text(content)
                else:
                    _output_json(content)

        except FlareCrawlError as e:
            console.print(f"  [red]Error: {e}[/red]")
            if job.get("json"):
                _output_ndjson({"index": i, "status": "error", "error": str(e)})

    console.print(f"[dim]Batch complete: {len(jobs)} jobs[/dim]")


# ------------------------------------------------------------------
# discover — feed/sitemap/link discovery
# ------------------------------------------------------------------


@app.command()
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
):
    """Discover all URLs on a site via sitemaps, RSS feeds, and page links.

    Combines XML sitemap parsing, RSS/Atom feed discovery, and page link
    extraction into a single unified URL list.

    Example:
        flarecrawl discover https://example.com --json
        flarecrawl discover https://example.com --sitemap --no-feed --no-links
        flarecrawl discover https://example.com --limit 100
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
        from bs4 import BeautifulSoup
        # CF renders XML as HTML — use BS to extract text content of <loc> tags
        soup = BeautifulSoup(html_or_xml, "lxml")
        pages, sub_sitemaps = [], []
        for loc in soup.find_all("loc"):
            text = loc.get_text(strip=True)
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
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            feed_urls = []
            # Find <link> tags with RSS/Atom types
            for link_tag in soup.find_all("link"):
                link_type = (link_tag.get("type") or "").lower()
                if "rss" in link_type or "atom" in link_type:
                    href = link_tag.get("href")
                    if href:
                        feed_urls.append(urljoin(url, href))
            # Also try common feed paths
            for feed_path in ["/feed", "/rss", "/atom.xml", "/feed.xml", "/rss.xml",
                              "/feed/", "/rss/", "/index.xml"]:
                feed_urls.append(f"{base}{feed_path}")

            for feed_url in dict.fromkeys(feed_urls):  # dedupe, preserve order
                try:
                    feed_html = client.get_content(feed_url, **kwargs)
                    # CF renders XML as HTML — use BS to find link elements
                    feed_soup = BeautifulSoup(feed_html, "lxml")
                    # RSS: <item><link>URL</link></item>
                    for item in feed_soup.find_all("item"):
                        link_el = item.find("link")
                        if link_el:
                            href = link_el.get_text(strip=True) or link_el.next_sibling
                            if href and isinstance(href, str) and href.strip().startswith("http"):
                                discovered.setdefault(href.strip(), "feed")
                    # Atom: <entry><link href="URL"/></entry>
                    for entry in feed_soup.find_all("entry"):
                        for link_el in entry.find_all("link"):
                            href = link_el.get("href")
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
        _output_json({"data": data, "meta": meta})
    else:
        for u, s in all_urls:
            _output_text(f"{u}  [{s}]")
        console.print(f"\n[dim]Total: {len(all_urls)} URLs[/dim]")


# ------------------------------------------------------------------
# schema — structured data extraction
# ------------------------------------------------------------------


@app.command()
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
    from .extract import extract_structured_data

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
# usage — browser time tracking
# ------------------------------------------------------------------


@app.command()
def usage(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show browser rendering time usage (tracked locally).

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


if __name__ == "__main__":
    app()
