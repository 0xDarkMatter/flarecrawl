"""session sub-app — saved cookie session management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ._common import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _error,
    _output_json,
    _validate_url,
    console,
)

session_app = typer.Typer(help="Saved cookie session management")


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
    from ..config import save_session as _save
    from ..cookies import load_cookies

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
    from ..config import list_sessions as _list

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
    from ..config import load_session as _load

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
    from ..config import delete_session as _delete

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
    from ..config import load_session as _load
    from ..cookies import validate_cookies

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


@session_app.command("inspect")
def session_inspect(
    jar: Annotated[str, typer.Argument(help="Session name, @name, or path to a cookie jar file")],
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    expiring_threshold: Annotated[int, typer.Option("--expiring-threshold", help="Seconds-to-expiry below which a shell cookie counts as expiring")] = 300,
):
    """Inspect a cookie jar's freshness offline (no network).

    Classifies anti-bot shell cookies (Akamai _abck/bm_*, Cloudflare
    __cf_bm/cf_clearance, Imperva visid_incap_*, DataDome, PerimeterX),
    computes TTLs, and returns a verdict: fresh | stale | expired | empty.

    Lets a connector re-mint proactively instead of after a block.
    Exit code is non-zero unless the verdict is 'fresh' (and 0 for an
    empty jar with no shells), so scripts can branch on it.

    Example:
        flarecrawl session inspect @ampol
        flarecrawl session inspect ./jar.json --json
    """
    from ..jarhealth import inspect_jar

    # Resolve jar source: @name / bare name → saved session; else file path.
    cookies: list[dict]
    if jar.startswith("@"):
        from ..config import load_session as _load_session
        try:
            cookies = _load_session(jar[1:])
        except FileNotFoundError:
            _error(f"Session not found: {jar[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
            return
    else:
        p = Path(jar)
        if p.exists():
            from ..cookies import load_cookies
            try:
                cookies = load_cookies(p)
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _error(f"Cannot read jar: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)
                return
        else:
            from ..config import load_session as _load_session
            try:
                cookies = _load_session(jar)
            except FileNotFoundError:
                _error(f"No jar file or saved session named: {jar}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
                return

    health = inspect_jar(cookies, expiring_threshold=float(expiring_threshold))

    if json_output:
        _output_json({"data": health.as_dict(), "meta": {"jar": jar}})
    else:
        colour = {
            "fresh": "green", "stale": "yellow",
            "expired": "red", "empty": "dim",
        }.get(health.verdict, "white")
        console.print(f"Jar: [bold]{jar}[/bold]")
        console.print(f"Verdict: [{colour}]{health.verdict}[/{colour}]  "
                       f"({health.cookie_count} cookies, {health.shell_count} anti-bot shells)")
        if health.vendors:
            console.print(f"Vendors: {', '.join(health.vendors)}")
        if health.expired_shells:
            console.print(f"Expired shells: [red]{', '.join(health.expired_shells)}[/red]")
        if health.expiring_shells:
            console.print(f"Expiring shells: [yellow]{', '.join(health.expiring_shells)}[/yellow]")
        for c in health.cookies:
            if c.is_shell:
                ttl = "session" if c.ttl_seconds is None else f"{c.ttl_seconds / 60:.0f}m"
                console.print(f"  [cyan]{c.name}[/cyan] ({c.vendor}) {c.state} ttl={ttl}")

    # Exit non-zero unless safe to replay. Empty jar with no shells → still
    # signal not-fresh so a connector knows to mint.
    if health.verdict != "fresh":
        raise typer.Exit(EXIT_ERROR)


# ------------------------------------------------------------------
# cdp — CDP session management
# ------------------------------------------------------------------

