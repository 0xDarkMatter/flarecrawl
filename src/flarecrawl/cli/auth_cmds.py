"""auth, cache, negotiate, rules sub-apps."""

from __future__ import annotations

from typing import Annotated

import typer

from ..client import Client, FlareCrawlError
from ..config import (
    clear_credentials,
    get_auth_status,
    save_credentials,
)
from ._common import (
    EXIT_VALIDATION,
    _error,
    _output_json,
    console,
)

auth_app = typer.Typer(help="Authentication")


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


@cache_app.command("clear")
def cache_clear():
    """Clear all cached responses.

    Example:
        flarecrawl cache clear
    """
    from .. import cache
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
    from .. import cache
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


@negotiate_app.command("status")
def negotiate_status(
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
):
    """Show markdown negotiation domain cache.

    Example:
        flarecrawl negotiate status
        flarecrawl negotiate status --json
    """
    from ..negotiate import _cache_path, _load_domain_cache
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
    from ..negotiate import clear_domain_cache
    count = clear_domain_cache()
    console.print(f"Cleared {count} domain cache entr{'ies' if count != 1 else 'y'}")


# ------------------------------------------------------------------
# rules — per-site header rulesets
# ------------------------------------------------------------------

rules_app = typer.Typer(help="Per-site header rulesets for enhanced extraction")


@rules_app.command("list")
def rules_list(
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """List all loaded rules (defaults + user overrides)."""
    from ..rules import list_rules
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
    from ..rules import load_rules
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
    from ..rules import _parse_yaml, _user_rules_path, clear_cache

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
    from ..rules import _default_rules_path, _user_rules_path
    console.print(f"Default: {_default_rules_path()}")
    console.print(f"User:    {_user_rules_path()}")


# ------------------------------------------------------------------
# scrape — matches firecrawl scrape
# ------------------------------------------------------------------


