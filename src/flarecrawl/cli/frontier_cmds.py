"""frontier sub-app, spider/authcrawl, videos commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer

from ..client import FlareCrawlError
from ..config import (
    DEFAULT_CACHE_TTL,
)
from ._common import (
    EXIT_ERROR,
    _apply_browser_cookies,
    _enrich_cdp_error,
    _error,
    _get_cdp_client,
    _get_client,
    _handle_api_error,
    _output_json,
    _parse_auth,
    _validate_url,
    console,
)

frontier_app = typer.Typer(
    name="frontier",
    help="Inspect a local frontier v2 job database (see PERF-PLAN-PROGRESS).",
    no_args_is_help=True,
)


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

    from .._validate import validate_job_id
    from ..dead_letter import dump_dead_letter, format_rows

    try:
        validate_job_id(job_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    rows = _asyncio.run(dump_dead_letter(job_id))
    typer.echo(format_rows(rows, as_json=as_json))


# ============================================================
# spider / authcrawl — direct BFS via AuthenticatedCrawler (no CF round-trip)
# ============================================================




# Direct commands use _cmd for registration
_cmd = typer.Typer(add_completion=False)


@_cmd.command("spider")
@_cmd.command("authcrawl", hidden=True)
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
    """Direct HTTP spider — no browser rendering, no CF cost.

    Crawls via httpx (not CF Browser Run), carrying cookies for
    authenticated sites. Orders of magnitude faster and cheaper than
    browser-based crawling. Use for sites that don't need JS rendering.

    Features: BFS with depth control, per-host rate limiting, robots.txt
    (protego), adaptive delay, resume, per-URL retry budget, NDJSON output.

    When to use spider vs crawl:
        spider — static HTML sites, docs, APIs (fast, free, 50-100 concurrent)
        crawl  — SPAs, JS-rendered content (slower, costs browser time)

    Example:
        flarecrawl spider https://docs.example.com --limit 500
        flarecrawl spider https://docs.example.com --limit 1000 --workers 10 --format markdown
        flarecrawl spider https://private.example.com --cookies session.json --limit 200
        flarecrawl spider https://docs.example.com --resume JOB_ID
    """
    import json as _json
    import os as _os

    from .._validate import validate_job_id
    from ..authcrawl import AuthenticatedCrawler, CrawlConfig
    from ..telemetry import init_tracing

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


# ------------------------------------------------------------------
# videos — video URL discovery
# ------------------------------------------------------------------


@_cmd.command()
def videos(
    url: Annotated[str, typer.Argument(help="URL to discover videos on")],
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    js: Annotated[bool, typer.Option("--js", help="Wait for JS rendering")] = False,
    session: Annotated[Path | None, typer.Option("--session", help="Load cookies from session file")] = None,
    interactive: Annotated[bool, typer.Option("--interactive", help="Interactive login before discovery")] = False,
    export_cookies: Annotated[Path | None, typer.Option("--export-cookies", help="Export cookies in Netscape format (for yt-dlp)")] = None,
    cdp: Annotated[bool, typer.Option("--cdp", help="Use CDP WebSocket")] = False,
    keep_alive: Annotated[int, typer.Option("--keep-alive", help="Keep browser alive")] = 0,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL")] = None,
    depth: Annotated[int, typer.Option("--depth", help="Crawl N pages for videos")] = 1,
    save_cookies: Annotated[Path | None, typer.Option("--save-cookies", help="Save browser cookies after navigation")] = None,
    download: Annotated[bool, typer.Option("--download", help="Download videos via yt-dlp at highest resolution")] = False,
    download_dir: Annotated[Path | None, typer.Option("--download-dir", help="Directory for downloaded videos")] = None,
    browser_cookies: Annotated[str | None, typer.Option("--browser-cookies", help="Grab cookies from local browser (chrome|firefox)")] = None,
    yt_dlp: Annotated[bool, typer.Option("--yt-dlp", help="Run discovered URLs through yt-dlp's extractor registry to resolve provider-specific embeds (DVIDS, Vimeo with auth, etc.). Optional dep: uv tool install 'flarecrawl[videos]'.")] = False,
):
    """Discover video URLs on a web page.

    Finds direct video files (mp4, webm, m3u8), embedded players
    (YouTube, Vimeo), OpenGraph video tags, and JSON-LD VideoObjects.
    Works behind login with --session or --interactive.

    Pipe to yt-dlp:
        flarecrawl videos URL --json | jq -r '.data[].url' | yt-dlp --batch-file -

    With authenticated cookies for yt-dlp:
        flarecrawl videos URL --interactive --export-cookies cookies.txt --json
        yt-dlp --cookies cookies.txt VIDEO_URL

    Example:
        flarecrawl videos https://course-site.com --json
        flarecrawl videos https://private-site.com --session cookies.json --json
        flarecrawl videos https://spa-site.com --js --json
    """
    from ..videos import extract_videos

    _validate_url(url, json_output)

    # Grab cookies from local browser
    if browser_cookies:
        _bc_path = _apply_browser_cookies(browser_cookies, url, as_json=json_output)
        if _bc_path and not session:
            session = _bc_path

    # Interactive mode auto-promotes to CDP
    if interactive:
        cdp = True
        if not keep_alive:
            keep_alive = 300

    all_videos = []
    _browser_cookies = None

    if cdp:
        cdp_client = _get_cdp_client(
            as_json=json_output,
            keep_alive=keep_alive,
            proxy=proxy,
        )
        try:
            page = cdp_client.new_page()

            if interactive:
                dt_url = cdp_client.devtools_url
                if dt_url:
                    console.print(f"[cyan]Live View:[/cyan] {dt_url}")
                page.navigate(url, wait_until="load", timeout=30000)
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
                _browser_cookies = page.get_cookies()()
                from ..config import save_session as _save_session
                session_path = _save_session("interactive", _browser_cookies)
                console.print(
                    f"[green]Saved {len(browser_cookies)} cookies to:[/green] {session_path}",
                )
            else:
                wait_until = "networkidle0" if js else "load"
                page.navigate(url, wait_until=wait_until, timeout=30000)

            html = page.get_content()
            all_videos.extend(extract_videos(html, url, use_yt_dlp=yt_dlp))

            # Depth > 1: follow links on the page
            if depth > 1:
                from selectolax.parser import HTMLParser
                tree = HTMLParser(html)
                from urllib.parse import urljoin as _urljoin
                from urllib.parse import urlparse as _urlparse
                base_parsed = _urlparse(url)
                link_urls: list[str] = []
                for a in tree.css("a[href]"):
                    href = a.attributes.get("href")
                    if not href:
                        continue
                    full = _urljoin(url, href)
                    fp = _urlparse(full)
                    if fp.netloc == base_parsed.netloc and full not in link_urls:
                        link_urls.append(full)
                seen_urls = {url}
                for link_url in link_urls[:depth - 1]:
                    if link_url in seen_urls:
                        continue
                    seen_urls.add(link_url)
                    try:
                        page.navigate(link_url, wait_until="load", timeout=30000)
                        sub_html = page.get_content()
                        all_videos.extend(extract_videos(sub_html, link_url, use_yt_dlp=yt_dlp))
                    except Exception:
                        continue

            if save_cookies:
                cookies_data = page.get_cookies()
                save_cookies.write_text(json.dumps(cookies_data, indent=2), encoding="utf-8")
            if not _browser_cookies:
                _browser_cookies = page.get_cookies()()
        except FlareCrawlError as e:
            _handle_api_error(_enrich_cdp_error(e, url), json_output)
        finally:
            cdp_client.close()
    else:
        # REST mode
        cache_ttl = DEFAULT_CACHE_TTL
        client = _get_client(json_output, cache_ttl=cache_ttl, proxy=proxy)
        auth_dict = _parse_auth(auth, json_output)

        kwargs: dict[str, Any] = {}
        if auth_dict:
            kwargs.update(auth_dict)
        if js:
            kwargs["wait_until"] = "networkidle0"

        # Load session cookies
        if session:
            from ..cookies import cookies_to_header, load_cookies
            cookies = load_cookies(session)
            parsed = urlparse(url)
            cookie_header = cookies_to_header(cookies, parsed.netloc)
            if cookie_header:
                extra = kwargs.get("extra_headers", {})
                extra["Cookie"] = cookie_header
                kwargs["extra_headers"] = extra

        try:
            html = client.get_content(url, **kwargs)
        except FlareCrawlError as e:
            _handle_api_error(e, json_output)
            return

        all_videos.extend(extract_videos(html, url, use_yt_dlp=yt_dlp))

        # Depth > 1: follow links
        if depth > 1:
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            from urllib.parse import urljoin as _urljoin
            from urllib.parse import urlparse as _urlparse
            base_parsed = _urlparse(url)
            link_urls = []
            for a in tree.css("a[href]"):
                href = a.attributes.get("href")
                if not href:
                    continue
                full = _urljoin(url, href)
                fp = _urlparse(full)
                if fp.netloc == base_parsed.netloc and full not in link_urls:
                    link_urls.append(full)
            seen_urls = {url}
            for link_url in link_urls[:depth - 1]:
                if link_url in seen_urls:
                    continue
                seen_urls.add(link_url)
                try:
                    sub_html = client.get_content(link_url, **kwargs)
                    all_videos.extend(extract_videos(sub_html, link_url, use_yt_dlp=yt_dlp))
                except FlareCrawlError:
                    continue

    # Deduplicate across pages
    seen_final: set[str] = set()
    deduped: list = []
    for v in all_videos:
        if v.url not in seen_final:
            seen_final.add(v.url)
            deduped.append(v)
    all_videos = deduped

    # Export cookies in Netscape format for yt-dlp
    if export_cookies and _browser_cookies:
        from ..cookies import cookies_to_netscape
        cookies_to_netscape(_browser_cookies, export_cookies)
        console.print(
            f"[green]Exported {len(browser_cookies)} cookies to:[/green] {export_cookies}",
        )

    # Download via yt-dlp
    if download and all_videos:
        import shutil
        import subprocess
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            _error("yt-dlp not found. Install the videos extra: uv tool install 'flarecrawl[videos]'", "MISSING_DEPENDENCY", EXIT_ERROR, as_json=json_output)
            return
        dl_dir = download_dir or Path(".")
        dl_dir.mkdir(parents=True, exist_ok=True)
        cookie_args: list[str] = []
        if export_cookies and export_cookies.exists():
            cookie_args = ["--cookies", str(export_cookies)]
        for v in all_videos:
            console.print(f"[cyan]Downloading:[/cyan] {v.url}")
            cmd = [ytdlp, "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                   "--merge-output-format", "mp4",
                   "-o", str(dl_dir / "%(title)s.%(ext)s"),
                   *cookie_args, v.url]
            subprocess.run(cmd, check=False)

    # Output
    video_dicts = [v.to_dict() for v in all_videos]

    if json_output:
        result = {"data": video_dicts, "meta": {"url": url, "count": len(video_dicts)}}
        if output:
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            console.print(f"[green]Saved to:[/green] {output}")
        else:
            _output_json(result)
    else:
        if not all_videos:
            console.print("[dim]No videos found.[/dim]")
        else:
            console.print(f"\nVideos found: {len(all_videos)}\n")
            for v in all_videos:
                title_part = f'  "{v.title}"' if v.title else ""
                console.print(f"  {v.format:<8}{v.url}{title_part}")
            console.print(
                "\nExport for yt-dlp: flarecrawl videos URL --json | jq -r '.data[].url' | yt-dlp -a -"
            )
        if output:
            output.write_text(json.dumps(video_dicts, indent=2), encoding="utf-8")
            console.print(f"[green]Saved to:[/green] {output}")


def register(app: typer.Typer) -> None:
    """Register direct commands onto the main app."""
    app.command("spider")(authcrawl)
    app.command("authcrawl", hidden=True)(authcrawl)
    app.command("videos")(videos)
