"""tech-detect command."""

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


def _fetch_for_tech_detect_cdp(
    url: str,
    *,
    cookies_in: list[dict] | None = None,
    custom_headers: dict[str, str] | None = None,
    proxy: str | None = None,
    timeout: float = 60.0,
    as_json: bool = False,
) -> tuple[str, dict[str, str], dict[str, str], "dict[str, str | None]"]:
    """Render a URL via Cloudflare Browser Run CDP and return (html, headers, cookies, js_globals).

    Unlocks the ~880 Wappalyzer fingerprints that only fire via a
    window-globals probe (jQuery version, Next.js buildId, React
    internals, framework-detect lib markers, ...). Reuses the same CDP
    machinery the v0.30.0 scrape `--cdp --tech-detect` path uses â€”
    `Network.responseReceived` for the main document's headers,
    `Runtime.evaluate` for the probe â€” so the JS-globals coverage is
    identical between this command and `scrape --cdp --tech-detect`.

    Costs CF browser time like any other CDP-routed command. Returns
    empty tuples on transport / CDP error.
    """
    from ..cdp import MainDocumentHeaders
    from ..wappalyzer import get_wappalyzer

    html = ""
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    js_globals: dict[str, "str | None"] = {}

    cdp_client = _get_cdp_client(as_json=as_json, proxy=proxy)
    page = None
    try:
        page = cdp_client.new_page()

        # Header collector â€” Network.responseReceived for the main document.
        # Must be subscribed before navigate, and Network domain must be
        # enabled for the event to fire.
        header_collector = MainDocumentHeaders(expected_url=url)
        cdp_client.subscribe(
            "Network.responseReceived",
            lambda p: header_collector._on_response_received(p),
        )
        page.enable_network()

        if custom_headers:
            try:
                page.send("Network.setExtraHTTPHeaders",
                          {"headers": dict(custom_headers)})
            except Exception:  # noqa: BLE001
                pass

        if cookies_in:
            try:
                page.set_cookies([c for c in cookies_in if isinstance(c, dict)])
            except Exception:  # noqa: BLE001
                pass

        try:
            page.navigate(url, wait_until="networkidle0",
                          timeout=int(timeout * 1000))
        except Exception:  # noqa: BLE001
            # A navigation timeout still leaves us a partially-loaded page
            # to probe â€” better than nothing.
            pass

        try:
            html = page.get_content() or ""
        except Exception:  # noqa: BLE001
            html = ""

        headers = dict(header_collector.headers)

        try:
            for c in page.get_cookies(urls=[url]) or []:
                name = c.get("name")
                val = c.get("value")
                if isinstance(name, str) and isinstance(val, str):
                    cookies[name] = val
        except Exception:  # noqa: BLE001
            pass

        # Inject the wappalyzer JS-globals probe (same shape as the
        # scrape --cdp --tech-detect path).
        try:
            probe = get_wappalyzer().build_js_probe()
            page.evaluate(probe, await_promise=False)
            raw = page.evaluate(
                "(function(){var e=document.getElementById('wap-probe');"
                "return e?e.textContent:'';})()",
                await_promise=False,
            )
            if isinstance(raw, str) and raw.strip().startswith("{"):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        if isinstance(k, str):
                            js_globals[k] = (
                                v if (v is None or isinstance(v, str))
                                else str(v)
                            )
        except Exception:  # noqa: BLE001 - probe is best-effort
            pass
    except Exception:  # noqa: BLE001
        return "", {}, {}, {}
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            cdp_client.close()
        except Exception:  # noqa: BLE001
            pass

    return html, headers, cookies, js_globals


def _fetch_for_tech_detect(
    url: str,
    *,
    cookies_in: list[dict] | None = None,
    custom_headers: dict[str, str] | None = None,
    proxy: str | None = None,
    stealth: bool = False,
    impersonate: str = "chrome131",
    timeout: float = 30.0,
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Single GET that yields (html, response_headers, cookies).

    The transport mirrors `flarecrawl fetch`: curl_cffi when stealth or
    session cookies are present (real Chrome JA3/JA4 fingerprint),
    httpx otherwise. Honours --proxy.

    Returns empty strings/dicts on transport error - tech-detect should
    not crash on a single bad URL.
    """
    import httpx as _httpx  # noqa: PLC0415

    NETWORK_ERRORS: tuple[type[BaseException], ...] = (
        _httpx.HTTPError,
        ConnectionError,
        OSError,
        TimeoutError,
    )

    # Note: we deliberately do NOT raise on 4xx/5xx â€” a 404 from Cloudflare
    # still carries `Server: cloudflare` + `CF-Ray:` headers that are valid
    # tech signals. Transport-level errors (connection refused, timeout,
    # TLS failure) return empty triples.
    try:
        if stealth:
            try:
                from curl_cffi import requests as _cffi  # noqa: PLC0415
                from curl_cffi.requests.exceptions import RequestException as _CFFIError  # noqa: PLC0415
            except ImportError:
                return "", {}, {}
            try:
                with _cffi.Session(impersonate=impersonate, timeout=timeout) as cs:  # type: ignore[arg-type]
                    if custom_headers:
                        cs.headers.update(custom_headers)
                    if cookies_in:
                        from ..cookies import cookies_to_httpx as _c2h  # noqa: PLC0415
                        cs.cookies = {
                            c.name: c.value
                            for c in _c2h(cookies_in).jar
                            if c.value is not None
                        }
                    if proxy:
                        cs.proxies = {"http": proxy, "https": proxy}
                    r = cs.get(url, allow_redirects=True)
                    html = r.content.decode("utf-8", errors="replace")
                    headers = {str(k): str(v) for k, v in r.headers.items()}
                    cookies = {
                        c.name: c.value for c in cs.cookies.jar
                        if c.value is not None
                    }
                    return html, headers, cookies
            except (_CFFIError, ConnectionError, OSError, TimeoutError):
                return "", {}, {}
        else:
            with _httpx.Client(
                follow_redirects=True, timeout=timeout, proxy=proxy,
            ) as session:
                if custom_headers:
                    session.headers.update(custom_headers)
                if cookies_in:
                    from ..cookies import cookies_to_httpx as _c2h  # noqa: PLC0415
                    jar = _c2h(cookies_in)
                    session.cookies = jar  # type: ignore[assignment]
                r = session.get(url)
                return (
                    r.text,
                    dict(r.headers),
                    {c.name: c.value for c in session.cookies.jar if c.value is not None},
                )
    except NETWORK_ERRORS:
        return "", {}, {}
    return "", {}, {}


@_cmd.command("tech-detect")
def tech_detect_command(
    urls: Annotated[
        list[str] | None,
        typer.Argument(help="URL(s) to identify. With --stdin, omit URLs and pipe HTML on stdin."),
    ] = None,
    stdin_mode: Annotated[bool, typer.Option("--stdin", help="Read HTML from stdin (no network).")] = False,
    input_file: Annotated[
        Path | None,
        typer.Option("--input", "-i", help="File with URLs (one per line)"),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Cookie file or @NAME for saved session"),
    ] = None,
    auth: Annotated[
        str | None,
        typer.Option("--auth", help="HTTP Basic Auth (user:password)"),
    ] = None,
    headers_opt: Annotated[
        list[str] | None,
        typer.Option("--headers", help="Custom HTTP request headers (Key: Value)"),
    ] = None,
    user_agent: Annotated[
        str | None,
        typer.Option("--user-agent", help="Custom User-Agent string"),
    ] = None,
    proxy: Annotated[
        str | None,
        typer.Option("--proxy", help="Proxy URL (http/https/socks5)"),
    ] = None,
    stealth: Annotated[
        bool,
        typer.Option("--stealth", help="Use browser TLS fingerprint (curl_cffi)."),
    ] = False,
    impersonate: Annotated[
        str,
        typer.Option("--impersonate", help="curl_cffi browser profile when --stealth."),
    ] = "chrome131",
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Per-URL fetch timeout (seconds)."),
    ] = 30.0,
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", help="Parallel workers for multi-URL"),
    ] = 5,
    min_confidence: Annotated[
        int,
        typer.Option("--min-confidence", help="Drop detections below this score (0-100)"),
    ] = 0,
    only_categories: Annotated[
        str | None,
        typer.Option("--only-categories", help="Comma-separated category names to keep (e.g. 'CMS,Frameworks')"),
    ] = None,
    exclude_categories: Annotated[
        str | None,
        typer.Option("--exclude-categories", help="Comma-separated category names to drop (e.g. 'Analytics,Tag managers')"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON envelope"),
    ] = False,
    ndjson: Annotated[
        bool,
        typer.Option("--ndjson", help="Stream one JSON record per line (multi-URL)"),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output file path"),
    ] = None,
    cdp: Annotated[
        bool,
        typer.Option(
            "--cdp",
            help=("Render the page via Cloudflare Browser Run CDP and inject "
                  "the JS-globals probe. Unlocks ~880 Wappalyzer fingerprints "
                  "that only fire via window globals (jQuery version, "
                  "Next.js buildId, framework-detect markers, ...). Costs CF "
                  "browser time like any other --cdp command. Requires auth."),
        ),
    ] = False,
) -> None:
    """Identify the technologies a page is built on.

    Local Wappalyzer fingerprint matching over fetched HTML + HTTP
    response headers + cookies. Roughly 7,500 technologies covered;
    detection adds zero CF browser time and zero CF API calls (the
    fetch itself uses your own connection, optionally with --stealth /
    --proxy / --session).

    Output (JSON):

        {
          "data": [
            {
              "url": "...",
              "technologies": [
                {"name": "WordPress", "version": "6.4",
                 "categories": ["CMS"], "groups": ["Content"]},
                ...
              ]
            }
          ],
          "meta": {"count": N}
        }

    Text mode prints a compact table per URL.

    Example:
        flarecrawl tech-detect https://example.com
        flarecrawl tech-detect https://a.com https://b.com --json
        flarecrawl tech-detect -i urls.txt -w 10 --only-categories CMS,Frameworks
        flarecrawl tech-detect https://example.com --stealth --proxy http://...
        cat page.html | flarecrawl tech-detect --stdin --json
    """
    if not stdin_mode and not urls and not input_file:
        _error(
            "Provide URL(s), --input FILE, or --stdin.",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    only_cats = _parse_category_list(only_categories)
    excl_cats = _parse_category_list(exclude_categories)

    # ---- stdin: detect from piped HTML --------------------------------
    if stdin_mode:
        html = sys.stdin.read()
        rec: dict = {"url": "(stdin)"}
        _attach_tech(
            rec,
            html=html,
            emit_summary=not json_output,
            min_confidence=min_confidence,
            only_categories=only_cats,
            exclude_categories=excl_cats,
        )
        techs = rec.get("technologies", [])
        if json_output:
            payload = {"data": [{"url": "(stdin)", "technologies": techs}],
                       "meta": {"count": 1}}
            if output:
                output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                console.print(f"Saved to {output}")
            else:
                _output_json(payload)
        else:
            _print_tech_table("(stdin)", techs)
        return

    # ---- collect URL list --------------------------------------------
    all_urls: list[str] = list(urls or [])
    if input_file:
        try:
            file_urls = parse_batch_file(input_file)
        except (OSError, ValueError) as e:
            _error(
                f"Cannot read --input: {e}",
                "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
            )
            return
        all_urls.extend(u for u in file_urls if isinstance(u, str))
    if not all_urls:
        _error(
            "No URLs to detect.",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    # ---- session/auth/headers/proxy -----------------------------------
    _session_cookies: list[dict] | None = None
    if session:
        if session.startswith("@"):
            from ..config import load_session as _load_session  # noqa: PLC0415
            try:
                _session_cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(
                    f"Session not found: {session[1:]}",
                    "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output,
                )
                return
        else:
            from ..cookies import load_cookies as _load_cookies  # noqa: PLC0415
            try:
                _session_cookies = _load_cookies(Path(session))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _error(
                    f"Cannot read session file: {e}",
                    "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
                )
                return

    parsed_headers = _parse_headers(headers_opt, json_output) or {}
    if user_agent:
        parsed_headers.setdefault("User-Agent", user_agent)
    if auth:
        if ":" not in auth:
            _error(
                "Invalid --auth format. Expected user:password",
                "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
            )
            return
        import base64 as _b64  # noqa: PLC0415
        u, _, p = auth.partition(":")
        parsed_headers.setdefault(
            "Authorization",
            "Basic " + _b64.b64encode(f"{u}:{p}".encode()).decode(),
        )

    from ..config import get_proxy as _gp  # noqa: PLC0415
    effective_proxy = proxy or _gp()

    # ---- detect per URL ----------------------------------------------
    def _one(target: str) -> dict:
        js_globals: "dict[str, str | None] | None" = None
        if cdp:
            html, hdrs, cks, js_globals = _fetch_for_tech_detect_cdp(
                target,
                cookies_in=_session_cookies,
                custom_headers=parsed_headers or None,
                proxy=effective_proxy,
                timeout=max(timeout, 60.0),
                as_json=json_output,
            )
            js_globals = js_globals or None
        else:
            html, hdrs, cks = _fetch_for_tech_detect(
                target,
                cookies_in=_session_cookies,
                custom_headers=parsed_headers or None,
                proxy=effective_proxy,
                stealth=stealth,
                impersonate=impersonate,
                timeout=timeout,
            )
        rec: dict = {"url": target}
        if not (html or hdrs or cks or js_globals):
            rec["technologies"] = []
            rec["error"] = "fetch failed"
            return rec
        _attach_tech(
            rec,
            html=html,
            headers=hdrs or None,
            cookies=cks or None,
            js_globals=js_globals,
            min_confidence=min_confidence,
            only_categories=only_cats,
            exclude_categories=excl_cats,
        )
        rec.setdefault("technologies", [])
        return rec

    if ndjson:
        # Stream mode: one record per line as completed
        if len(all_urls) > 1:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(workers, DEFAULT_MAX_WORKERS),
            ) as pool:
                futures = {pool.submit(_one, u): u for u in all_urls}
                for fut in concurrent.futures.as_completed(futures):
                    _output_ndjson(fut.result())
        else:
            _output_ndjson(_one(all_urls[0]))
        return

    if len(all_urls) > 1:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, DEFAULT_MAX_WORKERS),
        ) as pool:
            future_to_url = {pool.submit(_one, u): u for u in all_urls}
            results = []
            for fut in concurrent.futures.as_completed(future_to_url):
                results.append(fut.result())
        # Preserve input order
        order = {u: i for i, u in enumerate(all_urls)}
        results.sort(key=lambda r: order.get(r.get("url", ""), 0))
    else:
        results = [_one(all_urls[0])]

    if json_output:
        payload = {"data": results, "meta": {"count": len(results)}}
        if output:
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            console.print(f"Saved to {output}")
        else:
            _output_json(payload)
    else:
        for r in results:
            _print_tech_table(r["url"], r.get("technologies", []), error=r.get("error"))


def _print_tech_table(url: str, techs: list[dict], *, error: str | None = None) -> None:
    """Pretty-print one URL's detections to the console."""
    from rich.table import Table  # noqa: PLC0415

    title = url
    if error:
        console.print(f"[red]âœ—[/red] {title}: {error}")
        return
    if not techs:
        console.print(f"[dim]{title}: no technologies detected[/dim]")
        return
    table = Table(title=title, title_style="bold", show_lines=False, expand=False)
    table.add_column("Technology", style="bold")
    table.add_column("Version", style="cyan")
    table.add_column("Categories")
    table.add_column("Conf", justify="right", style="dim")
    for t in techs:
        table.add_row(
            t.get("name", ""),
            t.get("version", ""),
            ", ".join(t.get("categories", [])),
            str(t.get("confidence", 100)),
        )
    console.print(table)


# ------------------------------------------------------------------
# crawl â€” matches firecrawl crawl
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('tech-detect')(tech_detect_command)
