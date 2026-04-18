"""Forma journal integration.

Emit structured NDJSON lifecycle events to the shared Forma journal.

Strategy
--------

1. If the ``forma`` CLI is on ``$PATH``, shell out to
   ``forma log emit`` with the event payload on stdin. This lets the
   Forma daemon tag, shard, and forward the event.
2. If ``forma`` is not available, append the record directly to
   ``${FORMA_HOME:-~/.forma}/logs/<date>.jsonl``.
3. If both paths fail (no CLI, unwritable dir), the call is a silent
   no-op — crawlers never crash because the journal is broken.

Every record has:

* ``ts``       — ISO-8601 UTC
* ``source``   — always ``"flarecrawl"``
* ``action``   — supplied by caller, e.g. ``"started"``
* ``domain``   — logical grouping (default ``"crawl"``)
* ``level``    — ``"info" | "warn" | "error"`` (default ``"info"``)
* ``msg``      — optional free-text
* ``target``   — optional crawl target (hostname / seed URL)
* ``duration_ms`` — optional elapsed ms
* ``counts``   — optional ``dict[str, int]`` of crawl stats
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import pathlib
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return (
        _dt.datetime.now(tz=_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _forma_home() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("FORMA_HOME") or (pathlib.Path.home() / ".forma")
    )


def _logs_path() -> pathlib.Path:
    day = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    return _forma_home() / "logs" / f"{day}.jsonl"


def _forma_on_path() -> str | None:
    """Return the resolved ``forma`` CLI path, or None."""
    return shutil.which("forma")


def _build_record(
    action: str,
    *,
    domain: str,
    target: str | None,
    level: str,
    duration_ms: int | None,
    counts: dict[str, Any] | None,
    msg: str,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": _now_iso(),
        "source": "flarecrawl",
        "action": action,
        "domain": domain,
        "level": level,
        "msg": msg,
    }
    if target is not None:
        record["target"] = target
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    if counts is not None:
        record["counts"] = dict(counts)
    return record


def _emit_via_cli(binary: str, record: dict[str, Any]) -> bool:
    """Pipe ``record`` to ``forma log emit`` on stdin.

    Returns True on success, False on any failure (caller falls back).
    """
    try:
        payload = json.dumps(record, default=str)
        subprocess.run(  # noqa: S603 — trusted binary from PATH
            [binary, "log", "emit"],
            input=payload,
            text=True,
            check=True,
            timeout=2.0,
            capture_output=True,
        )
        return True
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("forma log emit failed: %r", exc)
        return False


def _emit_via_file(record: dict[str, Any]) -> bool:
    """Append ``record`` as one NDJSON line under FORMA_HOME/logs."""
    try:
        path = _logs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("journal fallback write failed: %r", exc)
        return False


def emit_event(
    action: str,
    *,
    domain: str = "crawl",
    target: str | None = None,
    level: str = "info",
    duration_ms: int | None = None,
    counts: dict[str, Any] | None = None,
    msg: str = "",
) -> None:
    """Emit a lifecycle event to the Forma journal.

    Every failure mode is swallowed — the caller is a crawler on a hot
    path. We never raise, never block longer than the 2s CLI timeout,
    and never corrupt the journal on concurrent writes (NDJSON append).
    """
    record = _build_record(
        action,
        domain=domain,
        target=target,
        level=level,
        duration_ms=duration_ms,
        counts=counts,
        msg=msg,
    )

    binary = _forma_on_path()
    if binary is not None:
        if _emit_via_cli(binary, record):
            return
        # Fall through to file append if CLI call fails.

    # If neither Forma CLI nor a writable logs dir exists, this is a
    # silent no-op.
    if not _forma_home().exists():
        # Try to create the dir lazily; if that fails, bail.
        try:
            _forma_home().mkdir(parents=True, exist_ok=True)
        except Exception:  # pragma: no cover
            return

    _emit_via_file(record)
