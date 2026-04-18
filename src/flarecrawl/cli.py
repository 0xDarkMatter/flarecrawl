"""Flarecrawl CLI - Firecrawl-compatible CLI backed by Cloudflare Browser Run."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import re
import sys
import time as _time

# Optional: install uvloop on non-Windows platforms for 2-4x async speedup.
# No-op on Windows (uvloop is not supported there) or if uvloop is not installed.
if sys.platform != "win32":
    try:  # pragma: no cover - platform-specific bootstrap
        import uvloop

        uvloop.install()
    except ImportError:
        pass
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any
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

app = typer.Typer(
    name="flarecrawl",
    help="Cloudflare Browser Run CLI — drop-in firecrawl replacement, much cheaper.",
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
    """Parse --auth user:pass into auth kwargs for CF Browser Run API.

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
    agent_safe: bool = False,
) -> dict:
    """Apply content filtering to a crawl/download record in-place."""
    if not (only_main_content or include_tags or exclude_tags or agent_safe):
        return record
    _record_findings: list = []
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
            if agent_safe:
                from .sanitise import sanitise_html
                _html_san = sanitise_html(html)
                html = _html_san.content
                _record_findings.extend(_html_san.findings)
            if key == "html":
                record[key] = html
            else:
                record[key] = html_to_markdown(html)
        if agent_safe and key == "markdown":
            md_content = record.get(key)
            if md_content and isinstance(md_content, str):
                from .sanitise import sanitise_text, SanitiseResult
                _text_san = sanitise_text(md_content)
                record[key] = _text_san.content
                _record_findings.extend(_text_san.findings)
        if agent_safe and _record_findings:
            _combined = SanitiseResult(content="", findings=_record_findings)
            meta = record.get("metadata") or {}
            meta["agentSafety"] = _combined.to_metadata()
            record["metadata"] = meta
    return record


def _get_client(as_json: bool = False, cache_ttl: int = 3600, proxy: str | None = None) -> Client:
    """Get authenticated client."""
    _require_auth(as_json)
    return Client(cache_ttl=cache_ttl, proxy=proxy)


def _get_cdp_client(
    as_json: bool = False,
    keep_alive: int = 0,
    recording: bool = False,
    proxy: str | None = None,
) -> "CDPClient":
    """Create and connect a CDP WebSocket client."""
    try:
        from .cdp import CDPClient
    except ImportError:
        _error(
            "CDP requires the 'websockets' package. Install with: uv pip install websockets",
            "MISSING_DEPENDENCY", EXIT_ERROR, as_json=as_json,
        )

    from .config import get_proxy
    account_id = get_account_id()
    api_token = get_api_token()
    if not account_id or not api_token:
        _error("Not authenticated. Run: flarecrawl auth login", "AUTH_REQUIRED", EXIT_AUTH_REQUIRED, as_json=as_json)

    effective_proxy = proxy or get_proxy()
    client = CDPClient(account_id=account_id, api_token=api_token)
    client.connect(keep_alive=keep_alive, recording=recording)
    return client


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
    """Cloudflare Browser Run CLI — drop-in firecrawl replacement."""


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
    """Authenticate with Cloudflare Browser Run.

    Opens the Cloudflare dashboard in your browser to create a token,
    then prompts for your account ID and token.

    Example:
        flarecrawl auth login
        flarecrawl auth login --account-id abc123 --token cftoken
    """
    import webbrowser

    if not account_id or not token:
        console.print("\n[bold]Cloudflare Browser Run Setup[/bold]\n")

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
# rules — per-site header rulesets
# ------------------------------------------------------------------

rules_app = typer.Typer(help="Per-site header rulesets for enhanced extraction")
app.add_typer(rules_app, name="rules")


@rules_app.command("list")
def rules_list(
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """List all loaded rules (defaults + user overrides)."""
    from .rules import list_rules
    rules = list_rules()
    if json_output:
        _output_json({"data": rules, "meta": {"count": len(rules)}})
    else:
        if not rules:
            console.print("[dim]No rules loaded[/dim]")
            return
        for domain, headers in sorted(rules.items()):
            console.print(f"[bold]{domain}[/bold]")
            for k, v in headers.items():
                console.print(f"  {k}: {v}")


@rules_app.command("show")
def rules_show(
    domain: Annotated[str, typer.Argument(help="Domain to look up")],
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Show headers for a specific domain."""
    from .rules import load_rules
    rules = load_rules()
    headers = rules.get(domain, {})
    if json_output:
        _output_json({"data": {"domain": domain, "headers": headers}})
    elif headers:
        console.print(f"[bold]{domain}[/bold]")
        for k, v in headers.items():
            console.print(f"  {k}: {v}")
    else:
        console.print(f"[dim]No rules for {domain}[/dim]")


@rules_app.command("add")
def rules_add(
    domain: Annotated[str, typer.Argument(help="Domain (e.g. www.example.com)")],
    referer: Annotated[str | None, typer.Option("--referer", help="Referer header")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="User-Agent header")] = None,
    cookie: Annotated[str | None, typer.Option("--cookie", help="Cookie header")] = None,
):
    """Add or update a rule in user rules.yaml."""
    from .rules import _user_rules_path, _parse_yaml, clear_cache

    headers = {}
    if referer:
        headers["Referer"] = referer
    if user_agent:
        headers["User-Agent"] = user_agent
    if cookie is not None:
        headers["Cookie"] = cookie

    if not headers:
        _error("Provide at least one header (--referer, --user-agent, --cookie)", "VALIDATION_ERROR", EXIT_VALIDATION)

    path = _user_rules_path()
    existing = _parse_yaml(path)

    # Update existing or append
    found = False
    for entry in existing:
        if entry.get("domain") == domain:
            entry["headers"] = {**entry.get("headers", {}), **headers}
            found = True
            break

    if not found:
        existing.append({"domain": domain, "headers": headers})

    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

    clear_cache()
    console.print(f"[green]Rule saved[/green] for {domain}")
    for k, v in headers.items():
        console.print(f"  {k}: {v}")


@rules_app.command("path")
def rules_path():
    """Show paths to default and user rules files."""
    from .rules import _default_rules_path, _user_rules_path
    console.print(f"Default: {_default_rules_path()}")
    console.print(f"User:    {_user_rules_path()}")


# ------------------------------------------------------------------
# scrape — matches firecrawl scrape
# ------------------------------------------------------------------


def _scrape_single_cdp(
    cdp_client: "CDPClient",
    url: str,
    format: str = "markdown",
    js_expression: str | None = None,
    wait_for_selector: str | None = None,
    selector: str | None = None,
    scroll: bool = False,
    full_page: bool = False,
    only_main_content: bool = False,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    agent_safe: bool = False,
    user_agent: str | None = None,
    timeout: int | None = None,
    har_output: Path | None = None,
    load_cookies: Path | None = None,
    save_cookies: Path | None = None,
    page: "SyncCDPPage | None" = None,
    skip_navigation: bool = False,
) -> dict:
    """Scrape a URL using CDP WebSocket connection.

    If *page* is provided, reuse the existing page instead of creating a new
    one (used by --interactive mode where the user has already navigated and
    authenticated). When *skip_navigation* is True, the URL navigation step is
    skipped — useful when the page is already on the target URL.
    """
    start = _time.time()

    own_page = page is None
    if own_page:
        page = cdp_client.new_page()
    try:
        collector = None
        if har_output:
            collector = page.enable_network()

        if load_cookies:
            cookies = json.loads(load_cookies.read_text(encoding="utf-8"))
            page.set_cookies(cookies)

        if not skip_navigation:
            wait_until = "networkidle0" if scroll else "load"
            page.navigate(url, wait_until=wait_until, timeout=timeout or 30000)

        if wait_for_selector:
            page.wait_for_selector(wait_for_selector, timeout=timeout or 30000)

        if scroll:
            page.scroll()

        if js_expression:
            result = page.evaluate(js_expression)
            elapsed = _time.time() - start
            return {"url": url, "content": str(result), "elapsed": round(elapsed, 2), "metadata": {"source": "cdp-evaluate"}}

        if format == "screenshot":
            data = page.screenshot(full_page=full_page)
            elapsed = _time.time() - start
            return {"url": url, "screenshot": base64.b64encode(data).decode(), "encoding": "base64", "format": "png", "size": len(data), "elapsed": round(elapsed, 2)}

        if format == "accessibility":
            nodes = page.get_accessibility_tree()
            elapsed = _time.time() - start
            return {"url": url, "content": nodes, "elapsed": round(elapsed, 2), "metadata": {"source": "cdp-accessibility"}}

        html = page.get_content()

        from .extract import (
            extract_images,
            extract_main_content,
            html_to_markdown,
        )

        if selector:
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            el = tree.css_first(selector)
            if el:
                html = el.html or html

        if format == "html":
            content = html
        elif format == "links":
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            content = [a.attributes.get("href") for a in tree.css("a[href]")]
        elif format == "images":
            content = extract_images(html, url)
        else:
            content = html_to_markdown(html)
            if only_main_content:
                content = extract_main_content(content)

        if agent_safe and isinstance(content, str):
            from .sanitise import sanitise as _sanitise_fn
            san_result = _sanitise_fn(content, html=html)
            content = san_result.text

        if save_cookies:
            cookies = page.get_cookies()
            save_cookies.write_text(json.dumps(cookies, indent=2), encoding="utf-8")

        if collector and har_output:
            har_data = collector.to_har()
            har_output.write_text(json.dumps(har_data, indent=2), encoding="utf-8")

        metadata: dict[str, Any] = {"source": "cdp"}
        if isinstance(content, str):
            metadata["contentLength"] = len(content)
            metadata["wordCount"] = len(content.split())
        metadata["sourceURL"] = url
        elapsed = _time.time() - start

        return {"url": url, "content": content, "elapsed": round(elapsed, 2), "metadata": metadata}
    finally:
        if own_page:
            page.close()


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
                   stealth: bool = False,
                   clean: bool = False,
                   proxy: str | None = None,
                   agent_safe: bool = False) -> dict:
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
            _agent_safety_meta = None
            if agent_safe:
                from .sanitise import sanitise_text as _sanitise_text
                _san = _sanitise_text(content)
                content = _san.content
                _agent_safety_meta = _san.to_metadata()

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
            if agent_safe and _agent_safety_meta:
                metadata["agentSafety"] = _agent_safety_meta
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
            _agent_safety_meta_pw = None
            if agent_safe:
                from .sanitise import sanitise_text as _sanitise_text_pw
                _san_pw = _sanitise_text_pw(content)
                content = _san_pw.content
                _agent_safety_meta_pw = _san_pw.to_metadata()

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
            if agent_safe and _agent_safety_meta_pw:
                metadata["agentSafety"] = _agent_safety_meta_pw
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
    _agent_findings: list = []
    if isinstance(content, str) and (only_main_content or precision or recall or include_tags or exclude_tags or agent_safe):
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

        if agent_safe:
            from .sanitise import sanitise_html as _sanitise_html
            _html_san = _sanitise_html(html)
            html = _html_san.content
            _agent_findings = list(_html_san.findings)

        content = _h2m(html) if format == "markdown" else html
    elif agent_safe and isinstance(content, str) and format == "html":
        # No extraction block ran, but we have HTML — sanitise it directly
        from .sanitise import sanitise_html as _sanitise_html_raw
        _html_san_raw = _sanitise_html_raw(content)
        content = _html_san_raw.content
        _agent_findings = list(_html_san_raw.findings)

    # Post-processing: relevance filter
    if query and isinstance(content, str):
        from .extract import filter_by_query
        content = filter_by_query(content, query)

    # Post-processing: clean ad/nav cruft
    if isinstance(content, str) and format == "markdown":
        from .extract import clean_content
        content = clean_content(content)
    if clean and isinstance(content, str) and format in ("html",):
        from .extract import clean_html
        content = clean_html(content)

    # Agent safety: text-level sanitisation (phase 2)
    _agent_safety_meta_br = None
    if agent_safe and isinstance(content, str):
        from .sanitise import SanitiseResult as _SanitiseResult
        from .sanitise import sanitise_text as _sanitise_text_br
        _text_san = _sanitise_text_br(content)
        content = _text_san.content
        _all_findings = _agent_findings + _text_san.findings
        _combined = _SanitiseResult(content=content, findings=_all_findings)
        _agent_safety_meta_br = _combined.to_metadata()

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
    if agent_safe and _agent_safety_meta_br:
        metadata["agentSafety"] = _agent_safety_meta_br
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
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for batch (max 50, free tier: 3)")] = 3,
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
    clean: Annotated[bool, typer.Option("--clean", help="Strip ads/promos from HTML output")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL (http/https/socks5)")] = None,
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
    cdp: Annotated[bool, typer.Option("--cdp", help="Use CDP WebSocket for browser control")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep browser alive N seconds (implies --cdp)")] = 0,
    record: Annotated[bool, typer.Option("--record", help="Record browser session (implies --cdp)")] = False,
    record_output: Annotated[Path | None, typer.Option("--record-output", help="Recording output path")] = None,
    live_view: Annotated[bool, typer.Option("--live-view", help="Show DevTools URL for live debugging (implies --cdp)")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", help="Human-in-the-loop auth mode (implies --cdp)")] = False,
    save_cookies_file: Annotated[Path | None, typer.Option("--save-cookies", help="Save browser cookies to file after navigation (implies --cdp)")] = None,
    load_cookies_file: Annotated[Path | None, typer.Option("--load-cookies", help="Load cookies from file before navigation (implies --cdp)")] = None,
    tabs: Annotated[int, typer.Option("--tabs", help="Reuse one CDP session across N URLs (reduces cost, implies --cdp)")] = 1,
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
    # Flags that require CDP
    if any([keep_alive, record, live_view, interactive, save_cookies_file, load_cookies_file, tabs > 1]):
        cdp = True

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
        _stdin_findings: list = []
        if agent_safe:
            from .sanitise import sanitise_html
            _san = sanitise_html(html)
            html = _san.content
            _stdin_findings = _san.findings
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
        if agent_safe and isinstance(content, str):
            from .sanitise import sanitise_text, SanitiseResult
            _text_san = sanitise_text(content)
            content = _text_san.content
            _all_findings = _stdin_findings + _text_san.findings
            _combined = SanitiseResult(content=content, findings=_all_findings)
        result = {"url": "(stdin)", "content": content}
        if json_output:
            meta = {"format": format, "source": "stdin"}
            if agent_safe and isinstance(content, str):
                meta["agentSafety"] = _combined.to_metadata()
            _output_json({"data": result, "meta": meta})
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
            from .cookies import load_cookies
            _session_cookies = load_cookies(session)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            _error(f"Cannot read session file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    # Resolve batch file (--batch takes precedence, --input is backward compat)
    batch_file = batch or input_file
    is_batch_mode = batch is not None

    # --js implies networkidle0 (unless --wait-until explicitly set)
    if js and not wait_until:
        wait_until = "networkidle0"

    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    from .config import get_proxy
    effective_proxy = proxy or get_proxy()
    # Defer auth when --paywall is set: bypass uses direct HTTP, not CF API.
    # Client is created only if credentials exist (needed as fallback).
    if paywall:
        _has_creds = get_account_id() and get_api_token()
        client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl, proxy=effective_proxy) if _has_creds else None
    else:
        client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl, proxy=effective_proxy)
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
                paywall, _pw_session, stealth, clean,
                effective_proxy, agent_safe,
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
    # CDP mode: route through WebSocket client
    # ------------------------------------------------------------------
    if cdp:
        # Interactive mode needs longer keep-alive for human auth
        if interactive and not keep_alive:
            keep_alive = 300  # 5 minutes

        cdp_client = _get_cdp_client(
            as_json=json_output,
            keep_alive=keep_alive,
            recording=record,
            proxy=effective_proxy,
        )
        if keep_alive and cdp_client.ws_url:
            expiry = _time.time() + keep_alive
            save_cdp_session(
                session_id=cdp_client.session_id or "unknown",
                ws_url=cdp_client.ws_url,
                expiry=expiry,
            )

        # Show DevTools URL when live-view or interactive is active
        if live_view or interactive:
            dt_url = cdp_client.devtools_url
            if dt_url:
                console.print(f"[cyan]Live View:[/cyan] {dt_url}", err=True)
            if cdp_client.session_id:
                console.print(f"[dim]Session ID: {cdp_client.session_id}[/dim]", err=True)

        try:
            results = []

            # --interactive: human-in-the-loop auth flow
            if interactive:
                from .config import save_session as _save_session
                url = all_urls[0]  # interactive uses single URL
                page = cdp_client.new_page()
                page.navigate(url, wait_until="load", timeout=timeout or 30000)
                console.print(
                    f"\n[bold yellow]Interactive mode:[/bold yellow] Browser is navigated to [cyan]{url}[/cyan]",
                    err=True,
                )
                console.print(
                    "Complete authentication in the browser, then press [bold]Enter[/bold] to continue...",
                    err=True,
                )
                try:
                    input()
                except EOFError:
                    pass

                # Extract cookies from authenticated session
                cookies = page.get_cookies()
                session_path = _save_session("interactive", cookies)
                console.print(
                    f"[green]Saved {len(cookies)} cookies to:[/green] {session_path}",
                    err=True,
                )

                # Continue scraping with the authenticated page
                result = _scrape_single_cdp(
                    cdp_client, url, format=format,
                    js_expression=js_expression,
                    wait_for_selector=wait_for_selector,
                    selector=selector, scroll=scroll,
                    full_page=full_page_screenshot,
                    only_main_content=only_main_content,
                    include_tags=_include, exclude_tags=_exclude,
                    agent_safe=agent_safe,
                    user_agent=user_agent,
                    timeout=timeout,
                    har_output=har_output,
                    save_cookies=save_cookies_file,
                    page=page,
                    skip_navigation=True,
                )
                if timing:
                    console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
                results.append(result)
                page.close()

                # Scrape remaining URLs (if any) with fresh pages
                for url in all_urls[1:]:
                    result = _scrape_single_cdp(
                        cdp_client, url, format=format,
                        js_expression=js_expression,
                        wait_for_selector=wait_for_selector,
                        selector=selector, scroll=scroll,
                        full_page=full_page_screenshot,
                        only_main_content=only_main_content,
                        include_tags=_include, exclude_tags=_exclude,
                        agent_safe=agent_safe,
                        user_agent=user_agent,
                        timeout=timeout,
                        har_output=har_output,
                        save_cookies=save_cookies_file,
                    )
                    if timing:
                        console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
                    results.append(result)
            else:
                for url in all_urls:
                    result = _scrape_single_cdp(
                        cdp_client, url, format=format,
                        js_expression=js_expression,
                        wait_for_selector=wait_for_selector,
                        selector=selector, scroll=scroll,
                        full_page=full_page_screenshot,
                        only_main_content=only_main_content,
                        include_tags=_include, exclude_tags=_exclude,
                        agent_safe=agent_safe,
                        user_agent=user_agent,
                        timeout=timeout,
                        har_output=har_output,
                        load_cookies=load_cookies_file,
                        save_cookies=save_cookies_file,
                    )
                    if timing:
                        console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
                    results.append(result)

            # --record: save recording data
            if record:
                recording_data = cdp_client.get_recording()
                if recording_data:
                    from datetime import datetime
                    rec_path = record_output or Path(f"recording-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
                    rec_path.write_text(json.dumps(recording_data, indent=2, default=str), encoding="utf-8")
                    console.print(f"[green]Recording saved to:[/green] {rec_path}", err=True)

            if live_view:
                console.print("[dim]Session active — press Ctrl+C to close[/dim]", err=True)
                try:
                    while True:
                        _time.sleep(1)
                except KeyboardInterrupt:
                    pass

            if json_output:
                data = results if len(results) > 1 else results[0]
                if fields:
                    data = _filter_fields(data, fields)
                meta = {"format": format, "source": "cdp"}
                if len(results) > 1:
                    meta["count"] = len(results)
                elif "metadata" in results[0]:
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
        finally:
            cdp_client.close()
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
                    paywall, None, stealth, clean,
                    effective_proxy, agent_safe,
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
                                    stealth=stealth,
                                    clean=clean,
                                    proxy=effective_proxy,
                                    agent_safe=agent_safe)
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
# search — web search via Jina
# ------------------------------------------------------------------


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 10,
    scrape_results: Annotated[bool, typer.Option("--scrape", help="Also scrape each result URL")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
    paywall: Annotated[bool, typer.Option("--paywall", help="Paywall bypass for scraped URLs")] = False,
    stealth: Annotated[bool, typer.Option("--stealth", help="Stealth mode for scraped URLs")] = False,
    only_main_content: Annotated[bool, typer.Option("--only-main-content", help="Main content only")] = False,
    clean: Annotated[bool, typer.Option("--clean", help="Strip ads from scraped HTML")] = False,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for --scrape (max 50)")] = 3,
):
    """Search the web and optionally scrape results.

    Uses Jina Search API (free, no auth required).

    Example:
        flarecrawl search "python web scraping" --json
        flarecrawl search "topic" --scrape --limit 5 --json
        flarecrawl search "query" --json | jq '.data[].url'
    """
    from .search import jina_search
    from .config import get_proxy

    effective_proxy = proxy or get_proxy()

    try:
        results = jina_search(query, limit=limit, proxy=effective_proxy)
    except Exception as e:
        _error(f"Search failed: {e}", "SEARCH_ERROR", EXIT_ERROR, as_json=json_output)
        return

    data = [{"url": r.url, "title": r.title, "snippet": r.snippet} for r in results]

    if scrape_results and data:
        cache_ttl = DEFAULT_CACHE_TTL
        if paywall:
            _has_creds = get_account_id() and get_api_token()
            client = _get_client(True, cache_ttl=cache_ttl, proxy=effective_proxy) if _has_creds else None
        else:
            client = _get_client(True, cache_ttl=cache_ttl, proxy=effective_proxy)

        for item in data:
            try:
                result = _scrape_single(
                    client, item["url"], "markdown", None, False, False,
                    None, None, paywall=paywall, stealth=stealth,
                    only_main_content=only_main_content, clean=clean,
                    proxy=effective_proxy,
                )
                item["content"] = result.get("content", "")
                item["metadata"] = result.get("metadata", {})
            except Exception as e:
                item["content"] = ""
                item["error"] = str(e)

    meta = {"count": len(data), "query": query}

    if json_output:
        _output_json({"data": data, "meta": meta})
    else:
        for i, item in enumerate(data, 1):
            console.print(f"\n[bold]{i}. {item['title']}[/bold]")
            console.print(f"[dim]{item['url']}[/dim]")
            console.print(item["snippet"])
            if "content" in item and item["content"]:
                console.print(f"\n{'─' * 60}")
                content = item["content"]
                if len(content) > 2000:
                    content = content[:2000] + "\n\n[dim]... truncated[/dim]"
                _output_text(content)


# ------------------------------------------------------------------
# fetch — content-type aware download
# ------------------------------------------------------------------


@app.command()
def fetch(
    url: Annotated[str, typer.Argument(help="URL to fetch")],
    session: Annotated[str | None, typer.Option("--session", help="Cookie file or @NAME for saved session")] = None,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers (Key: Value)")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file path")] = None,
    stealth: Annotated[bool, typer.Option("--stealth", help="Use browser TLS fingerprint (requires curl_cffi)")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL (http/https/socks5)")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing files")] = False,
):
    """Fetch a URL with content-type awareness.

    HTML pages are converted to markdown. Binary files (PDF, ZIP, etc.)
    are downloaded directly. JSON responses are pretty-printed.

    Use --session to load cookies from a file or @name for saved sessions.

    Example:
        flarecrawl fetch https://example.com/file.pdf -o file.pdf
        flarecrawl fetch https://example.com --session cookies.json
        flarecrawl fetch https://example.com --session @mysession
        flarecrawl fetch https://api.example.com/data.json --json
    """
    from .fetch import ContentInfo, build_session, detect_content_type, download_binary

    _validate_url(url, json_output)

    # Resolve session cookies
    _cookies = None
    if session:
        if session.startswith("@"):
            from .config import load_session as _load_session
            try:
                _cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(f"Session not found: {session[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        else:
            from .cookies import load_cookies
            try:
                _cookies = load_cookies(Path(session))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _error(f"Cannot read session file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    # Build auth tuple
    _auth = None
    if auth:
        if ":" not in auth:
            _error("Invalid --auth format. Expected user:password", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output)
        _auth = tuple(auth.split(":", 1))

    custom_headers = _parse_headers(headers, json_output)
    from .config import get_proxy
    effective_proxy = proxy or get_proxy()

    # Build httpx session
    http_session = build_session(
        cookies=_cookies,
        auth=_auth,
        headers=custom_headers,
        proxy=effective_proxy,
    )

    try:
        # Detect content type
        console.print(f"[dim]Probing {url}...[/dim]")
        info = detect_content_type(url, session=http_session, headers=custom_headers)

        if info.is_binary:
            # Binary download
            out_path = output or Path(info.filename or "download")
            if out_path.exists() and not overwrite:
                _error(f"File exists: {out_path} (use --overwrite)", "VALIDATION_ERROR", EXIT_VALIDATION,
                       as_json=json_output)

            console.print(f"[dim]Downloading {info.content_type}"
                          f"{f' ({info.size / 1024 / 1024:.1f} MB)' if info.size and info.size > 1024 * 1024 else ''}[/dim]")

            # Progress bar for large files
            if info.size and info.size > 1024 * 1024:
                from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn
                with Progress(BarColumn(), DownloadColumn(), TransferSpeedColumn(), console=console) as progress:
                    task = progress.add_task("Downloading", total=info.size)
                    result = download_binary(
                        url, http_session, out_path,
                        progress_callback=lambda n: progress.update(task, completed=n),
                    )
            else:
                result = download_binary(url, http_session, out_path)

            if json_output:
                _output_json({"data": {
                    "path": str(result.path),
                    "content_type": result.content_type,
                    "size": result.size,
                    "elapsed": result.elapsed,
                }, "meta": {"url": url}})
            else:
                console.print(f"[green]Saved:[/green] {result.path} ({result.size:,} bytes, {result.elapsed:.1f}s)")

        elif info.is_json:
            # JSON response
            resp = http_session.get(url)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                data = resp.text
            if output:
                output.write_text(json.dumps(data, indent=2) if isinstance(data, dict) else str(data))
                console.print(f"[green]Saved:[/green] {output}")
            elif json_output:
                _output_json({"data": data, "meta": {"url": url, "content_type": info.content_type}})
            else:
                _output_json(data)

        else:
            # HTML/text — fall through to scrape for markdown conversion
            console.print("[dim]HTML content — converting to markdown...[/dim]")
            from .config import get_proxy as _gp
            _require_auth(json_output)
            cache_ttl = DEFAULT_CACHE_TTL
            client = _get_client(json_output, cache_ttl=cache_ttl, proxy=effective_proxy)

            auth_kwargs = {}
            if _cookies:
                auth_kwargs["cookies"] = _cookies
            if _auth:
                import base64 as _b64
                auth_kwargs["authenticate"] = {"username": _auth[0], "password": _auth[1]}
                auth_kwargs["extra_headers"] = {"Authorization": f"Basic {_b64.b64encode(f'{_auth[0]}:{_auth[1]}'.encode()).decode()}"}
            if custom_headers:
                existing = auth_kwargs.get("extra_headers", {})
                auth_kwargs["extra_headers"] = {**custom_headers, **existing}

            result = _scrape_single(
                client, url, "markdown", None, False, False, None, None,
                auth_kwargs=auth_kwargs or None,
                stealth=stealth,
                proxy=effective_proxy,
            )

            content = result.get("content", "")
            if output:
                output.write_text(content)
                console.print(f"[green]Saved:[/green] {output}")
            elif json_output:
                _output_json({"data": result, "meta": {"url": url, "format": "markdown"}})
            else:
                _output_text(content)

    except httpx.HTTPError as e:
        _error(f"HTTP error: {e}", "ERROR", EXIT_ERROR, as_json=json_output)
    finally:
        http_session.close()


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
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
    ignore_robots: Annotated[bool, typer.Option("--ignore-robots", help="Ignore robots.txt and AI Crawl Control directives")] = False,
    rate_limit: Annotated[float, typer.Option("--rate-limit", help="Max requests/sec per hostname (0 disables)")] = 2.0,
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
        if ignore_robots:
            kwargs["ignore_robots"] = True

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
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
    rate_limit: Annotated[float, typer.Option("--rate-limit", help="Max requests/sec per hostname (0 disables)")] = 2.0,
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
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for batch (max 50, free tier: 3)")] = 3,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
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
        if agent_safe:
            from .sanitise import sanitise_text
            for r in results:
                if r.get("status") == "ok" and "data" in r:
                    d = r["data"]
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if isinstance(v, str):
                                d[k] = sanitise_text(v).content
                    elif isinstance(d, str):
                        r["data"] = sanitise_text(d).content
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
            if agent_safe:
                from .sanitise import sanitise_text
                if isinstance(extracted, dict):
                    for k, v in extracted.items():
                        if isinstance(v, str):
                            extracted[k] = sanitise_text(v).content
                elif isinstance(extracted, str):
                    extracted = sanitise_text(extracted).content
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
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers (max 50)")] = 3,
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
        # CF renders XML as HTML — use selectolax to extract text of <loc> tags
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
                    # CF renders XML as HTML — use selectolax to find link elements
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
            from .openapi import discover_specs, probe_common_paths
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
# openapi — OpenAPI/Swagger spec discovery
# ------------------------------------------------------------------


@app.command()
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
    from .openapi import discover_specs, download_spec, probe_common_paths

    _validate_url(url, json_output)
    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output, cache_ttl=cache_ttl)

    # Load session cookies for HTTP probing
    _cookies = None
    if session:
        if session.startswith("@"):
            from .config import load_session as _load_session
            try:
                _cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(f"Session not found: {session[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        else:
            from .cookies import load_cookies
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
            from .cookies import cookies_to_httpx
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
                    console.print(f"  {status} {spec.url} → {out_path}")
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
# session — saved session management
# ------------------------------------------------------------------


session_app = typer.Typer(help="Saved cookie session management")
app.add_typer(session_app, name="session")


@session_app.command("save")
def session_save(
    name: Annotated[str, typer.Argument(help="Session name")],
    file: Annotated[Path, typer.Option("--file", "-f", help="Cookie file to save")],
):
    """Save cookies from a file to a named session.

    Supports Puppeteer JSON, Chrome DevTools, and Netscape format.

    Example:
        flarecrawl session save mysite --file cookies.json
        flarecrawl session save github --file github-cookies.json
    """
    from .config import save_session as _save
    from .cookies import load_cookies

    try:
        cookies = load_cookies(file)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        _error(f"Cannot read cookie file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION)

    path = _save(name, cookies)
    console.print(f"[green]Session saved:[/green] {name} ({len(cookies)} cookies → {path})")


@session_app.command("list")
def session_list(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """List all saved sessions.

    Example:
        flarecrawl session list
        flarecrawl session list --json
    """
    from .config import list_sessions as _list

    sessions = _list()

    if json_output:
        _output_json({"data": sessions, "meta": {"count": len(sessions)}})
        return

    if not sessions:
        console.print("[dim]No saved sessions[/dim]")
        return

    for name in sessions:
        console.print(f"  {name}")
    console.print(f"\n[dim]{len(sessions)} session(s)[/dim]")


@session_app.command("show")
def session_show(
    name: Annotated[str, typer.Argument(help="Session name")],
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show cookies in a saved session.

    Example:
        flarecrawl session show mysite
        flarecrawl session show mysite --json
    """
    from .config import load_session as _load

    try:
        cookies = _load(name)
    except FileNotFoundError:
        _error(f"Session not found: {name}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        return

    if json_output:
        _output_json({"data": cookies, "meta": {"name": name, "count": len(cookies)}})
        return

    console.print(f"[bold]{name}[/bold] ({len(cookies)} cookies)")
    for c in cookies:
        domain = c.get("domain", "")
        console.print(f"  [cyan]{c['name']}[/cyan] = {c['value'][:40]}{'...' if len(c['value']) > 40 else ''}"
                      f" [{domain}]")


@session_app.command("delete")
def session_delete(
    name: Annotated[str, typer.Argument(help="Session name")],
):
    """Delete a saved session.

    Example:
        flarecrawl session delete mysite
    """
    from .config import delete_session as _delete

    if _delete(name):
        console.print(f"[green]Deleted:[/green] {name}")
    else:
        _error(f"Session not found: {name}", "NOT_FOUND", EXIT_NOT_FOUND)


@session_app.command("validate")
def session_validate(
    name: Annotated[str, typer.Argument(help="Session name")],
    url: Annotated[str, typer.Argument(help="URL to test session against")],
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Test a saved session against a URL with a HEAD request.

    Example:
        flarecrawl session validate mysite https://example.com
        flarecrawl session validate mysite https://example.com --json
    """
    from .config import load_session as _load
    from .cookies import validate_cookies

    _validate_url(url, json_output)

    try:
        cookies = _load(name)
    except FileNotFoundError:
        _error(f"Session not found: {name}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        return

    result = validate_cookies(cookies, url)

    if json_output:
        _output_json({"data": result, "meta": {"name": name, "url": url}})
        return

    status = "[green]valid[/green]" if result.get("valid") else "[red]invalid[/red]"
    console.print(f"Session: [bold]{name}[/bold]")
    console.print(f"URL: {url}")
    console.print(f"Status: {status} (HTTP {result.get('status_code')})")
    if result.get("redirected_to"):
        console.print(f"Redirected to: [dim]{result['redirected_to']}[/dim]")
    if result.get("error"):
        console.print(f"Error: [red]{result['error']}[/red]")


# ------------------------------------------------------------------
# cdp — CDP session management
# ------------------------------------------------------------------

cdp_app = typer.Typer(help="CDP session management")
app.add_typer(cdp_app, name="cdp")


@cdp_app.command("sessions")
def cdp_sessions_cmd(json_output: Annotated[bool, typer.Option("--json")] = False):
    """List active CDP browser sessions."""
    sessions = list_cdp_sessions()
    if not sessions:
        if json_output:
            _output_json({"sessions": []})
        else:
            console.print("[dim]No active sessions[/dim]")
        return

    if json_output:
        _output_json({"sessions": sessions})
        return

    from datetime import datetime
    table = Table(title="Active CDP Sessions")
    table.add_column("Session ID", style="cyan")
    table.add_column("WebSocket URL", style="dim", max_width=60)
    table.add_column("Expires", style="green")
    for s in sessions:
        expiry_dt = datetime.fromtimestamp(s["expiry"]).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(s["session_id"], s["ws_url"], expiry_dt)
    console.print(table)


@cdp_app.command("connect")
def cdp_connect(
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep browser alive N seconds")] = 300,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Print CDP WebSocket URL for Playwright/Puppeteer connection.

    Starts a browser session and prints the connection URL.
    External tools connect via this URL for full browser control.

    Example:
        flarecrawl cdp connect
        flarecrawl cdp connect --keep-alive 600 --json

    Playwright usage:
        browser = await playwright.chromium.connect_over_cdp(url)
    """
    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive)

    endpoint = cdp_client.endpoint
    session_id = cdp_client.session_id

    if json_output:
        _output_json({
            "data": {
                "ws_url": endpoint,
                "session_id": session_id,
                "keep_alive": keep_alive,
                "playwright_example": f'browser = await playwright.chromium.connect_over_cdp("{endpoint}")',
            }
        })
    else:
        console.print(f"\n[bold]CDP WebSocket URL[/bold]\n")
        console.print(f"  {endpoint}\n")
        if session_id:
            console.print(f"[dim]Session:[/dim] {session_id}")
        console.print(f"[dim]Expires:[/dim] {keep_alive}s\n")
        console.print("[bold]Playwright:[/bold]")
        console.print(f'  browser = await playwright.chromium.connect_over_cdp("{endpoint}")\n')
        console.print("[bold]Puppeteer:[/bold]")
        console.print(f'  browser = await puppeteer.connect({{browserWSEndpoint: "{endpoint}"}})\n')
        console.print("[dim]Press Ctrl+C to close session[/dim]")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    cdp_client.close()


@cdp_app.command("close")
def cdp_close_cmd(
    session_id: Annotated[str | None, typer.Argument(help="Session ID to close (omit to close all)")] = None,
):
    """Close a CDP browser session."""
    removed = clear_cdp_session(session_id)
    if removed:
        target = session_id or "all"
        console.print(f"[green]Session removed:[/green] {target}")
    else:
        console.print("[dim]No matching session found[/dim]")


@app.command()
def interact(
    url: Annotated[str, typer.Argument(help="URL to interact with")],
    fill: Annotated[list[str] | None, typer.Option("--fill", help="Fill field: 'selector=value'")] = None,
    click: Annotated[list[str] | None, typer.Option("--click", help="Click element by CSS selector")] = None,
    select: Annotated[list[str] | None, typer.Option("--select", help="Select dropdown: 'selector=value'")] = None,
    wait_for: Annotated[str | None, typer.Option("--wait-for", help="Wait for selector after actions")] = None,
    wait_for_url: Annotated[str | None, typer.Option("--wait-for-url", help="Wait for URL pattern after actions")] = None,
    screenshot: Annotated[Path | None, typer.Option("--screenshot", "-o", help="Screenshot after actions")] = None,
    save_cookies: Annotated[Path | None, typer.Option("--save-cookies", help="Save cookies after interaction")] = None,
    load_cookies: Annotated[Path | None, typer.Option("--load-cookies", help="Load cookies before interaction")] = None,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep browser alive N seconds")] = 0,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
    stagehand: Annotated[bool, typer.Option("--stagehand", help="Use AI to find elements by intent (coming soon)")] = False,
):
    """Interact with a web page: fill forms, click buttons, select dropdowns.

    Actions execute in order: fill -> select -> click. Uses human-like
    timing (variable keystroke delays, Bezier mouse curves) to avoid
    bot detection.

    Example:
        flarecrawl interact https://form.example.com \\
          --fill "#name=John Doe" --fill "#email=john@example.com" \\
          --select "#country=US" \\
          --click "button[type=submit]" \\
          --wait-for ".success-message" \\
          --screenshot result.png --save-cookies session.json
    """
    if stagehand:
        console.print("[yellow]Stagehand integration coming soon.[/yellow]")
        console.print("[dim]For now, Stagehand works directly via Playwright + CF Browser Run.[/dim]")
        console.print("[dim]See: https://developers.cloudflare.com/browser-run/stagehand/[/dim]")
        raise typer.Exit(0)

    _validate_url(url, json_output)
    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()
        start = _time.time()

        # Load cookies if provided
        if load_cookies:
            cookies = json.loads(load_cookies.read_text())
            page.set_cookies(cookies)

        # Navigate
        page.navigate(url, wait_until="load")

        # Execute fills
        if fill:
            for item in fill:
                if "=" not in item:
                    _error(
                        f"Invalid --fill format: '{item}' (expected 'selector=value')",
                        "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
                    )
                selector, _, value = item.partition("=")
                page.fill(selector.strip(), value.strip())

        # Execute selects
        if select:
            for item in select:
                if "=" not in item:
                    _error(
                        f"Invalid --select format: '{item}' (expected 'selector=value')",
                        "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
                    )
                selector, _, value = item.partition("=")
                page.select(selector.strip(), value.strip())

        # Execute clicks
        if click:
            for sel in click:
                page.click(sel.strip())
                _time.sleep(0.5)  # Brief pause between clicks

        # Wait conditions
        if wait_for:
            page.wait_for_selector(wait_for)

        if wait_for_url:
            # Poll for URL match
            pattern = wait_for_url.replace("*", ".*")
            for _ in range(60):  # 30 seconds max
                current_url = page.evaluate("window.location.href")
                if re.search(pattern, current_url):
                    break
                _time.sleep(0.5)

        elapsed = _time.time() - start

        # Save cookies
        if save_cookies:
            cookies = page.get_cookies()
            save_cookies.write_text(json.dumps(cookies, indent=2))
            if not json_output:
                console.print(f"[dim]Cookies saved to {save_cookies}[/dim]")

        # Screenshot
        if screenshot:
            data = page.screenshot(full_page=True)
            screenshot.write_bytes(data)
            if not json_output:
                console.print(f"[dim]Screenshot saved to {screenshot}[/dim]")

        # Get final page state
        final_url = page.evaluate("window.location.href")
        title = page.evaluate("document.title")

        result = {
            "url": final_url,
            "title": title,
            "elapsed": round(elapsed, 2),
            "actions": {
                "fills": len(fill) if fill else 0,
                "selects": len(select) if select else 0,
                "clicks": len(click) if click else 0,
            },
        }

        if json_output:
            _output_json({"data": result, "meta": {"command": "interact"}})
        else:
            console.print(f"\n[green]Done[/green] in {elapsed:.1f}s")
            console.print(f"[dim]URL:[/dim] {final_url}")
            console.print(f"[dim]Title:[/dim] {title}")
            if fill:
                console.print(f"[dim]Filled:[/dim] {len(fill)} fields")
            if click:
                console.print(f"[dim]Clicked:[/dim] {len(click)} elements")

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


# ------------------------------------------------------------------
# WebMCP commands
# ------------------------------------------------------------------

webmcp_app = typer.Typer(help="WebMCP tool discovery and execution")
app.add_typer(webmcp_app, name="webmcp")


@webmcp_app.command("discover")
def webmcp_discover(
    url: Annotated[str, typer.Argument(help="URL to discover WebMCP tools on")],
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep session alive")] = 60,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
):
    """Discover WebMCP tools exposed by a website.

    WebMCP lets sites declare structured tools that AI agents can call
    directly — no HTML scraping needed. Requires Chrome 146+ (CF lab pool).

    Example:
        flarecrawl webmcp discover https://hotel-site.com --json
    """
    from .cdp import CDPError

    _validate_url(url, json_output)
    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()
        page.navigate(url, wait_until="networkidle0")

        try:
            tools = page.webmcp_list_tools()
        except (CDPError, FlareCrawlError) as e:
            if "not supported" in str(e).lower():
                if json_output:
                    _output_json({"data": {"tools": [], "supported": False}, "meta": {"url": url}})
                else:
                    console.print("[yellow]WebMCP not supported[/yellow] on this page")
                    console.print("[dim]Requires Chrome 146+ via CF lab pool[/dim]")
                return
            raise

        if json_output:
            _output_json({
                "data": {"tools": tools, "supported": True, "count": len(tools)},
                "meta": {"url": url},
            })
        else:
            if not tools:
                console.print(f"[dim]No WebMCP tools found on {url}[/dim]")
            else:
                console.print(f"\n[bold]WebMCP Tools[/bold] ({len(tools)} found)\n")
                for tool in tools:
                    console.print(f"  [cyan]{tool.get('name', '?')}[/cyan]")
                    if tool.get("description"):
                        console.print(f"    {tool['description']}")
                    if tool.get("inputSchema"):
                        props = tool["inputSchema"].get("properties", {})
                        if props:
                            params = ", ".join(f"{k}: {v.get('type', '?')}" for k, v in props.items())
                            console.print(f"    [dim]params: {params}[/dim]")
                    console.print()

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


@webmcp_app.command("call")
def webmcp_call(
    url: Annotated[str, typer.Argument(help="URL with WebMCP tools")],
    tool: Annotated[str, typer.Option("--tool", "-t", help="Tool name to execute")] = ...,
    params: Annotated[str | None, typer.Option("--params", "-p", help="Tool parameters as JSON")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep session alive")] = 60,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
):
    """Execute a WebMCP tool on a website.

    First discover available tools with 'webmcp discover', then call them.

    Example:
        flarecrawl webmcp call https://hotel.com --tool searchHotels --params '{"city": "Paris"}' --json
    """
    _validate_url(url, json_output)

    parsed_params = None
    if params:
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError as e:
            _error(f"Invalid JSON params: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)

    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()
        page.navigate(url, wait_until="networkidle0")

        start = _time.time()
        result = page.webmcp_execute(tool, parsed_params)
        elapsed = _time.time() - start

        if json_output:
            _output_json({
                "data": {"tool": tool, "params": parsed_params, "result": result, "elapsed": round(elapsed, 2)},
                "meta": {"url": url},
            })
        else:
            console.print(f"\n[bold]{tool}[/bold] returned:\n")
            if isinstance(result, (dict, list)):
                console.print(json.dumps(result, indent=2))
            else:
                console.print(str(result))
            console.print(f"\n[dim]{elapsed:.2f}s[/dim]")

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


# ------------------------------------------------------------------
# Design extraction
# ------------------------------------------------------------------

design_app = typer.Typer(help="Extract design systems from websites")
app.add_typer(design_app, name="design")


@design_app.command("extract")
def design_extract(
    url: Annotated[str, typer.Argument(help="URL to extract design from")],
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSON envelope output")] = False,
    preview: Annotated[bool, typer.Option("--preview", help="Generate HTML preview instead of markdown")] = False,
    dark: Annotated[bool, typer.Option("--dark", help="Extract dark mode theme")] = False,
    auto_dark: Annotated[bool, typer.Option("--auto-dark", help="Auto-detect and extract both themes")] = False,
    interactions: Annotated[bool, typer.Option("--interactions", help="Capture hover/focus states via CDP")] = False,
    responsive: Annotated[bool, typer.Option("--responsive", help="Extract at 4 viewports")] = False,
    full: Annotated[bool, typer.Option("--full", help="Enable all captures")] = False,
    depth: Annotated[int, typer.Option("--depth", help="Crawl N internal pages")] = 1,
    session: Annotated[Path | None, typer.Option("--session", help="Load cookies for auth")] = None,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep browser alive (seconds)")] = 0,
):
    """Extract design tokens from a website into DESIGN.md or HTML preview."""
    from .design import EXTRACT_JS, format_design_md, format_preview_html, process_tokens, score_coherence

    _validate_url(url, json_output)

    # --full enables all capture modes
    if full:
        dark = True
        interactions = True
        responsive = True
        auto_dark = True

    cdp_client = _get_cdp_client(as_json=json_output, keep_alive=keep_alive, proxy=proxy)

    try:
        page = cdp_client.new_page()

        # Load cookies from session file
        if session:
            import json as _json

            cookies = _json.loads(session.read_text())
            page.set_cookies(cookies)

        page.navigate(url, wait_until="networkidle0", timeout=30000)

        # Main extraction via live DOM
        raw = page.evaluate(EXTRACT_JS)
        tokens = process_tokens(raw)

        # TODO: --dark, --interactions, --responsive do additional extractions

        coherence = score_coherence(tokens)

        if json_output:
            _output_json({
                "data": {"tokens": tokens, "coherence": coherence, "url": url},
                "meta": {"command": "design"},
            })
        elif preview:
            html = format_preview_html(tokens, coherence, url)
            if output:
                output.write_text(html)
                console.print(f"[dim]Preview saved to {output}[/dim]")
            else:
                print(html)
        else:
            md = format_design_md(tokens, coherence, url)
            if output:
                output.write_text(md)
                console.print(f"[dim]DESIGN.md saved to {output}[/dim]")
            else:
                print(md)

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


@design_app.command("coherence")
def design_coherence(
    url: Annotated[str, typer.Argument(help="URL to score")],
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    proxy: Annotated[str | None, typer.Option("--proxy")] = None,
    session: Annotated[Path | None, typer.Option("--session")] = None,
):
    """Score a website's design coherence (A-F grade, 9 categories)."""
    from .design import EXTRACT_JS, process_tokens, score_coherence

    _validate_url(url, json_output)

    cdp_client = _get_cdp_client(as_json=json_output, proxy=proxy)

    try:
        page = cdp_client.new_page()

        if session:
            import json as _json

            cookies = _json.loads(session.read_text())
            page.set_cookies(cookies)

        page.navigate(url, wait_until="networkidle0", timeout=30000)

        raw = page.evaluate(EXTRACT_JS)
        tokens = process_tokens(raw)
        coherence = score_coherence(tokens)

        if json_output:
            _output_json({
                "data": {"coherence": coherence, "url": url},
                "meta": {"command": "design coherence"},
            })
        else:
            console.print(f"\n[bold]Design Coherence: {url}[/bold]\n")
            console.print(f"  Overall: [bold]{coherence['overall']}/100[/bold] ({coherence['grade']})\n")
            if coherence.get("categories"):
                table = Table(show_header=True)
                table.add_column("Category")
                table.add_column("Score", justify="right")
                for cat, score in coherence["categories"].items():
                    label = cat.replace("_", " ").title()
                    color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
                    table.add_row(label, f"[{color}]{score}[/{color}]")
                console.print(table)
            if coherence.get("issues"):
                console.print("\n[bold]Issues:[/bold]")
                for issue in coherence["issues"]:
                    console.print(f"  [yellow]- {issue}[/yellow]")
            console.print()

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


@design_app.command("diff")
def design_diff(
    url1: Annotated[str, typer.Argument(help="First URL")],
    url2: Annotated[str, typer.Argument(help="Second URL")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    proxy: Annotated[str | None, typer.Option("--proxy")] = None,
):
    """Compare design tokens between two URLs."""
    from .design import EXTRACT_JS, process_tokens, score_coherence

    _validate_url(url1, json_output)
    _validate_url(url2, json_output)

    cdp_client = _get_cdp_client(as_json=json_output, proxy=proxy)

    try:
        page = cdp_client.new_page()

        # Extract first URL
        page.navigate(url1, wait_until="networkidle0", timeout=30000)
        raw1 = page.evaluate(EXTRACT_JS)
        tokens1 = process_tokens(raw1)
        coherence1 = score_coherence(tokens1)

        # Extract second URL
        page.navigate(url2, wait_until="networkidle0", timeout=30000)
        raw2 = page.evaluate(EXTRACT_JS)
        tokens2 = process_tokens(raw2)
        coherence2 = score_coherence(tokens2)

        diff_data = {
            "url1": {"url": url1, "coherence": coherence1},
            "url2": {"url": url2, "coherence": coherence2},
            "differences": {
                "colors": {
                    "url1_unique_count": len(tokens1.get("colors", {}).get("backgrounds", [])),
                    "url2_unique_count": len(tokens2.get("colors", {}).get("backgrounds", [])),
                },
                "typography": {
                    "url1_elements": list(tokens1.get("typography", {}).keys()),
                    "url2_elements": list(tokens2.get("typography", {}).keys()),
                },
                "spacing": {
                    "url1_values": tokens1.get("spacing", {}).get("values", []),
                    "url2_values": tokens2.get("spacing", {}).get("values", []),
                },
                "css_vars": {
                    "url1_count": len(tokens1.get("cssVars", {})),
                    "url2_count": len(tokens2.get("cssVars", {})),
                    "shared": list(
                        set(tokens1.get("cssVars", {}).keys()) & set(tokens2.get("cssVars", {}).keys())
                    ),
                },
            },
        }

        if json_output:
            _output_json({"data": diff_data, "meta": {"command": "design diff"}})
        else:
            report = []
            report.append(f"Design Diff: {url1} vs {url2}\n")
            report.append(f"  {url1}: {coherence1['overall']}/100 ({coherence1['grade']})")
            report.append(f"  {url2}: {coherence2['overall']}/100 ({coherence2['grade']})")
            report.append("")
            report.append("Differences:")
            d = diff_data["differences"]
            report.append(f"  Colors: {d['colors']['url1_unique_count']} vs {d['colors']['url2_unique_count']} unique")
            report.append(f"  Typography elements: {d['typography']['url1_elements']} vs {d['typography']['url2_elements']}")
            report.append(f"  Spacing values: {len(d['spacing']['url1_values'])} vs {len(d['spacing']['url2_values'])}")
            report.append(f"  CSS vars: {d['css_vars']['url1_count']} vs {d['css_vars']['url2_count']} ({len(d['css_vars']['shared'])} shared)")
            text = "\n".join(report)
            if output:
                output.write_text(text)
                console.print(f"[dim]Diff saved to {output}[/dim]")
            else:
                console.print(text)

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(e, json_output)
    finally:
        cdp_client.close()


# ---------------------------------------------------------------------
# Frontier v2 ops subcommands
# ---------------------------------------------------------------------

frontier_app = typer.Typer(
    name="frontier",
    help="Inspect a local frontier v2 job database (see PERF-PLAN-PROGRESS).",
    no_args_is_help=True,
)
app.add_typer(frontier_app, name="frontier")


@frontier_app.command("dead-letter")
def frontier_dead_letter(
    job_id: Annotated[str, typer.Argument(help="Frontier job ID")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON instead of a table")] = False,
) -> None:
    """Dump the dead-letter rows for a frontier v2 job.

    Example:
        flarecrawl frontier dead-letter my-job
        flarecrawl frontier dead-letter my-job --json
    """
    import asyncio as _asyncio

    from ._validate import validate_job_id
    from .dead_letter import dump_dead_letter, format_rows

    try:
        validate_job_id(job_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    rows = _asyncio.run(dump_dead_letter(job_id))
    typer.echo(format_rows(rows, as_json=as_json))


# ============================================================
# authcrawl — direct BFS via AuthenticatedCrawler (no CF round-trip)
# ============================================================


@app.command("authcrawl")
def authcrawl(
    url: Annotated[str, typer.Argument(help="Seed URL to crawl")],
    limit: Annotated[int, typer.Option("--limit", help="Max pages")] = 50,
    max_depth: Annotated[int, typer.Option("--max-depth", help="BFS max depth")] = 3,
    workers: Annotated[int, typer.Option("--workers", help="Concurrent fetchers")] = 3,
    delay: Annotated[float, typer.Option("--delay", help="Sleep between batches (seconds)")] = 1.0,
    rate_limit: Annotated[float, typer.Option("--rate-limit", help="Per-host req/sec (0 disables)")] = 2.0,
    cookies_file: Annotated[Path | None, typer.Option("--cookies", help="JSON cookies file")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Override User-Agent")] = None,
    ignore_robots: Annotated[bool, typer.Option("--ignore-robots", help="Skip robots.txt")] = False,
    include_paths: Annotated[str | None, typer.Option("--include-paths", help="Comma-separated regex/substrings")] = None,
    exclude_paths: Annotated[str | None, typer.Option("--exclude-paths", help="Comma-separated regex/substrings")] = None,
    format: Annotated[str, typer.Option("--format", "-f", help="markdown, html")] = "markdown",
    resume: Annotated[str | None, typer.Option("--resume", help="Resume an existing frontier job by ID")] = None,
    max_attempts: Annotated[int, typer.Option("--max-attempts", help="Per-URL retry cap before dead-letter")] = 3,
    adaptive_delay: Annotated[bool, typer.Option("--adaptive-delay/--no-adaptive-delay", help="Use EWMA per-host snooze instead of fixed delay")] = False,
    refresh_days: Annotated[int, typer.Option("--refresh-days", help="Days until a visited row is stale")] = 7,
    tracing: Annotated[str, typer.Option("--tracing", help="OpenTelemetry exporter: none, console, json, otlp")] = "none",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write NDJSON results to file")] = None,
):
    """Authenticated BFS crawl driven by the flarecrawl Frontier v2.

    Unlike ``flarecrawl crawl`` (which hits the Cloudflare Browser Run
    API), ``authcrawl`` fetches pages directly via ``httpx`` while
    carrying a cookie jar — ideal for session-gated sites. Dedup,
    retries, conditional headers, adaptive delay, and resume are all
    delegated to the frontier.
    """
    import json as _json
    import os as _os

    from ._validate import validate_job_id
    from .authcrawl import AuthenticatedCrawler, CrawlConfig
    from .telemetry import init_tracing

    if resume is not None:
        try:
            validate_job_id(resume)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

    # Tracing is opt-in via flag or env var.
    _exp = tracing or _os.environ.get("FLARECRAWL_TRACING", "none")
    if _exp not in ("none", "console", "json", "otlp"):
        console.print(f"[red]Unknown --tracing value: {_exp}[/red]")
        raise typer.Exit(2)
    init_tracing(exporter=_exp)  # type: ignore[arg-type]

    cookies: list[dict] | None = None
    if cookies_file is not None:
        cookies = _json.loads(cookies_file.read_text(encoding="utf-8"))

    inc = [s.strip() for s in include_paths.split(",")] if include_paths else None
    exc = [s.strip() for s in exclude_paths.split(",")] if exclude_paths else None

    cfg = CrawlConfig(
        seed_url=url,
        cookies=cookies,
        max_depth=max_depth,
        max_pages=limit,
        include_patterns=inc,
        exclude_patterns=exc,
        format=format,
        workers=workers,
        delay=delay,
        rate_limit=rate_limit if rate_limit > 0 else None,
        user_agent=user_agent,
        ignore_robots=ignore_robots,
        resume_job_id=resume,
        max_attempts=max_attempts,
        adaptive_delay=adaptive_delay,
        refresh_days=refresh_days,
    )

    async def _run():
        crawler = AuthenticatedCrawler(cfg)
        out_fh = output.open("w", encoding="utf-8") if output else None
        try:
            async for r in crawler.crawl():
                rec = {
                    "url": r.url,
                    "depth": r.depth,
                    "content": r.content,
                    "content_type": r.content_type,
                    "elapsed": r.elapsed,
                    "error": r.error,
                }
                line = _json.dumps(rec, default=str)
                if out_fh:
                    out_fh.write(line + "\n")
                else:
                    print(line, flush=True)
        finally:
            if out_fh:
                out_fh.close()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
