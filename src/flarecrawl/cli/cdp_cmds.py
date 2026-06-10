"""cdp sub-app and interact command."""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from ..client import FlareCrawlError
from ..config import (
    clear_cdp_session,
    list_cdp_sessions,
)
from ._common import (
    EXIT_VALIDATION,
    _apply_browser_cookies,
    _enrich_cdp_error,
    _error,
    _get_cdp_client,
    _handle_api_error,
    _output_json,
    _validate_url,
    console,
)

cdp_app = typer.Typer(help="CDP session management")


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
        console.print("\n[bold]CDP WebSocket URL[/bold]\n")
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




# interact command uses _cmd for registration
_cmd = typer.Typer(add_completion=False)


@_cmd.command()
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
    browser_cookies: Annotated[str | None, typer.Option("--browser-cookies", help="Grab cookies from local browser (chrome|firefox)")] = None,
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

    # Grab cookies from local browser
    if browser_cookies:
        _bc_path = _apply_browser_cookies(browser_cookies, url, as_json=json_output)
        if _bc_path:
            load_cookies = _bc_path

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
        _handle_api_error(_enrich_cdp_error(e, url), json_output)
    finally:
        cdp_client.close()


# ------------------------------------------------------------------
# WebMCP commands
# ------------------------------------------------------------------



def register(app: typer.Typer) -> None:
    """Register interact command onto the main app."""
    app.command("interact")(interact)
