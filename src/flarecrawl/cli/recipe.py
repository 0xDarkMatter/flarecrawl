"""recipe, p6, batch commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ..client import FlareCrawlError
from .scrape import _scrape_single
from ._common import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _error,
    _get_client,
    _handle_api_error,
    _output_json,
    _output_ndjson,
    _output_text,
    _validate_url,
    console,
)

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command("recipe")
def recipe_command(
    path: Annotated[Path, typer.Argument(help="Recipe YAML file")],
    resume: Annotated[bool, typer.Option("--resume", help="Skip steps already completed in journal")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate + print plan without running")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output result as JSON")] = False,
):
    """Run a multi-step browser flow from a YAML recipe.

    Recipe format (v1):

        version: 1
        goto: https://example.com
        browser: local
        headed: true
        steps:
          - wait_for: ".loaded"
          - capture:
              pattern: "*.csv,*.json"
              to: ./out/
          - click: "[data-action]"
          - wait: 500ms

    Resume support: each completed step is journaled to
    .recipe-state-<hash>.ndjson next to the recipe. Pass --resume to
    skip up to the last successful step.

    Step kinds: click, fill, press, wait, wait_for, eval, capture,
    screenshot, get_content, for_each, capture_download.

    Behaviour notes:
      - capture/capture_download steps are armed BEFORE navigation so the
        goto waterfall is intercepted regardless of step order (they show
        status "pre-armed" in the summary). They require browser: local —
        browser: cf fails fast (CF-hosted Chromium can't intercept bodies).
      - eval and get_content return values surface in steps[].result.
      - --json emits a frozen contract (schema_version: 1): canonical key
        "captured" (not "captures"), plus a "blocked" bot-wall verdict for
        the landing page.

    Example:
        flarecrawl recipe scrape-uap.yml
        flarecrawl recipe scrape-uap.yml --dry-run
        flarecrawl recipe scrape-uap.yml --resume
    """
    from ..recipe import RecipeError
    from ..recipe import run as run_recipe

    try:
        summary = run_recipe(path, resume=resume, dry_run=dry_run)
    except RecipeError as e:
        _error(f"{e}  (recipe format + step kinds: flarecrawl guide recipe)",
               "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)
        return
    except FlareCrawlError as e:
        # Notably the missing-`websockets` MISSING_DEPENDENCY guard — surface it
        # as a clean error (exit 1 / JSON envelope) instead of an uncaught
        # traceback. RecipeError is independent of FlareCrawlError, so order is moot.
        _handle_api_error(e, as_json=json_output)
        return

    if dry_run:
        if json_output:
            _output_json({"data": summary, "meta": {"dry_run": True}})
        else:
            console.print(f"[bold]Recipe plan:[/bold] {path}")
            for line in summary.get("plan", []):
                console.print(f"  {line}")
        return

    status = summary.get("status", "unknown")
    if json_output:
        _output_json({"data": summary, "meta": {"status": status}})
    else:
        if status == "ok":
            console.print(
                f"[green]Recipe done.[/green] "
                f"{len(summary.get('steps', []))} steps, "
                f"{summary.get('captured_count', 0)} captured."
            )
        else:
            console.print(f"[red]Recipe failed:[/red] {summary}")
            raise typer.Exit(EXIT_ERROR)


# ------------------------------------------------------------------
# p6 — mint -> replay anti-bot primitive (v0.29.0 F1)
# ------------------------------------------------------------------


@_cmd.command("p6")
def p6_command(
    mint_url: Annotated[str, typer.Argument(help="URL on the target domain to mint cookie shells from (headed/headless local Chromium)")],
    jar: Annotated[Path, typer.Option("--jar", help="Cookie jar path (minted shells are written/refreshed here)")],
    target: Annotated[list[str] | None, typer.Option("--target", help="Target URL to replay via curl_cffi (repeatable)")] = None,
    targets_from: Annotated[Path | None, typer.Option("--targets-from", help="File of target URLs (one per line)")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o", help="Write replay response bodies here")] = None,
    impersonate: Annotated[str, typer.Option("--impersonate", help="curl_cffi browser profile for replay")] = "chrome131",
    headed: Annotated[bool, typer.Option("--headed", help="Mint with a visible browser (debugging)")] = False,
    max_remints: Annotated[int, typer.Option("--max-remints", help="Global re-mint cap before cumulative resume")] = 3,
    base_cooldown: Annotated[float, typer.Option("--base-cooldown", help="Base seconds for the exponential egress cool-down")] = 5.0,
    max_cooldown: Annotated[float, typer.Option("--max-cooldown", help="Cool-down ceiling in seconds")] = 300.0,
    expiring_threshold: Annotated[int, typer.Option("--expiring-threshold", help="Shell seconds-to-expiry that triggers a proactive re-mint")] = 300,
    proxy: Annotated[str | None, typer.Option("--proxy", help="Proxy URL for both mint and replay")] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Skip targets already completed in the jar's journal")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output result as JSON")] = False,
):
    """Mint anti-bot cookie shells, then replay targets with Chrome TLS.

    The P6 dance that cracks Akamai / Cloudflare / Imperva / CloudFront:
    a local Chromium navigates MINT_URL so the wall deposits its cookie
    shells, then curl_cffi (--impersonate) replays the real targets
    carrying the jar plus a genuine Chrome JA3/JA4 handshake.

    Built-in: proactive re-mint when the jar goes stale, cumulative
    exponential cool-down (the Akamai egress-escalation trap), and a
    fast-fail on terminal Cloudflare 1020 (minting can't help — it's
    keyed on the egress, not the session).

    Example:
        flarecrawl p6 https://site.com/ --jar ./jar.json \\
          --target https://site.com/api/data --output-dir ./out
        flarecrawl p6 https://site.com/ --jar ./jar.json \\
          --targets-from urls.txt --json
    """
    from ..config import get_proxy
    from ..p6 import P6Config, run_p6

    targets: list[str] = list(target or [])
    if targets_from:
        if not targets_from.exists():
            _error(f"--targets-from file not found: {targets_from}", "NOT_FOUND", EXIT_NOT_FOUND, as_json=json_output)
            return
        for line in targets_from.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    if not targets:
        _error("No targets. Pass --target URL (repeatable) or --targets-from FILE.",
               "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output)
        return

    _validate_url(mint_url, json_output)
    for t in targets:
        _validate_url(t, json_output)

    cfg = P6Config(
        mint_url=mint_url,
        jar_path=jar,
        targets=targets,
        impersonate=impersonate,
        headed=headed,
        max_remints=max_remints,
        base_cooldown=base_cooldown,
        max_cooldown=max_cooldown,
        output_dir=output_dir,
        proxy=proxy or get_proxy(),
        expiring_threshold=float(expiring_threshold),
        resume=resume,
    )

    def _on_event(event: str, payload: dict) -> None:
        if json_output:
            return
        if event == "mint":
            console.print(f"[dim]mint #{payload.get('n')} — {payload.get('reason')}[/dim]")
        elif event == "mint_empty":
            console.print(f"[yellow]mint #{payload.get('n')} produced 0 cookies — "
                          f"check mint URL / network (wall deposited no shells)[/yellow]")
        elif event == "cooldown":
            console.print(f"[yellow]cool-down {payload.get('seconds')}s — {payload.get('reason')}[/yellow]")
        elif event == "terminal":
            console.print(f"[red]terminal block on {payload.get('url')} ({payload.get('reason')}) — aborting[/red]")
        elif event == "target":
            st = str(payload.get("status", ""))
            colour = {"ok": "green", "blocked": "red", "error": "red"}.get(st, "white")
            console.print(f"  [{colour}]{st}[/{colour}] {payload.get('url')}")

    try:
        result = run_p6(cfg, on_event=_on_event)
    except Exception as e:
        _error(f"P6 run failed: {e}  (mint→replay walkthrough: "
               f"flarecrawl guide hard-targets)",
               "ERROR", EXIT_ERROR, as_json=json_output)
        return

    if json_output:
        _output_json({"data": result.as_dict(), "meta": {"mint_url": mint_url}})
    else:
        console.print(
            f"[bold]P6 done.[/bold] {result.targets_ok} ok, "
            f"{result.targets_blocked} blocked, {result.targets_failed} failed, "
            f"{result.targets_skipped} skipped "
            f"({result.minted} mints, {result.remints} re-mints)"
        )
        if result.terminal_abort:
            console.print(f"[red]Aborted: terminal block ({result.aborted_reason})[/red]")

    # Non-zero exit when nothing succeeded or a terminal wall ended the run.
    if result.terminal_abort or (result.targets_ok == 0 and result.targets_total > 0):
        raise typer.Exit(EXIT_ERROR)


# ------------------------------------------------------------------
# batch — YAML config batch operations
# ------------------------------------------------------------------


@_cmd.command("batch")
def batch_config(
    config_file: Annotated[Path, typer.Argument(help="YAML config file")],
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers (max 50)")] = 3,
):
    """Run batch operations from a YAML config file.

    Config format (list of scrape jobs):
        - url: https://example.com
          format: markdown
          output: example.md
        - url: https://other.com
          format: images
          selector: main
          json: true

    Example:
        flarecrawl batch config.yml
        flarecrawl batch config.yml --workers 5
    """
    try:
        import yaml
    except ImportError:
        _error("PyYAML required for batch config. Install: pip install pyyaml",
               "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    try:
        jobs = yaml.safe_load(config_file.read_text())
    except (OSError, yaml.YAMLError) as e:
        _error(f"Cannot read config: {e}", "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    if not isinstance(jobs, list):
        _error("Config must be a YAML list of jobs", "VALIDATION_ERROR", EXIT_VALIDATION)
        return

    client = _get_client(True)

    console.print(f"[dim]Running {len(jobs)} jobs from {config_file}...[/dim]")

    for i, job in enumerate(jobs):
        if not isinstance(job, dict) or "url" not in job:
            console.print(f"[yellow]Job {i}: missing 'url', skipping[/yellow]")
            continue

        url = job["url"]
        fmt = job.get("format", "markdown")
        out_file = job.get("output")

        console.print(f"[dim]{i + 1}/{len(jobs)} {url} ({fmt})[/dim]")

        try:
            result = _scrape_single(
                client, url, fmt,
                wait_for=None, screenshot=False, full_page_screenshot=False,
                raw_body=None, timeout_ms=job.get("timeout"),
                wait_until=job.get("wait_until"),
                css_selector=job.get("selector"),
                only_main_content=job.get("only_main_content", False),
            )

            content = result.get("content", "")

            if out_file:
                Path(out_file).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    Path(out_file).write_text(content, encoding="utf-8")
                else:
                    Path(out_file).write_text(
                        json.dumps(content, indent=2, default=str), encoding="utf-8"
                    )
                console.print(f"  [green]Saved: {out_file}[/green]")
            elif job.get("json"):
                _output_ndjson({"index": i, "status": "ok", "data": result})
            else:
                if isinstance(content, str):
                    _output_text(content)
                else:
                    _output_json(content)

        except FlareCrawlError as e:
            console.print(f"  [red]Error: {e}[/red]")
            if job.get("json"):
                _output_ndjson({"index": i, "status": "error", "error": str(e)})

    console.print(f"[dim]Batch complete: {len(jobs)} jobs[/dim]")


# ------------------------------------------------------------------
# discover — feed/sitemap/link discovery
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('recipe')(recipe_command)
    app.command('p6')(p6_command)
    app.command('batch')(batch_config)
