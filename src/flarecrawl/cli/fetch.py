"""fetch command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ..config import (
    DEFAULT_CACHE_TTL,
)
from .scrape import _scrape_single
from ._common import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _attach_tech,
    _error,
    _get_cdp_client,
    _get_client,
    _output_json,
    _output_text,
    _parse_headers,
    _require_auth,
    _validate_url,
    console,
)

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command()
def fetch(
    url: Annotated[str, typer.Argument(help="URL to fetch")],
    session: Annotated[str | None, typer.Option("--session", help="Cookie file or @NAME for saved session")] = None,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers (Key: Value)")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file path")] = None,
    stealth: Annotated[bool, typer.Option("--stealth", help="Use browser TLS fingerprint (requires curl_cffi). Bypasses JA3/JA4 fingerprinting.")] = False,
    paywall: Annotated[bool, typer.Option("--paywall", help="Apply paywall cascade (stealth fetch, archive fallbacks). Implies --stealth for binaries.")] = False,
    impersonate: Annotated[str, typer.Option("--impersonate", help="curl_cffi browser profile (chrome131, chrome120, safari17, etc.)")] = "chrome131",
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL (http/https/socks5)")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing files")] = False,
    tech_detect: Annotated[bool, typer.Option("--tech-detect", help="Wappalyzer tech detection (HTML + response headers + cookies from the same transport). HTML branch only.")] = False,
):
    """Fetch a URL with content-type awareness.

    Auto-routes by content type: binary files (PDF, ZIP, ...) download to
    disk; JSON is pretty-printed; raw text (XML/CSV/RSS/KML/YAML/...) is
    returned verbatim; HTML is converted to markdown via CF Browser Rendering.

    Backend / output flags are orthogonal:
      - --session (file or @name) implies the curl_cffi TLS path (a session
        jar is for anti-bot replay — it carries a real Chrome JA3/JA4
        fingerprint). --stealth/--impersonate force it explicitly.
      - --json is output-format only. It never downgrades the backend, and
        it JSON-sniffs the body even when the server mislabels it
        application/octet-stream (no more files named "download").
      - With --json, meta.blocked carries a machine-readable bot-wall
        verdict {blocked, vendor, kind, terminal, signal} for the text and
        HTML branches.

    Example:
        flarecrawl fetch https://example.com/file.pdf -o file.pdf
        flarecrawl fetch https://example.com --session cookies.json
        flarecrawl fetch https://example.com --session @mysession
        flarecrawl fetch https://api.example.com/data.json --json
        flarecrawl fetch https://api.example.com/data --session @site --json
    """
    from ..fetch import build_session, detect_content_type, download_binary, download_binary_stealth

    _validate_url(url, json_output)
    # --session implies --stealth: a session jar is specifically for curl_cffi
    # TLS-replay anti-bot (Akamai P6, Cloudflare, Imperva). There is no point
    # loading a cookie jar and then making a plain httpx request — the TLS
    # fingerprint would be rejected before the cookies are even sent.
    # --paywall also implies stealth for the binary path.
    use_stealth = stealth or paywall or bool(session)
    if session and not stealth and not paywall:
        console.print("[dim]--session detected — implying curl_cffi TLS path (use --stealth to suppress this note)[/dim]")

    # Resolve session cookies
    _cookies = None
    if session:
        if session.startswith("@"):
            from ..config import load_session as _load_session
            try:
                _cookies = _load_session(session[1:])
            except FileNotFoundError:
                _error(f"Session not found: {session[1:]}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
        else:
            from ..cookies import load_cookies
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
    from ..config import get_proxy
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
        console.print(f"[dim]Probing {url}{'  [stealth]' if use_stealth else ''}...[/dim]")
        info = detect_content_type(
            url, session=http_session, headers=custom_headers,
            stealth=use_stealth, impersonate=impersonate,
        )

        def _stealth_get_bytes(target_url: str) -> bytes:
            """GET via curl_cffi, returning raw response bytes.

            Used when --stealth / --session is active so the request carries a
            real-Chrome TLS fingerprint (JA3/JA4) through to the server.  Falls
            back to raising ImportError when curl_cffi is not installed.
            """
            from curl_cffi import requests as cffi_requests  # noqa: PLC0415
            with cffi_requests.Session(impersonate=impersonate, timeout=60) as cs:
                if custom_headers:
                    cs.headers.update(custom_headers)
                if _cookies:
                    from ..cookies import cookies_to_httpx as _c2h
                    cs.cookies = {c.name: c.value for c in _c2h(_cookies).jar}
                if effective_proxy:
                    cs.proxies = {"http": effective_proxy, "https": effective_proxy}
                r = cs.get(target_url, allow_redirects=True)
                r.raise_for_status()
                return r.content

        if info.is_binary:
            # T2b: When the caller explicitly requests JSON output and the URL
            # filename doesn't look like a real binary (e.g. application/octet-stream
            # serving a JSON API response), attempt a JSON-parse fetch before
            # falling through to a file download.
            from ..fetch import _filename_looks_binary
            if json_output and not _filename_looks_binary(info.filename):
                # Only the fetch+parse is guarded — a parse/transport failure
                # falls through to the normal binary download. Output writes
                # happen *after* so a disk error isn't silently swallowed.
                _parsed = None
                try:
                    if use_stealth:
                        _body = _stealth_get_bytes(url)
                    else:
                        _r = http_session.get(url)
                        _r.raise_for_status()
                        _body = _r.content
                    _parsed = json.loads(_body)
                except Exception:
                    _parsed = None  # not JSON — fall through to binary download
                if _parsed is not None:
                    if output:
                        output.write_text(json.dumps(_parsed, indent=2), encoding="utf-8")
                        console.print(f"[green]Saved:[/green] {output}")
                    else:
                        _output_json({"data": _parsed, "meta": {"url": url, "content_type": info.content_type}})
                    http_session.close()
                    return

            # Binary download
            out_path = output or Path(info.filename or "download")
            if out_path.exists() and not overwrite:
                _error(f"File exists: {out_path} (use --overwrite)", "VALIDATION_ERROR", EXIT_VALIDATION,
                       as_json=json_output)

            console.print(f"[dim]Downloading {info.content_type}"
                          f"{f' ({info.size / 1024 / 1024:.1f} MB)' if info.size and info.size > 1024 * 1024 else ''}"
                          f"{' [stealth]' if use_stealth else ''}[/dim]")

            def _do_download(progress_cb=None):
                if use_stealth:
                    return download_binary_stealth(
                        url, out_path,
                        cookies=_cookies,
                        headers=custom_headers,
                        proxy=effective_proxy,
                        impersonate=impersonate,
                        progress_callback=progress_cb,
                    )
                return download_binary(url, http_session, out_path, progress_callback=progress_cb)

            # Progress bar for large files
            if info.size and info.size > 1024 * 1024:
                from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn
                with Progress(BarColumn(), DownloadColumn(), TransferSpeedColumn(), console=console) as progress:
                    task = progress.add_task("Downloading", total=info.size)
                    result = _do_download(
                        progress_cb=lambda n: progress.update(task, completed=n),
                    )
            else:
                result = _do_download()

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
            # JSON response — use curl_cffi when stealth so the TLS fingerprint
            # matches the probe (avoids being blocked at the GET after passing HEAD).
            try:
                if use_stealth:
                    _body = _stealth_get_bytes(url)
                    try:
                        data = json.loads(_body)
                    except ValueError:
                        data = _body.decode("utf-8", errors="replace")
                else:
                    resp = http_session.get(url)
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except ValueError:
                        data = resp.text
            except ImportError:
                # curl_cffi not installed — fall back to httpx
                resp = http_session.get(url)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError:
                    data = resp.text
            if output:
                output.write_text(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data), encoding="utf-8")
                console.print(f"[green]Saved:[/green] {output}")
            elif json_output:
                _output_json({"data": data, "meta": {"url": url, "content_type": info.content_type}})
            else:
                _output_json(data)

        elif not info.is_html:
            # Non-HTML text (XML, KML, CSV, YAML, plain text, etc.) — return raw body.
            # Do NOT attempt CF Browser Rendering markdown conversion on non-HTML content.
            console.print(f"[dim]Text content ({info.content_type}) — fetching raw...[/dim]")
            try:
                if use_stealth:
                    _body = _stealth_get_bytes(url)
                    body = _body.decode("utf-8", errors="replace")
                else:
                    resp = http_session.get(url, headers=custom_headers or {})
                    resp.raise_for_status()
                    body = resp.text
            except ImportError:
                resp = http_session.get(url, headers=custom_headers or {})
                resp.raise_for_status()
                body = resp.text
            if output:
                output.write_text(body, encoding="utf-8")
                console.print(f"[green]Saved:[/green] {output}")
            elif json_output:
                from ..blockdetect import detect_block
                _blk = detect_block(0, {}, body).as_dict()
                _output_json({"data": body, "meta": {
                    "url": url, "content_type": info.content_type,
                    "blocked": _blk}})
            else:
                _output_text(body)

        else:
            # HTML — convert to markdown via CF Browser Rendering
            console.print("[dim]HTML content — converting to markdown...[/dim]")
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

            # --tech-detect on HTML branch: fetch raw HTML alongside the
            # markdown conversion so Wappalyzer can see <script>, <meta>,
            # DOM, and class signals (markdown strips all of those). Also
            # capture the response headers + cookie jar from the same call
            # so header-only fingerprints (Server: cloudflare,
            # X-Powered-By: PHP/8.2, X-Drupal-Cache, ...) and cookie-only
            # fingerprints (PHPSESSID, wp_*, _ga_*) fire too. Cheap (no
            # CF browser time) - reuses the existing transport session.
            if tech_detect:
                _tech_headers: dict[str, str] = {}
                _tech_cookies: dict[str, str] = {}
                _tech_html = ""
                try:
                    if use_stealth:
                        from curl_cffi import requests as _cffi_req  # noqa: PLC0415
                        with _cffi_req.Session(impersonate=impersonate, timeout=60) as _cs:  # type: ignore[arg-type]
                            if custom_headers:
                                _cs.headers.update(custom_headers)
                            if _cookies:
                                from ..cookies import cookies_to_httpx as _c2h  # noqa: PLC0415
                                _cs.cookies = {c.name: c.value for c in _c2h(_cookies).jar}
                            if effective_proxy:
                                _cs.proxies = {"http": effective_proxy, "https": effective_proxy}
                            _gr = _cs.get(url, allow_redirects=True)
                            _gr.raise_for_status()
                            _tech_html = _gr.content.decode("utf-8", errors="replace")
                            _tech_headers = {str(k): str(v) for k, v in _gr.headers.items()}
                            _tech_cookies = {
                                c.name: c.value for c in _cs.cookies.jar
                                if c.value is not None
                            }
                    else:
                        _r = http_session.get(url, headers=custom_headers or {})
                        _r.raise_for_status()
                        _tech_html = _r.text
                        _tech_headers = dict(_r.headers)
                        _tech_cookies = {
                            c.name: c.value for c in http_session.cookies.jar
                            if c.value is not None
                        }
                    _attach_tech(
                        result,
                        html=_tech_html,
                        headers=_tech_headers,
                        cookies=_tech_cookies,
                        emit_summary=not json_output,
                    )
                except Exception:
                    pass  # tech detection is best-effort, never fails the fetch

            if output:
                output.write_text(content, encoding="utf-8")
                console.print(f"[green]Saved:[/green] {output}")
            elif json_output:
                from ..blockdetect import detect_block
                _blk = detect_block(0, {}, content if isinstance(content, str) else "").as_dict()
                _output_json({"data": result, "meta": {
                    "url": url, "format": "markdown", "blocked": _blk}})
            else:
                _output_text(content)

    except Exception as e:
        # Catch httpx.HTTPError, RuntimeError from stealth path, and any
        # other transport errors. (httpx is imported lazily inside fetch.py;
        # importing at module top would break test isolation.)
        import httpx as _httpx
        if isinstance(e, _httpx.HTTPError) or isinstance(e, RuntimeError):
            _error(f"HTTP error: {e}", "ERROR", EXIT_ERROR, as_json=json_output)
        elif isinstance(e, SystemExit):
            raise
        else:
            _error(f"Unexpected error: {e}", "ERROR", EXIT_ERROR, as_json=json_output)
    finally:
        http_session.close()


# ------------------------------------------------------------------
# tech-detect - dedicated Wappalyzer fingerprint subcommand
# ------------------------------------------------------------------


def _fetch_for_tech_detect_cdp(
    url: str,
    *,
    cookies_in: list[dict] | None = None,
    custom_headers: dict[str, str] | None = None,
    proxy: str | None = None,
    timeout: float = 60.0,
    as_json: bool = False,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str | None]]:
    """Render a URL via Cloudflare Browser Run CDP and return (html, headers, cookies, js_globals).

    Unlocks the ~880 Wappalyzer fingerprints that only fire via a
    window-globals probe (jQuery version, Next.js buildId, React
    internals, framework-detect lib markers, ...). Reuses the same CDP
    machinery the v0.30.0 scrape `--cdp --tech-detect` path uses —
    `Network.responseReceived` for the main document's headers,
    `Runtime.evaluate` for the probe — so the JS-globals coverage is
    identical between this command and `scrape --cdp --tech-detect`.

    Costs CF browser time like any other CDP-routed command. Returns
    empty tuples on transport / CDP error.
    """
    from ..cdp import MainDocumentHeaders
    from ..wappalyzer import get_wappalyzer

    html = ""
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    js_globals: dict[str, str | None] = {}

    cdp_client = _get_cdp_client(as_json=as_json, proxy=proxy)
    page = None
    try:
        page = cdp_client.new_page()

        # Header collector — Network.responseReceived for the main document.
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
            # to probe — better than nothing.
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

    # Note: we deliberately do NOT raise on 4xx/5xx — a 404 from Cloudflare
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




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('fetch')(fetch)
