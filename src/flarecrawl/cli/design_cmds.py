"""design sub-app — extract design systems from websites."""

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


design_app = typer.Typer(help="Extract design systems from websites")


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
    browser_cookies: Annotated[str | None, typer.Option("--browser-cookies", help="Grab cookies from local browser (chrome|firefox)")] = None,
):
    """Extract design tokens from a website into DESIGN.md or HTML preview."""
    from ..design import EXTRACT_JS, format_design_md, format_preview_html, process_tokens, score_coherence

    _validate_url(url, json_output)

    # Grab cookies from local browser
    if browser_cookies:
        _bc_path = _apply_browser_cookies(browser_cookies, url, as_json=json_output)
        if _bc_path:
            session = _bc_path

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
                output.write_text(html, encoding="utf-8")
                console.print(f"[dim]Preview saved to {output}[/dim]")
            else:
                print(html)
        else:
            md = format_design_md(tokens, coherence, url)
            if output:
                output.write_text(md, encoding="utf-8")
                console.print(f"[dim]DESIGN.md saved to {output}[/dim]")
            else:
                print(md)

        page.close()
    except FlareCrawlError as e:
        _handle_api_error(_enrich_cdp_error(e, url), json_output)
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
    from ..design import EXTRACT_JS, process_tokens, score_coherence

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
        _handle_api_error(_enrich_cdp_error(e, url), json_output)
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
    from ..design import EXTRACT_JS, process_tokens, score_coherence

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
        _handle_api_error(_enrich_cdp_error(e), json_output)
    finally:
        cdp_client.close()


# ---------------------------------------------------------------------
# Frontier v2 ops subcommands
# ---------------------------------------------------------------------

