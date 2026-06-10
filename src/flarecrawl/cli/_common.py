"""Shared helpers used by 2+ command modules.

All output/envelope formatting, error handling, exit codes, common option
processing, and the CDP auto-promote flag logic live here.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import typer
from rich.console import Console

from .. import __version__
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

if TYPE_CHECKING:
    pass

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
# Output helpers
# ------------------------------------------------------------------


def _output_json(data) -> None:
    """Output JSON to stdout — Windows-safe."""
    text = json.dumps(data, indent=2, default=str)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode(enc, errors="replace"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()


def _output_ndjson(record: dict) -> None:
    """Output single JSON record (newline-delimited)."""
    print(json.dumps(record, default=str))


def _output_text(text: str) -> None:
    """Output raw text to stdout — Windows-safe."""
    if not text:
        return
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.write(text)
        sys.stdout.write("\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode(enc, errors="replace"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()


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


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


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
            "Not authenticated. Run: flarecrawl auth login  "
            "(setup walkthrough: flarecrawl guide auth)",
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
        "CDP_AUTH_ERROR": EXIT_AUTH_REQUIRED,
        "CDP_TIER_ERROR": EXIT_FORBIDDEN,
    }
    exit_code = code_map.get(e.code, EXIT_ERROR)
    _error(str(e), e.code, exit_code, as_json=as_json)


def _enrich_cdp_error(e: FlareCrawlError, url: str | None = None) -> FlareCrawlError:
    """Enrich CDP error messages with actionable suggestions."""
    msg = str(e).lower()
    suggestions: list[str] = []

    if "execution context" in msg or "navigation" in msg or "detached" in msg:
        suggestions.append("Site may have bot detection. Try: --stealth or --paywall")
    if "timeout" in msg or "timed out" in msg:
        suggestions.append("Page took too long. Try: --timeout 60000 or check site reachability")
    if "redirect" in msg:
        suggestions.append("Too many redirects. Try: --browser-cookies chrome to reuse your session")
    if "network error" in msg or "net::" in msg or "connection" in msg:
        suggestions.append("Network issue. Try: --proxy or check if site blocks CF IPs")
    if "websocket" in msg or "ws " in msg:
        suggestions.append("CDP WebSocket failure. Run: flarecrawl cdp sessions, then flarecrawl cdp close")
    if "cookies" in msg or "auth" in msg or "401" in msg or "403" in msg:
        suggestions.append("Auth failed. Try: --interactive to log in, or --browser-cookies chrome")

    if not suggestions:
        return e

    enriched = f"{e}\n\nSuggestions:\n" + "\n".join(f"  - {s}" for s in suggestions)
    return FlareCrawlError(enriched, code=e.code)


# ------------------------------------------------------------------
# Input parsing helpers
# ------------------------------------------------------------------


def _validate_url(url: str, as_json: bool = False) -> None:
    """Validate URL format."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        hint = ""
        if not parsed.scheme and "/" not in url and "." not in url:
            hint = f" — '{url}' has no scheme; did a shell glob or path expand into the URL position?"
        _error(
            f"Invalid URL: {url} (must include scheme, e.g. https://){hint}",
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
    """Parse --auth user:pass into auth kwargs for CF Browser Run API."""
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
    """Parse --headers values into a dict for setExtraHTTPHeaders."""
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
    if parsed.query:
        path = f"{path}--{parsed.query}"
    name = re.sub(r'[^\w\-.]', '-', path)
    name = re.sub(r'-+', '-', name).strip('-')
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
        from ..extract import extract_main_content, filter_tags, html_to_markdown
        if key == "html" or "<" in content[:100]:
            html = content
            if only_main_content:
                html = extract_main_content(html)
            if include_tags:
                html = filter_tags(html, include=include_tags)
            if exclude_tags:
                html = filter_tags(html, exclude=exclude_tags)
            if agent_safe:
                from ..sanitise import sanitise_html
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
                from ..sanitise import sanitise_text, SanitiseResult
                _text_san = sanitise_text(md_content)
                record[key] = _text_san.content
                _record_findings.extend(_text_san.findings)
        if agent_safe and _record_findings:
            _combined = SanitiseResult(content="", findings=_record_findings)
            meta = record.get("metadata") or {}
            meta["agentSafety"] = _combined.to_metadata()
            record["metadata"] = meta
    return record


def _apply_browser_cookies(
    browser_cookies: str | None,
    url: str,
    as_json: bool = False,
) -> Path | None:
    """Grab cookies from a local browser, write to temp file, return path."""
    if not browser_cookies:
        return None
    try:
        from ..browser_cookies import grab_cookies
    except ImportError:
        _error(
            "Browser cookie extraction requires rookiepy. Install with: uv pip install rookiepy",
            "MISSING_DEPENDENCY", EXIT_ERROR, as_json=as_json,
        )
        return None  # unreachable — _error raises
    cookies = grab_cookies(browser_cookies, url)
    import tempfile
    tmp = Path(tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w").name)
    tmp.write_text(json.dumps(cookies), encoding="utf-8")
    if not as_json:
        console.print(f"[dim]Grabbed {len(cookies)} cookies from {browser_cookies}[/dim]")
    return tmp


# ------------------------------------------------------------------
# Client factories
# ------------------------------------------------------------------


def _get_client(as_json: bool = False, cache_ttl: int = 3600, proxy: str | None = None) -> Client:
    """Get authenticated client."""
    _require_auth(as_json)
    return Client(cache_ttl=cache_ttl, proxy=proxy)


def _get_cdp_client(
    as_json: bool = False,
    keep_alive: int = 0,
    recording: bool = False,
    proxy: str | None = None,
):
    """Create and connect a CDP WebSocket client."""
    try:
        from ..cdp import CDPClient
    except ImportError:
        _error(
            "CDP requires the 'websockets' package. Install with: uv pip install websockets",
            "MISSING_DEPENDENCY", EXIT_ERROR, as_json=as_json,
        )

    from ..config import get_proxy
    account_id = get_account_id()
    api_token = get_api_token()
    if not account_id or not api_token:
        _error("Not authenticated. Run: flarecrawl auth login", "AUTH_REQUIRED", EXIT_AUTH_REQUIRED, as_json=as_json)

    effective_proxy = proxy or get_proxy()
    client = CDPClient(account_id=account_id, api_token=api_token)
    client.connect(keep_alive=keep_alive, recording=recording)
    return client


# ------------------------------------------------------------------
# Tech detection helpers
# ------------------------------------------------------------------


def _filter_detections(
    detections: list,
    *,
    min_confidence: int = 0,
    only_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> list:
    """Apply user filters to a list of Detection objects."""
    if min_confidence > 0:
        detections = [d for d in detections if d.confidence >= min_confidence]
    if only_categories:
        wanted = {c.lower() for c in only_categories}
        detections = [
            d for d in detections
            if any(c.lower() in wanted for c in d.categories)
        ]
    if exclude_categories:
        unwanted = {c.lower() for c in exclude_categories}
        detections = [
            d for d in detections
            if not any(c.lower() in unwanted for c in d.categories)
        ]
    return detections


def _parse_category_list(raw: str | None) -> list[str] | None:
    """Parse a comma-separated category list flag into a clean list, or None."""
    if not raw:
        return None
    items = [s.strip() for s in raw.split(",")]
    items = [s for s in items if s]
    return items or None


def _attach_tech(
    record: dict,
    *,
    html: str | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    js_globals: "dict[str, str | None] | None" = None,
    emit_summary: bool = False,
    min_confidence: int = 0,
    only_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> None:
    """Run Wappalyzer detection on a single record (mutates in place)."""
    from ..wappalyzer import get_wappalyzer
    if not isinstance(record, dict):
        return
    if "technologies" in record:
        return  # idempotent - someone already attached results
    if html is None:
        html = record.get("html") or ""
        if not html:
            content = record.get("content")
            if isinstance(content, str) and content.lstrip().startswith("<"):
                html = content
    if not (html or headers or cookies or js_globals):
        return
    url = record.get("url", "")
    wappa = get_wappalyzer()
    detections = wappa.analyze(
        html=html or "",
        headers=headers,
        cookies=cookies,
        js_globals=js_globals,
        url=url,
    )
    detections = _filter_detections(
        detections,
        min_confidence=min_confidence,
        only_categories=only_categories,
        exclude_categories=exclude_categories,
    )
    record["technologies"] = [d.to_dict() for d in detections]
    if emit_summary and detections:
        summary_parts = []
        for d in detections[:6]:
            name = d.name + (f" {d.version}" if d.version else "")
            cat = d.categories[0] if d.categories else ""
            summary_parts.append(f"{name} ({cat})" if cat else name)
        extra = f" +{len(detections) - 6} more" if len(detections) > 6 else ""
        sys.stderr.write(f"[tech] {' + '.join(summary_parts)}{extra}\n")


def _apply_tech_detection(records: list[dict], emit_summary: bool = False) -> None:
    """Bulk helper - run _attach_tech over each record (HTML-only signals)."""
    for r in records:
        _attach_tech(r, emit_summary=emit_summary)


def _collect_response_signals(
    url: str,
    *,
    http_session=None,
    proxy: str | None = None,
    stealth: bool = False,
    impersonate: str = "chrome131",
    timeout: float = 10.0,
) -> tuple[dict[str, str], dict[str, str]]:
    """Side-fetch response headers + cookies for a URL."""
    import httpx as _httpx  # noqa: PLC0415

    SNIFF_BYTES = 32 * 1024
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}

    NETWORK_ERRORS: tuple[type[BaseException], ...] = (
        _httpx.HTTPError,
        ConnectionError,
        OSError,
        TimeoutError,
    )
    try:
        if stealth:
            try:
                from curl_cffi import requests as _cffi  # noqa: PLC0415
                from curl_cffi.requests.exceptions import RequestException as _CFFIError  # noqa: PLC0415
            except ImportError:
                return headers, cookies
            try:
                with _cffi.Session(impersonate=impersonate, timeout=timeout) as cs:  # type: ignore[arg-type]
                    if proxy:
                        cs.proxies = {"http": proxy, "https": proxy}
                    r = cs.get(url, allow_redirects=True, stream=True)
                    try:
                        headers = {str(k): str(v) for k, v in r.headers.items()}
                        cookies = {
                            c.name: c.value for c in cs.cookies.jar
                            if c.value is not None
                        }
                    finally:
                        try:
                            r.close()
                        except Exception:  # noqa: BLE001
                            pass
            except (_CFFIError, ConnectionError, OSError, TimeoutError):
                return headers, cookies
        else:
            session = http_session
            close_after = False
            if session is None:
                session = _httpx.Client(
                    follow_redirects=True,
                    timeout=timeout,
                    proxy=proxy,
                )
                close_after = True
            try:
                with session.stream("GET", url) as r:
                    headers = {str(k): str(v) for k, v in r.headers.items()}
                    drained = 0
                    for chunk in r.iter_raw():
                        drained += len(chunk)
                        if drained >= SNIFF_BYTES:
                            break
                    cookies = {
                        c.name: c.value for c in session.cookies.jar
                        if c.value is not None
                    }
            finally:
                if close_after:
                    session.close()
    except NETWORK_ERRORS:
        return headers, cookies
    return headers, cookies


# ------------------------------------------------------------------
# then-fetch helpers
# ------------------------------------------------------------------


def _classify_url_for_organize(url: str, mode: str) -> str:
    """Pick a subdirectory name for a URL given an organize-by mode."""
    from urllib.parse import urlparse as _urlparse

    if mode in (None, "flat"):
        return ""
    name = Path(_urlparse(url.split("?")[0]).path).name.lower()
    ext = Path(name).suffix or ""

    is_thumb = "/thumbnail/" in url.lower() or "thumbnail" in name

    if mode == "thumbnail":
        if is_thumb:
            return "thumbnails"
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
    cdp_client,
    then_fetch: str | None,
    then_fetch_from: Path | None,
    then_fetch_column: str | None,
    then_fetch_output: Path,
    then_fetch_workers: int,
    json_output: bool,
    then_fetch_organize_by: str | None = None,
) -> dict:
    """v0.24.0 P2.3: mass-download URLs reusing browser session + stealth TLS."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..fetch import download_binary_stealth

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
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

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

    then_fetch_output.mkdir(parents=True, exist_ok=True)
    if not json_output:
        console.print(
            f"[dim]then-fetch: {len(urls)} URLs, {then_fetch_workers} workers, "
            f"output={then_fetch_output}[/dim]"
        )

    def _do_one(url: str) -> dict:
        from urllib.parse import urlparse as _urlparse2
        name = Path(_urlparse2(url.split("?")[0]).path).name or "download"
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
