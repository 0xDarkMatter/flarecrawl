"""Operator convenience: dump a frontier job's dead-letter rows.

Used by the ``flarecrawl frontier dead-letter <JOB_ID>`` CLI
subcommand. Kept tiny — all heavy lifting is in
:class:`flarecrawl.frontier_v2.DeadLetter`.

Example
-------
>>> # doctest: +SKIP
>>> rows = await dump_dead_letter("my-job")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .frontier_v2 import Frontier


async def dump_dead_letter(
    job_id: str, *, base_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Open ``job_id`` in resume mode and collect every dead row."""
    fr = await Frontier.open(job_id, resume=True, base_dir=base_dir)
    try:
        rows: list[dict[str, Any]] = []
        async for row in fr.dead_letter.list():
            # fp is bytes — stringify for JSON-friendliness.
            row = dict(row)
            row["fp"] = row["fp"].hex()
            rows.append(row)
        return rows
    finally:
        await fr.close()


def format_rows(rows: list[dict[str, Any]], *, as_json: bool = False) -> str:
    """Return a human-readable table or JSON blob."""
    if as_json:
        return json.dumps(rows, indent=2, sort_keys=True)
    if not rows:
        return "(no dead rows)"
    lines = [f"{'URL':<60}  ATTEMPTS  LAST_ERROR"]
    for r in rows:
        url = (r.get("url") or "")[:59]
        attempts = r.get("attempts", 0)
        err = (r.get("last_error") or "")[:60]
        lines.append(f"{url:<60}  {attempts:>8}  {err}")
    return "\n".join(lines)
