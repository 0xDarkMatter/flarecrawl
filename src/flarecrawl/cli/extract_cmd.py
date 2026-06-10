"""extract command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from ..batch import parse_batch_file, process_batch
from ..client import FlareCrawlError
from ..config import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_WORKERS,
)
from ._common import (
    EXIT_ERROR,
    EXIT_VALIDATION,
    _error,
    _get_client,
    _handle_api_error,
    _output_json,
    _output_ndjson,
    _parse_auth,
    _parse_body,
    _parse_headers,
    _validate_url,
    console,
)

# Module-local Typer — commands are mounted by register() in __init__.py
_cmd = typer.Typer(add_completion=False)


@_cmd.command()
def extract(
    prompt: Annotated[str, typer.Argument(help="Natural language prompt for extraction")],
    urls: Annotated[str | None, typer.Option("--urls", help="Comma-separated URLs")] = None,
    schema: Annotated[str | None, typer.Option("--schema", help="JSON schema (inline string)")] = None,
    schema_file: Annotated[Path | None, typer.Option("--schema-file", help="Path to JSON schema file")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    batch: Annotated[Path | None, typer.Option("--batch", "-b", help="Batch input file with URLs")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers for batch (max 50, free tier: 3)")] = 3,
    body: Annotated[str | None, typer.Option("--body", help="Raw JSON body")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass response cache")] = False,
    auth: Annotated[str | None, typer.Option("--auth", help="HTTP Basic Auth (user:password)")] = None,
    headers: Annotated[list[str] | None, typer.Option("--headers", help="Custom HTTP headers")] = None,
    user_agent: Annotated[str | None, typer.Option("--user-agent", help="Custom User-Agent string")] = None,
    agent_safe: Annotated[bool, typer.Option("--agent-safe", help="Sanitise against AI agent traps")] = False,
):
    """AI-powered structured data extraction from web pages.

    Uses Cloudflare Workers AI to extract structured data based on a prompt.
    Use --batch for parallel extraction with NDJSON output.

    Example:
        flarecrawl extract "Extract all product names and prices" --urls https://shop.example.com --json
        flarecrawl extract "Get article title and date" --urls https://blog.example.com --schema-file schema.json
        flarecrawl extract "Get page title" --batch urls.txt --workers 5
        flarecrawl extract "Get credentials" --urls https://intranet.example.com --auth user:pass --json
    """
    is_batch_mode = batch is not None
    cache_ttl = 0 if no_cache else DEFAULT_CACHE_TTL
    client = _get_client(json_output or is_batch_mode, cache_ttl=cache_ttl)
    raw_body = _parse_body(body, json_output or is_batch_mode)
    auth_dict = _parse_auth(auth, json_output or is_batch_mode)
    custom_headers = _parse_headers(headers, json_output or is_batch_mode)
    if custom_headers:
        if auth_dict is None:
            auth_dict = {}
        existing = auth_dict.get("extra_headers", {})
        auth_dict["extra_headers"] = {**custom_headers, **existing}

    # Parse URLs from --urls flag
    url_list = []
    if urls:
        url_list = [u.strip() for u in urls.split(",")]

    # Load URLs from --batch file
    if batch:
        try:
            batch_urls = parse_batch_file(batch)
            url_list.extend(str(u) for u in batch_urls)
        except OSError as e:
            _error(f"Cannot read batch file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION, as_json=True)

    if not url_list and not raw_body:
        _error(
            "Provide at least one URL with --urls or --batch",
            "VALIDATION_ERROR", EXIT_VALIDATION, as_json=json_output or is_batch_mode,
        )

    # Parse schema
    response_format = None
    if schema_file:
        try:
            response_format = json.loads(schema_file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            _error(f"Invalid schema file: {e}", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output or is_batch_mode)
    elif schema:
        try:
            response_format = json.loads(schema)
        except json.JSONDecodeError as e:
            _error(f"Invalid --schema JSON: {e}", "VALIDATION_ERROR", EXIT_VALIDATION,
                   as_json=json_output or is_batch_mode)

    target_urls = url_list if not raw_body else [raw_body.get("url", "")]

    for url in target_urls:
        _validate_url(url, json_output or is_batch_mode)

    # ------------------------------------------------------------------
    # Batch mode: asyncio + NDJSON output
    # ------------------------------------------------------------------
    if is_batch_mode:
        capped_workers = min(workers, DEFAULT_MAX_WORKERS)

        extra_kwargs = {}
        if auth_dict:
            extra_kwargs.update(auth_dict)
        if user_agent:
            extra_kwargs["user_agent"] = user_agent

        async def _extract_one(url: str) -> dict:
            return await asyncio.to_thread(
                client.extract_json, url, prompt, response_format, **extra_kwargs,
            )

        def _on_progress(completed: int, total: int, errors: int):
            console.print(f"[dim]{completed}/{total} (errors: {errors})[/dim]")

        console.print(f"[dim]Extracting from {len(target_urls)} URLs with {capped_workers} workers...[/dim]")
        results = asyncio.run(
            process_batch(target_urls, _extract_one, workers=capped_workers, on_progress=_on_progress)
        )

        has_errors = any(r["status"] == "error" for r in results)
        if agent_safe:
            from ..sanitise import sanitise_text
            for r in results:
                if r.get("status") == "ok" and "data" in r:
                    d = r["data"]
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if isinstance(v, str):
                                d[k] = sanitise_text(v).content
                    elif isinstance(d, str):
                        r["data"] = sanitise_text(d).content
        for r in sorted(results, key=lambda x: x["index"]):
            _output_ndjson(r)

        error_count = sum(1 for r in results if r["status"] == "error")
        console.print(f"[dim]Done: {len(results) - error_count} ok, {error_count} errors[/dim]")
        if has_errors:
            raise typer.Exit(EXIT_ERROR)
        return

    # ------------------------------------------------------------------
    # Non-batch: existing sequential behavior
    # ------------------------------------------------------------------
    results = []
    for url in target_urls:
        try:
            if raw_body:
                raw_body.setdefault("url", url)
                result = client.post_raw("json", raw_body)
                extracted = result.get("result", result)
            else:
                extra = auth_dict if auth_dict else {}
                extracted = client.extract_json(url, prompt, response_format, **extra)
            if agent_safe:
                from ..sanitise import sanitise_text
                if isinstance(extracted, dict):
                    for k, v in extracted.items():
                        if isinstance(v, str):
                            extracted[k] = sanitise_text(v).content
                elif isinstance(extracted, str):
                    extracted = sanitise_text(extracted).content
            results.append({"url": url, "data": extracted})
        except FlareCrawlError as e:
            if len(target_urls) == 1:
                _handle_api_error(e, json_output)
                return
            results.append({"url": url, "error": str(e)})

    if output:
        output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        console.print(f"Saved to {output}")
    elif json_output:
        if len(results) == 1:
            _output_json({"data": results[0], "meta": {}})
        else:
            _output_json({"data": results, "meta": {"count": len(results)}})
    else:
        _output_json(results)


# ------------------------------------------------------------------
# screenshot — convenience command
# ------------------------------------------------------------------




def register(app: typer.Typer) -> None:
    """Register this module's commands onto the main app."""
    app.command('extract')(extract)
