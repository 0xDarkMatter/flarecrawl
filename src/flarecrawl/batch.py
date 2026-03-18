"""Batch processing with parallel workers.

Supports:
  - Plain text input (one item per line, # comments skipped)
  - NDJSON input (one JSON object per line)
  - JSON array input
  - Parallel workers with bounded concurrency
  - NDJSON output with index correlation
  - Fail-fast on fatal errors (auth, forbidden)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

# Error codes that should stop the entire batch (non-retryable)
FATAL_ERROR_CODES = {"AUTH_REQUIRED", "FORBIDDEN"}


def parse_batch_file(path: Path) -> list:
    """Auto-detect and parse batch input file.

    Formats:
      - JSON array: starts with [
      - NDJSON: starts with {
      - Plain text: one item per line (blank lines and # comments skipped)
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    if text.startswith("["):
        return json.loads(text)

    if text.startswith("{"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


async def process_batch(
    items: list,
    process_fn: Callable[[Any], Coroutine[Any, Any, dict]],
    workers: int = 3,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """Process items in parallel with bounded concurrency.

    Fails fast on fatal errors (AUTH_REQUIRED, FORBIDDEN) — cancels
    remaining tasks since they would all fail the same way.

    Args:
        items: List of items to process.
        process_fn: Async function that processes one item and returns a dict.
        workers: Max concurrent workers.
        on_progress: Callback(completed, total, errors) for progress reporting.

    Returns:
        List of result dicts with index, status, data/error fields.
    """
    semaphore = asyncio.Semaphore(workers)
    results: list[dict] = []
    error_count = 0
    fatal_error: dict | None = None

    async def _worker(index: int, item: Any):
        nonlocal error_count, fatal_error
        # Skip if a fatal error already occurred
        if fatal_error is not None:
            result = {
                "index": index,
                "status": "error",
                "error": fatal_error,
            }
            results.append(result)
            return result

        async with semaphore:
            try:
                data = await process_fn(item)
                result = {"index": index, "status": "ok", "data": data}
            except Exception as e:
                error_count += 1
                code = getattr(e, "code", "ERROR")
                error_info = {"code": code, "message": str(e)}
                result = {
                    "index": index,
                    "status": "error",
                    "error": error_info,
                }
                # Check for fatal errors that should stop the batch
                if code in FATAL_ERROR_CODES:
                    fatal_error = error_info
            results.append(result)
            if on_progress:
                on_progress(len(results), len(items), error_count)
            return result

    tasks = [_worker(i, item) for i, item in enumerate(items)]
    await asyncio.gather(*tasks)
    return results
