"""Flarecrawl CLI package.

Assembles the Typer app from per-domain submodules.  The entry point
``flarecrawl.cli:app`` (declared in pyproject.toml) resolves here.
"""

from __future__ import annotations

import sys

# Force UTF-8 on Windows stdio. Without this, Rich/Typer crash with
# UnicodeEncodeError on non-ASCII output (e.g. "→" in help epilogs) because
# Python defaults stdout encoding to the system codepage (cp1252 on en-US
# Windows). Must run before any Rich/Typer output is emitted.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass  # detached stream or unsupported in this environment

# Optional: install uvloop on non-Windows platforms for 2-4x async speedup.
if sys.platform != "win32":
    try:  # pragma: no cover - platform-specific bootstrap
        import uvloop

        uvloop.install()
    except ImportError:
        pass

from typing import Annotated

import typer

from .. import __version__
from ..config import get_auth_status

# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

_APP_EPILOG = (
    "Mental model: routing escalates by difficulty. "
    "fetch (content-type aware) / scrape (browser render) for normal sites "
    "→ add --stealth (curl_cffi TLS) when TLS-fingerprinted "
    "→ --browser local --headed for CF-stub / headless detection "
    "→ recipe for repeatable multi-step flows "
    "→ p6 (mint→replay) for Akamai/Cloudflare/Imperva hard targets. "
    "Every --json result carries meta.blocked {vendor,kind,terminal}. "
    "\n\nAgents: run `flarecrawl guide` for the full orientation doc "
    "(when/why each command, JSON shapes, exit codes, footgun rules), "
    "or `flarecrawl guide <topic>` (e.g. hard-targets, json, errors, rules)."
)

app = typer.Typer(
    name="flarecrawl",
    help="Cloudflare Browser Run CLI — drop-in firecrawl replacement, much cheaper.",
    no_args_is_help=True,
    epilog=_APP_EPILOG,
)

# ---------------------------------------------------------------------------
# Top-level callbacks (--version, --status)
# ---------------------------------------------------------------------------

from rich.console import Console as _Console  # noqa: E402

_console = _Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        print(f"flarecrawl {__version__}")
        raise typer.Exit()


def _status_callback(value: bool) -> None:
    if value:
        status = get_auth_status()
        _console.print(f"flarecrawl {__version__}")
        _console.print()
        if status.get("authenticated"):
            _console.print(
                f"Auth: [green]authenticated[/green] (source: {status.get('source')})"
            )
            _console.print(f"Account: [cyan]{status.get('account_id')}[/cyan]")
        else:
            _console.print("Auth: [red]not authenticated[/red]")
            _console.print("Run: flarecrawl auth login")
        _console.print()
        _console.print("[dim]Pricing: Free 10 min/day, then $0.09/hr[/dim]")
        _console.print("[dim]Limits: Free 3 concurrent, Paid 10 concurrent browsers[/dim]")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True),
    ] = None,
    status: Annotated[
        bool | None,
        typer.Option(
            "--status",
            callback=_status_callback,
            is_eager=True,
            help="Show version, auth status, and usage info",
        ),
    ] = None,
) -> None:
    """Cloudflare Browser Run CLI — drop-in firecrawl replacement."""


# ---------------------------------------------------------------------------
# Named sub-apps (registered by add_typer)
# ---------------------------------------------------------------------------

from .auth_cmds import auth_app, cache_app, negotiate_app, rules_app  # noqa: E402
from .cdp_cmds import cdp_app  # noqa: E402
from .design_cmds import design_app  # noqa: E402
from .frontier_cmds import frontier_app  # noqa: E402
from .sessions import session_app  # noqa: E402
from .webmcp_cmds import webmcp_app  # noqa: E402

app.add_typer(auth_app, name="auth")
app.add_typer(cache_app, name="cache")
app.add_typer(negotiate_app, name="negotiate")
app.add_typer(rules_app, name="rules")
app.add_typer(session_app, name="session")
app.add_typer(cdp_app, name="cdp")
app.add_typer(webmcp_app, name="webmcp")
app.add_typer(design_app, name="design")
app.add_typer(frontier_app, name="frontier")

# ---------------------------------------------------------------------------
# Direct commands (registered via module register() functions)
# ---------------------------------------------------------------------------

from . import (  # noqa: E402
    crawl,
    discover,
    extract_cmd,
    fetch,
    guide_cmd,
    media,
    recipe,
    scrape,
    search,
    techdetect,
)
from .frontier_cmds import register as _frontier_register  # noqa: E402

guide_cmd.register(app)
scrape.register(app)
search.register(app)
fetch.register(app)
techdetect.register(app)
crawl.register(app)
extract_cmd.register(app)
media.register(app)
recipe.register(app)
discover.register(app)
# cdp interact command (direct on main app)
from .cdp_cmds import register as _cdp_register  # noqa: E402
_cdp_register(app)
# frontier direct commands (spider, authcrawl, videos)
_frontier_register(app)

# ---------------------------------------------------------------------------
# Re-exports for backward compatibility
# Tests import private helpers directly from flarecrawl.cli (the old
# single-module path). Re-export them here so existing imports continue to
# work without modification.
# ---------------------------------------------------------------------------

from ..client import Client, FlareCrawlError  # noqa: E402
from ..config import get_account_id, get_api_token  # noqa: E402
from ._common import (  # noqa: E402
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
    _sanitize_filename,
    _validate_url,
    console,
)
from .fetch import _fetch_for_tech_detect, _fetch_for_tech_detect_cdp  # noqa: E402
from .media import _extract_favicons  # noqa: E402
from .scrape import (  # noqa: E402
    _classify_url_for_organize,
    _run_then_fetch,
    _scrape_single,
    _scrape_single_cdp,
)

__all__ = [
    "app",
    # client
    "Client",
    "FlareCrawlError",
    "get_account_id",
    "get_api_token",
    # exit codes
    "EXIT_AUTH_REQUIRED",
    "EXIT_ERROR",
    "EXIT_FORBIDDEN",
    "EXIT_NOT_FOUND",
    "EXIT_RATE_LIMITED",
    "EXIT_SUCCESS",
    "EXIT_VALIDATION",
    # helpers
    "_apply_browser_cookies",
    "_apply_tech_detection",
    "_attach_tech",
    "_classify_url_for_organize",
    "_collect_response_signals",
    "_enrich_cdp_error",
    "_error",
    "_extract_favicons",
    "_fetch_for_tech_detect",
    "_fetch_for_tech_detect_cdp",
    "_filter_detections",
    "_filter_fields",
    "_filter_record_content",
    "_get_cdp_client",
    "_get_client",
    "_handle_api_error",
    "_output_json",
    "_output_ndjson",
    "_output_text",
    "_parse_auth",
    "_parse_body",
    "_parse_category_list",
    "_parse_headers",
    "_require_auth",
    "_run_then_fetch",
    "_sanitize_filename",
    "_scrape_single",
    "_scrape_single_cdp",
    "_validate_url",
    "console",
]
