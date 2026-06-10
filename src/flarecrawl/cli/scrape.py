"""scrape command."""

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

from .. import __version__
from ..batch import parse_batch_file, process_batch
from ..client import MOBILE_PRESET, Client, FlareCrawlError
from ..config import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_WORKERS,
    get_account_id,
    get_api_token,
    save_cdp_session,
)
from ._common import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _apply_browser_cookies,
    _apply_tech_detection,
    _attach_tech,
    _classify_url_for_organize,
    _collect_response_signals,
    _enrich_cdp_error,
    _error,
    _filter_fields,
    _get_cdp_client,
    _get_client,
    _handle_api_error,
    _output_json,
    _output_ndjson,
    _output_text,
    _parse_auth,
    _parse_body,
    _parse_headers,
    _run_then_fetch,
    _sanitize_filename,
    _validate_url,
    console,
)

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


def _classify_url_for_organize(url: str, mode: str) -> str:
    """Pick a subdirectory name for a URL given an organize-by mode.

    Modes:
        flat        → "" (everything in then_fetch_output)
        extension   → "pdfs" / "images" / "videos" / "docs" / "other"
        content-type → "image" / "application" / "text" / "video" / "audio"
                      (best-effort from the URL extension; refined by
                      Content-Type at fetch time isn't worth the round trip)
        thumbnail   → war.gov-style: pull URLs containing "/thumbnail/" into
                      a thumbnails/ subdir; everything else by extension.
    """

    if mode in (None, "flat"):
        return ""
    name = Path(urlparse(url.split("?")[0]).path).name.lower()
    ext = Path(name).suffix or ""

    is_thumb = "/thumbnail/" in url.lower() or "thumbnail" in name

    if mode == "thumbnail":
        if is_thumb:
            return "thumbnails"
        # fall through to extension behaviour
        mode = "extension"

    if mode == "extension":
        if ext == ".pdf":
            return "pdfs"
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
            return "images"
        if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".m3u8"):
            return "videos"
        if ext in (".doc", ".docx", ".xlsx", ".csv", ".xls", ".ppt", ".pptx", ".txt"):
            return "docs"
        return "other"

    if mode == "content-type":
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
            return "image"
        if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
            return "video"
        if ext in (".mp3", ".wav", ".ogg"):
            return "audio"
        if ext in (".pdf", ".doc", ".docx", ".xlsx", ".csv"):
            return "application"
        return "other"

    return ""


def _run_then_fetch(
    *,
    cdp_client: CDPClient,
    then_fetch: str | None,
    then_fetch_from: Path | None,
    then_fetch_column: str | None,
    then_fetch_output: Path,
    then_fetch_workers: int,
    json_output: bool,
    then_fetch_organize_by: str | None = None,
) -> dict:
    """v0.24.0 P2.3: mass-download URLs reusing browser session + stealth TLS.

    Cookies extracted from the live CDP browser are handed off to a
    curl_cffi thread pool (Chrome 131 impersonation). Resume-safe — files
    that already exist with non-zero size are skipped.

    Returns a summary dict for inclusion in scrape result metadata.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..fetch import download_binary_stealth

    # ── 1. Resolve URL list ──────────────────────────────────────────────
    urls: list[str] = []
    if then_fetch:
        urls.extend(u.strip() for u in then_fetch.split(",") if u.strip())
    if then_fetch_from:
        if not then_fetch_from.exists():
            _error(
                f"--then-fetch-from file not found: {then_fetch_from}",
                "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output,
            )
        text = then_fetch_from.read_text(encoding="utf-8-sig")
        if then_fetch_column:
            # CSV mode
            import csv
            import io
            reader = csv.DictReader(io.StringIO(text))
            if then_fetch_column not in (reader.fieldnames or []):
                _error(
                    f"Column '{then_fetch_column}' not found. "
                    f"Available: {reader.fieldnames}",
                    "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
                )
            for row in reader:
                val = (row.get(then_fetch_column) or "").strip()
                if val and val.lower().startswith(("http://", "https://")):
                    urls.append(val)
        else:
            # One URL per line
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    # Dedupe while preserving order
    seen: set = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    urls = deduped

    if not urls:
        _error(
            "--then-fetch produced no URLs to download",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output,
        )

    # ── 2. Extract cookies via a temporary page ──────────────────────────
    cookies: list[dict] = []
    try:
        cookie_page = cdp_client.new_page()
        try:
            cookies = cookie_page.get_cookies()
        finally:
            cookie_page.close()
    except Exception as e:
        if not json_output:
            console.print(f"[yellow]Warning: cookie extraction failed ({e}); proceeding without[/yellow]")

    # ── 3. Output dir ────────────────────────────────────────────────────
    then_fetch_output.mkdir(parents=True, exist_ok=True)
    if not json_output:
        console.print(
            f"[dim]then-fetch: {len(urls)} URLs, {then_fetch_workers} workers, "
            f"output={then_fetch_output}[/dim]"
        )

    # ── 4. Parallel downloads ───────────────────────────────────────────
    def _do_one(url: str) -> dict:
        name = Path(urlparse(url.split("?")[0]).path).name or "download"
        subdir = _classify_url_for_organize(url, then_fetch_organize_by or "flat")
        dest_dir = then_fetch_output / subdir if subdir else then_fetch_output
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            return {"status": "skip", "url": url, "path": str(dest), "size": dest.stat().st_size}
        try:
            result = download_binary_stealth(url, dest, cookies=cookies)
            return {"status": "ok", "url": url, "path": str(result.path), "size": result.size}
        except Exception as e:
            return {"status": "error", "url": url, "error": {"code": "FETCH_ERROR", "message": str(e)[:200]}}

    ok_count = 0
    skip_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=then_fetch_workers) as pool:
        futures = {pool.submit(_do_one, u): u for u in urls}
        for fut in as_completed(futures):
            res = fut.result()
            # Stream NDJSON output for batch-style consumption
            if json_output:
                _output_ndjson(res)
            else:
                status = res["status"]
                url_short = res["url"][:80]
                if status == "ok":
                    ok_count += 1
                    console.print(f"  [green]ok[/green]    {url_short} ({res['size']:,} bytes)")
                elif status == "skip":
                    skip_count += 1
                    console.print(f"  [dim]skip[/dim]  {url_short}")
                else:
                    fail_count += 1
                    msg = res.get("error", {}).get("message", "unknown")
                    console.print(f"  [red]fail[/red]  {url_short}: {msg}")

    summary = {
        "total": len(urls),
        "ok": ok_count if not json_output else None,
        "skip": skip_count if not json_output else None,
        "fail": fail_count if not json_output else None,
        "output_dir": str(then_fetch_output),
        "cookies_used": len(cookies),
    }
    if not json_output:
        console.print(
            f"[dim]then-fetch done: {ok_count} ok, {skip_count} skipped, "
            f"{fail_count} failed[/dim]"
        )
    return {k: v for k, v in summary.items() if v is not None}


def _scrape_single_cdp(
    cdp_client: CDPClient,
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
    page: SyncCDPPage | None = None,
    skip_navigation: bool = False,
    capture_patterns: list[str] | None = None,
    capture_dir: Path | None = None,
    capture_content_types: list[str] | None = None,
    auto_data: bool = True,
    humanize: bool = False,
    humanize_profile: str = "fast",
    tech_detect: bool = False,
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
        body_capture = None
        data_probe = None
        _har_written = False
        _bodies_fetched = False
        if capture_patterns and capture_dir:
            from ..cdp import BodyCapture
            body_capture = BodyCapture(
                patterns=capture_patterns,
                output_dir=capture_dir,
                content_types=capture_content_types,
            )
        if auto_data:
            from ..cdp import DataSourceProbe
            data_probe = DataSourceProbe(page_origin=url)
        if har_output or body_capture or data_probe:
            collector = page.enable_network(body_capture=body_capture, data_probe=data_probe)

        # --tech-detect: capture the main document's response headers so
        # Wappalyzer can fire header-only fingerprints (Server: cloudflare,
        # X-Powered-By, X-Drupal-Cache, ...). Cookies + JS globals come
        # after page load (below).
        tech_headers_collector = None
        if tech_detect:
            from ..cdp import MainDocumentHeaders
            tech_headers_collector = MainDocumentHeaders(expected_url=url)
            cdp_client.subscribe(
                "Network.responseReceived",
                lambda p: tech_headers_collector._on_response_received(p),
            )
            # Network domain must be enabled for responseReceived to fire.
            # If collector wasn't built above, enable a no-op one here.
            if collector is None:
                collector = page.enable_network()

        # v0.24.0 P2.2a: apply stealth patches before any navigation. Cheap
        # (one CDP message), idempotent, fails open if asset missing.
        if not skip_navigation:
            try:
                page.apply_stealth()
            except Exception:
                pass  # Stealth is best-effort; never fail the scrape over it

        if load_cookies:
            cookies = json.loads(load_cookies.read_text(encoding="utf-8"))
            page.set_cookies(cookies)

        if not skip_navigation:
            wait_until = "networkidle0" if scroll else "load"
            page.navigate(url, wait_until=wait_until, timeout=timeout or 30000)

        # v0.26.0 P1: humanize before any meaningful click/eval to defeat
        # behavioural-fingerprint engines (Akamai BMP, DataDome, PerimeterX).
        # Cheap when not needed (~700ms), critical for hard targets.
        if humanize:
            try:
                from ..humanize import humanize_page
                humanize_page(page, profile=humanize_profile)
            except Exception:
                pass  # Humanize is best-effort; never fail the scrape

        if wait_for_selector:
            _wfs_timeout = timeout or 30000
            try:
                page.wait_for_selector(wait_for_selector, timeout=_wfs_timeout)
            except Exception as _wfs_exc:
                # Clean, actionable message instead of a raw CDP traceback.
                # HAR / captured bodies still flush via the finally block.
                raise FlareCrawlError(
                    f"selector '{wait_for_selector}' not found after "
                    f"{_wfs_timeout / 1000:.0f}s",
                    code="TIMEOUT",
                ) from _wfs_exc

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

        from ..extract import (
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
            from ..sanitise import sanitise as _sanitise_fn
            san_result = _sanitise_fn(content, html=html)
            content = san_result.text

        if save_cookies:
            cookies = page.get_cookies()
            save_cookies.write_text(json.dumps(cookies, indent=2), encoding="utf-8")

        # Success path: write HAR and fetch captured bodies, then build metadata.
        _har_written = False
        if collector and har_output:
            har_data = collector.to_har()
            har_output.write_text(json.dumps(har_data, indent=2), encoding="utf-8")
            _har_written = True

        # Resolve any pending response bodies for capture (P2.1)
        captured_meta: list[dict] = []
        _bodies_fetched = False
        if body_capture is not None:
            page.fetch_captured_bodies(body_capture)
            captured_meta = body_capture.captured
            _bodies_fetched = True

        metadata: dict[str, Any] = {"source": "cdp"}
        if isinstance(content, str):
            metadata["contentLength"] = len(content)
            metadata["wordCount"] = len(content.split())
        metadata["sourceURL"] = url
        # T4: machine-readable bot-wall verdict so connectors stop
        # string-matching their own heuristics.
        from ..blockdetect import detect_block
        metadata["blocked"] = detect_block(200, {}, html).as_dict()
        if captured_meta:
            metadata["captured"] = captured_meta
        # v0.25.0 P3.3: surface auto-discovered structured data sources
        if data_probe is not None and data_probe.detected:
            metadata["data_sources"] = data_probe.detected

        # --tech-detect: now that the page is loaded, collect cookies +
        # JS globals (probe injection) and run Wappalyzer with the full
        # signal set (HTML + headers + cookies + js_globals + url).
        if tech_detect:
            _td_headers = (
                dict(tech_headers_collector.headers)
                if tech_headers_collector is not None else None
            )
            _td_cookies: dict[str, str] = {}
            try:
                jar = page.get_cookies(urls=[url])
                for c in jar or []:
                    name = c.get("name")
                    val = c.get("value")
                    if isinstance(name, str) and isinstance(val, str):
                        _td_cookies[name] = val
            except Exception:
                pass
            _td_js_globals: dict[str, str | None] = {}
            try:
                from ..wappalyzer import get_wappalyzer
                probe_js = get_wappalyzer().build_js_probe()
                page.evaluate(probe_js, await_promise=False)
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
                                _td_js_globals[k] = (
                                    v if (v is None or isinstance(v, str)) else str(v)
                                )
            except Exception:
                pass  # JS probe is best-effort - never fail the scrape

            _rec: dict = {"url": url}
            _attach_tech(
                _rec,
                html=html,
                headers=_td_headers,
                cookies=_td_cookies or None,
                js_globals=_td_js_globals or None,
            )
            _cdp_technologies = _rec.get("technologies")
        else:
            _cdp_technologies = None

        elapsed = _time.time() - start

        out = {"url": url, "content": content, "elapsed": round(elapsed, 2), "metadata": metadata}
        # Attach technologies at the top level (consistent with non-CDP path)
        # so the JSON output shape is the same regardless of backend.
        if _cdp_technologies:
            out["technologies"] = _cdp_technologies
        return out
    finally:
        # Safety flush: write HAR and captured bodies even when an exception
        # (e.g. wait_for_selector timeout) short-circuits the success path.
        # A partial HAR is most valuable precisely when a selector never appears.
        if not _har_written and collector and har_output:
            try:
                har_data = collector.to_har()
                har_output.write_text(json.dumps(har_data, indent=2), encoding="utf-8")
            except Exception:
                pass
        if not _bodies_fetched and body_capture is not None:
            try:
                page.fetch_captured_bodies(body_capture)
            except Exception:
                pass
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
                   negotiate_session: httpx.Client | None = None,
                   paywall: bool = False,
                   paywall_session: httpx.Client | None = None,
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
        from ..negotiate import try_negotiate
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
                from ..extract import filter_by_query
                content = filter_by_query(content, query)
            from ..extract import clean_content
            content = clean_content(content)
            _agent_safety_meta = None
            if agent_safe:
                from ..sanitise import sanitise_text as _sanitise_text
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
            from ..paywall import _try_stealth_fetch
            pw_result = _try_stealth_fetch(url, None, pw_headers)
        else:
            # No CF auth: run full cascade (all tiers use user's IP anyway)
            from ..paywall import try_bypass
            pw_result = try_bypass(
                url,
                session=paywall_session,
                extra_headers=pw_headers or None,
            )
        if pw_result is not None:
            content = pw_result.content
            if query:
                from ..extract import filter_by_query
                content = filter_by_query(content, query)
            from ..extract import clean_content
            content = clean_content(content)
            _agent_safety_meta_pw = None
            if agent_safe:
                from ..sanitise import sanitise_text as _sanitise_text_pw
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
        from ..paywall import _get_site_headers
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
        from ..extract import extract_images
        html = client.get_content(url, **kwargs)
        content = extract_images(html, url)
    elif format == "summary":
        if only_main_content or include_tags or exclude_tags:
            from ..extract import extract_main_content as _mc
            from ..extract import filter_tags as _ft
            from ..extract import html_to_markdown as _md
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
        from ..extract import extract_structured_data
        html = client.get_content(url, **kwargs)
        content = extract_structured_data(html)
    elif format == "accessibility":
        from ..extract import extract_accessibility_tree
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
        from ..extract import extract_main_content as _extract_main
        from ..extract import extract_main_content_precision as _prec
        from ..extract import extract_main_content_recall as _rec
        from ..extract import filter_tags as _filter
        from ..extract import html_to_markdown as _h2m
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
            from ..sanitise import sanitise_html as _sanitise_html
            _html_san = _sanitise_html(html)
            html = _html_san.content
            _agent_findings = list(_html_san.findings)

        content = _h2m(html) if format == "markdown" else html
    elif agent_safe and isinstance(content, str) and format == "html":
        # No extraction block ran, but we have HTML — sanitise it directly
        from ..sanitise import sanitise_html as _sanitise_html_raw
        _html_san_raw = _sanitise_html_raw(content)
        content = _html_san_raw.content
        _agent_findings = list(_html_san_raw.findings)

    # Post-processing: relevance filter
    if query and isinstance(content, str):
        from ..extract import filter_by_query
        content = filter_by_query(content, query)

    # Post-processing: clean ad/nav cruft
    if isinstance(content, str) and format == "markdown":
        from ..extract import clean_content
        content = clean_content(content)
    if clean and isinstance(content, str) and format in ("html",):
        from ..extract import clean_html
        content = clean_html(content)

    # Agent safety: text-level sanitisation (phase 2)
    _agent_safety_meta_br = None
    if agent_safe and isinstance(content, str):
        from ..sanitise import SanitiseResult as _SanitiseResult
        from ..sanitise import sanitise_text as _sanitise_text_br
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


@_cmd.command()
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
    js_expression: Annotated[str | None, typer.Option("--js-eval", help="Run JS expression, return result (implies --cdp for typed return values)")] = None,
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
    browser_cookies: Annotated[str | None, typer.Option("--browser-cookies", help="Grab cookies from local browser (chrome|firefox)")] = None,
    capture_pattern: Annotated[str | None, typer.Option("--capture-pattern", help="Comma-separated glob patterns to capture response bodies for (e.g. '*.csv,*.json'). Implies --cdp.")] = None,
    capture_dir: Annotated[Path | None, typer.Option("--capture-dir", help="Directory to save captured response bodies. Required with --capture-pattern.")] = None,
    capture_content_type: Annotated[str | None, typer.Option("--capture-content-type", help="Optional MIME type filter for captures (comma-separated, e.g. 'text/csv,application/json')")] = None,
    browser: Annotated[str, typer.Option("--browser", help="Browser backend: 'cf' (Cloudflare-hosted, default) or 'local' (Playwright Chromium). Local bypasses CF bot detection on hard targets.")] = "cf",
    headed: Annotated[bool, typer.Option("--headed", help="Run local browser visibly (debugging). Implies --browser local.")] = False,
    then_fetch: Annotated[str | None, typer.Option("--then-fetch", help="Comma-separated URLs to download after scraping (uses captured cookies + stealth TLS).")] = None,
    then_fetch_from: Annotated[Path | None, typer.Option("--then-fetch-from", help="File listing URLs (one per line) or CSV. With CSV, also pass --then-fetch-column.")] = None,
    then_fetch_column: Annotated[str | None, typer.Option("--then-fetch-column", help="CSV column to extract URLs from (e.g. 'PDF | Image Link').")] = None,
    then_fetch_output: Annotated[Path | None, typer.Option("--then-fetch-output", help="Output directory for --then-fetch downloads.")] = None,
    then_fetch_workers: Annotated[int, typer.Option("--then-fetch-workers", help="Parallel workers for --then-fetch.")] = 4,
    then_fetch_organize_by: Annotated[str | None, typer.Option("--then-fetch-organize-by", help="Subdirectory layout for downloads: 'flat' (default), 'extension' (group .pdf/.jpg/.mp4 by file extension), 'content-type' (group by major MIME type), or 'thumbnail' (special-case the war.gov '/thumbnail/' path).")] = None,
    auto_data: Annotated[bool, typer.Option("--auto-data/--no-auto-data", help="When CDP is in use, surface structured-data URLs (CSV/JSON/XLSX) the page fetched on init in meta.data_sources.")] = True,
    humanize: Annotated[bool | None, typer.Option("--humanize/--no-humanize", help="Synthesise mouse moves + scrolls + idle gaps before scraping. Defaults to ON for headless --browser local. Pass --no-humanize to disable. ~700ms cost.")] = None,
    humanize_profile: Annotated[str, typer.Option("--humanize-profile", help="Humanize intensity: 'fast' (~700ms), 'natural' (~1500ms), 'thorough' (~3000ms).")] = "fast",
    tech_detect: Annotated[bool, typer.Option("--tech-detect", help="Wappalyzer tech detection. CDP path: full signals (HTML + response headers + cookies + injected JS-globals probe). REST path: HTML + side-fetched headers/cookies. Stdin: HTML only.")] = False,
):
    """Scrape one or more URLs. Default output is markdown.

    Multiple URLs are scraped concurrently. Use --batch for file input
    with NDJSON output and configurable workers. Responses are cached
    for 1 hour by default (use --no-cache to bypass).

    --output + --json writes the JSON envelope to the file (the two are
    no longer mutually exclusive). On the CDP path, meta.blocked carries a
    machine-readable bot-wall verdict {blocked, vendor, kind, terminal}.
    HAR (--har) and captured bodies (--capture-dir) are flushed even when
    --wait-for-selector times out (a partial HAR is most useful then).

    Example:
        flarecrawl scrape https://example.com
        flarecrawl scrape https://example.com --format html --json
        flarecrawl scrape https://a.com https://b.com --json
        flarecrawl scrape --batch urls.txt --workers 5
        flarecrawl scrape --only-main-content --json
        flarecrawl scrape --exclude-tags "nav,footer" --json
        flarecrawl scrape https://example.com --json -o result.json
        flarecrawl scrape --format schema --json
    """
    # Flags that require CDP
    if any([keep_alive, record, live_view, interactive, save_cookies_file, load_cookies_file, tabs > 1]):
        cdp = True

    # --js-eval auto-promotes: REST scrape silently drops the return value,
    # CDP returns the typed result via Runtime.evaluate. Match behaviour of
    # other auto-promoting flags (--interactive, --live-view, etc.).
    if js_expression and not cdp:
        cdp = True
        if not json_output:
            console.print(
                "[dim]auto-promoting to --cdp for --js-eval (returns typed result)[/dim]"
            )

    # --browser local / --headed auto-promote to CDP (local Chromium is
    # always driven via CDP) and signals the local-browser context manager.
    use_local_browser = browser == "local" or headed
    if browser not in ("cf", "local"):
        _error(
            f"--browser must be 'cf' or 'local', got '{browser}'",
            "VALIDATION_ERROR", EXIT_VALIDATION,
            as_json=json_output,
        )
    if use_local_browser and not cdp:
        cdp = True
        if not json_output:
            console.print("[dim]auto-promoting to --cdp for --browser local[/dim]")

    # v0.26.0 P1: auto-humanize on headless local browser (the case where
    # behavioural fingerprinting hits hardest). User can override with
    # --no-humanize. Headed mode doesn't need it (real cursor history
    # comes from the OS).
    # `humanize is None` means user didn't pass either flag — apply default.
    # `humanize is False` means user explicitly said --no-humanize; respect.
    # `humanize is True` means user said --humanize; respect.
    if humanize is None:
        humanize = bool(use_local_browser and not headed)
    # After this point humanize is a concrete bool. (Pyright still sees
    # the Optional in the function signature; cast at call sites if needed.)

    # Validate --then-fetch-organize-by before any work begins
    if then_fetch_organize_by is not None and then_fetch_organize_by not in (
        "flat", "extension", "content-type", "thumbnail",
    ):
        _error(
            f"--then-fetch-organize-by must be one of "
            f"flat | extension | content-type | thumbnail, got '{then_fetch_organize_by}'",
            "VALIDATION_ERROR", EXIT_VALIDATION,
            as_json=json_output,
        )

    # Validate --humanize-profile
    if humanize_profile not in ("fast", "natural", "thorough"):
        _error(
            f"--humanize-profile must be one of fast | natural | thorough, "
            f"got '{humanize_profile}'",
            "VALIDATION_ERROR", EXIT_VALIDATION,
            as_json=json_output,
        )

    # --capture-pattern needs CDP for Network.getResponseBody (REST has no
    # body-fetch hook). Auto-promote and validate args.
    capture_patterns: list[str] | None = None
    capture_content_types: list[str] | None = None
    if capture_pattern:
        if not capture_dir:
            _error(
                "--capture-pattern requires --capture-dir",
                "VALIDATION_ERROR", EXIT_VALIDATION,
                as_json=json_output,
            )
        capture_patterns = [p.strip() for p in capture_pattern.split(",") if p.strip()]
        if capture_content_type:
            capture_content_types = [
                ct.strip() for ct in capture_content_type.split(",") if ct.strip()
            ]
        if not cdp:
            cdp = True
            if not json_output:
                console.print(
                    "[dim]auto-promoting to --cdp for --capture-pattern (body interception)[/dim]"
                )

    # Grab cookies from local browser
    if browser_cookies:
        _bc_path = _apply_browser_cookies(browser_cookies, urls[0] if urls else "", as_json=json_output)
        if _bc_path:
            load_cookies_file = _bc_path
            cdp = True

    # Stdin mode: process local HTML without API call
    if stdin_mode:
        from ..extract import (
            extract_images,
            extract_main_content,
            extract_structured_data,
            filter_tags,
            html_to_markdown,
        )
        html = sys.stdin.read()
        _stdin_findings: list = []
        if agent_safe:
            from ..sanitise import sanitise_html
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
            from ..sanitise import SanitiseResult, sanitise_text
            _text_san = sanitise_text(content)
            content = _text_san.content
            _all_findings = _stdin_findings + _text_san.findings
            _combined = SanitiseResult(content=content, findings=_all_findings)
        result = {"url": "(stdin)", "content": content}
        if tech_detect:
            # Stdin holds raw HTML by definition (the user piped it in);
            # use it directly regardless of the requested output format.
            _tmp = {"url": "(stdin)", "html": html}
            _apply_tech_detection([_tmp], emit_summary=not json_output)
            if _tmp.get("technologies"):
                result["technologies"] = _tmp["technologies"]
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
            from ..cookies import load_cookies
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
    from ..config import get_proxy
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
        from ..negotiate import get_negotiate_session
        _neg_session = get_negotiate_session() if not no_negotiate else None
        from ..paywall import get_paywall_session
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

        # v0.24.0 P2.2b: --browser local launches a Playwright Chromium and
        # exposes its CDP URL via FLARECRAWL_CDP_ENDPOINT, which the existing
        # CDPClient picks up. Lifetime is the scrape command itself.
        _local_browser_ctx = None
        if use_local_browser:
            from ..local_browser import LocalBrowser, LocalBrowserError
            try:
                _local_browser_ctx = LocalBrowser(headless=not headed).__enter__()
            except LocalBrowserError as exc:
                _error(str(exc), "MISSING_DEPENDENCY", EXIT_ERROR, as_json=json_output)

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
                console.print(f"[cyan]Live View:[/cyan] {dt_url}")
            if cdp_client.session_id:
                console.print(f"[dim]Session ID: {cdp_client.session_id}[/dim]")

        try:
            results = []

            # --interactive: human-in-the-loop auth flow
            if interactive:
                from ..config import save_session as _save_session
                url = all_urls[0]  # interactive uses single URL
                page = cdp_client.new_page()
                page.navigate(url, wait_until="load", timeout=timeout or 30000)
                console.print(
                    f"\n[bold yellow]Interactive mode:[/bold yellow] Browser is navigated to [cyan]{url}[/cyan]",
                )
                console.print(
                    "Complete authentication in the browser, then press [bold]Enter[/bold] to continue...",
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
                    capture_patterns=capture_patterns,
                    capture_dir=capture_dir,
                    capture_content_types=capture_content_types,
                    auto_data=auto_data,
                    humanize=humanize,
                    humanize_profile=humanize_profile,
                    tech_detect=tech_detect,
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
                        capture_patterns=capture_patterns,
                        capture_dir=capture_dir,
                        capture_content_types=capture_content_types,
                    auto_data=auto_data,
                    humanize=humanize,
                    humanize_profile=humanize_profile,
                    tech_detect=tech_detect,
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
                        capture_patterns=capture_patterns,
                        capture_dir=capture_dir,
                        capture_content_types=capture_content_types,
                    auto_data=auto_data,
                    humanize=humanize,
                    humanize_profile=humanize_profile,
                    tech_detect=tech_detect,
                    )
                    if timing:
                        console.print(f"[dim]{url} — {result['elapsed']:.1f}s[/dim]")
                    results.append(result)

            # v0.24.0 P2.3: --then-fetch — mass-download URLs reusing the
            # browser's session (cookies + stealth TLS via curl_cffi).
            if (then_fetch or then_fetch_from) and then_fetch_output:
                _then_fetch_results = _run_then_fetch(
                    cdp_client=cdp_client,
                    then_fetch=then_fetch,
                    then_fetch_from=then_fetch_from,
                    then_fetch_column=then_fetch_column,
                    then_fetch_output=then_fetch_output,
                    then_fetch_workers=then_fetch_workers,
                    json_output=json_output,
                    then_fetch_organize_by=then_fetch_organize_by,
                )
                # Surface the result counts in the scrape result meta
                if results and isinstance(results[-1], dict):
                    results[-1].setdefault("metadata", {})["then_fetch"] = _then_fetch_results

            # --record: save recording data
            if record:
                recording_data = cdp_client.get_recording()
                if recording_data:
                    from datetime import datetime
                    rec_path = record_output or Path(f"recording-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
                    rec_path.write_text(json.dumps(recording_data, indent=2, default=str), encoding="utf-8")
                    console.print(f"[green]Recording saved to:[/green] {rec_path}")

            if live_view:
                console.print("[dim]Session active — press Ctrl+C to close[/dim]")
                try:
                    while True:
                        _time.sleep(1)
                except KeyboardInterrupt:
                    pass

            if tech_detect:
                _apply_tech_detection(results, emit_summary=not json_output)

            if json_output:
                data = results if len(results) > 1 else results[0]
                if fields:
                    data = _filter_fields(data, fields)
                meta = {"format": format, "source": "cdp"}
                if len(results) > 1:
                    meta["count"] = len(results)
                elif "metadata" in results[0]:
                    meta.update(results[0]["metadata"])
                payload = {"data": data, "meta": meta}
                if output:
                    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
                    console.print(f"Saved to {output}")
                else:
                    _output_json(payload)
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
        except FlareCrawlError as e:
            _handle_api_error(_enrich_cdp_error(e), json_output)
        finally:
            cdp_client.close()
            if _local_browser_ctx is not None:
                _local_browser_ctx.__exit__(None, None, None)
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

        from .. import cache as _cache
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

    if tech_detect:
        # REST path: CF's /content endpoint returns rendered HTML but
        # doesn't surface the upstream page's HTTP response headers. We
        # side-fetch via httpx/curl_cffi (HEAD with GET fallback, ~10s
        # timeout, best-effort) so header- and cookie-only fingerprints
        # fire. Adds zero CF browser time. Uses --stealth + --proxy from
        # the parent invocation when set.
        for r in results:
            if not isinstance(r, dict):
                continue
            r_url = r.get("url", "")
            r_headers: dict[str, str] = {}
            r_cookies: dict[str, str] = {}
            if r_url:
                r_headers, r_cookies = _collect_response_signals(
                    r_url, proxy=effective_proxy, stealth=stealth,
                )
            _attach_tech(
                r,
                headers=r_headers or None,
                cookies=r_cookies or None,
                emit_summary=not json_output,
            )

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
        payload = {"data": data, "meta": meta}
        if output:
            output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            console.print(f"Saved to {output}")
        else:
            _output_json(payload)
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




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('scrape')(scrape)
