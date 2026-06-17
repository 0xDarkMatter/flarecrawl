"""In-process CLI execution layer for the flarecrawl MCP surface.

Handlers call ``run_cli()`` with a list of CLI arguments.  The function
executes the flarecrawl Typer app in-process via ``typer.testing.CliRunner``,
captures stdout, parses the JSON envelope, applies post-processing
(truncation, agent_safe injection, blocked verdict escalation), and returns a
Python dict ready for JSON serialisation.

Design decisions:
- ``mcp`` package is NOT imported here. This module must be importable without
  the optional ``mcp`` extra.
- CliRunner with ``mix_stderr=False`` captures stdout cleanly.
- Exit codes are mapped to §30.9 error envelopes via ``_errors.exit_code_error``.
- ``meta.blocked`` is passed through verbatim; ``next_steps`` derived from it.
- Binary outputs (screenshot/pdf) write to disk; return ``{"path": ..., "bytes": N}``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from ._errors import blocked_error, exit_code_error

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MAX_CHARS = 40_000

# ---------------------------------------------------------------------------
# CLI runner (lazy import so tests can monkeypatch before first call)
# ---------------------------------------------------------------------------


def _get_runner():  # type: ignore[return]
    """Return a CliRunner instance (lazy import).

    click < 8.2 needs mix_stderr=False to keep JSON stdout clean of the
    Rich stderr chatter; click >= 8.2 removed the kwarg and separates
    stderr by default.
    """
    from typer.testing import CliRunner  # type: ignore[import-untyped]

    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _get_app():  # type: ignore[return]
    """Return the flarecrawl Typer app (lazy import)."""
    from flarecrawl.cli import app  # type: ignore[import-untyped]

    return app


# ---------------------------------------------------------------------------
# Options dict → CLI flags conversion
# ---------------------------------------------------------------------------


def _options_to_flags(options: dict[str, Any], explicit_keys: set[str]) -> list[str]:
    """Convert an ``options`` dict to CLI flags.

    Rules:
    - key with underscores → ``--key-with-hyphens``
    - bool True → bare flag (``--flag``)
    - bool False → skip
    - list → repeated flag (``--flag val --flag val2``)
    - other → ``--flag value``
    - Raises ValueError if a key collides with an explicit argument.
    """
    flags: list[str] = []
    for key, value in options.items():
        normalised = key.replace("_", "-")
        if key in explicit_keys or normalised in explicit_keys:
            raise ValueError(
                f"options key '{key}' collides with an explicit argument. "
                "Pass it as an explicit parameter instead."
            )
        flag = f"--{normalised}"
        if isinstance(value, bool):
            if value:
                flags.append(flag)
        elif isinstance(value, list):
            for item in value:
                flags.extend([flag, str(item)])
        elif value is not None:
            flags.extend([flag, str(value)])
    return flags


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def _truncate_at_line(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate ``text`` at the last line boundary before ``max_chars``."""
    if len(text) <= max_chars:
        return text, False
    cut = text.rfind("\n", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return text[:cut], True


def _apply_truncation(
    envelope: dict[str, Any],
    max_chars: int | None,
) -> dict[str, Any]:
    """Apply max_chars truncation to content fields in the envelope."""
    if max_chars is None:
        return envelope

    meta = envelope.setdefault("meta", {})
    data = envelope.get("data")
    if data is None:
        return envelope

    if isinstance(data, dict):
        # Single result — truncate 'content' or 'markdown' field
        for field in ("content", "markdown", "html", "text"):
            if field in data and isinstance(data[field], str):
                truncated, did_truncate = _truncate_at_line(data[field], max_chars)
                if did_truncate:
                    meta["truncated"] = True
                    meta["chars_total"] = len(data[field])
                    data = dict(data)
                    data[field] = truncated
                    envelope = dict(envelope)
                    envelope["data"] = data
                break
    elif isinstance(data, list):
        total_chars = 0
        new_data = []
        truncated_any = False
        for item in data:
            if isinstance(item, dict):
                for field in ("content", "markdown", "html", "text"):
                    if field in item and isinstance(item[field], str):
                        remaining = max_chars - total_chars
                        if remaining <= 0:
                            truncated_any = True
                            break
                        trunc, did_trunc = _truncate_at_line(item[field], remaining)
                        if did_trunc:
                            truncated_any = True
                            item = dict(item)
                            item[field] = trunc
                        total_chars += len(item.get(field, ""))
                        break
            new_data.append(item)
        if truncated_any:
            meta["truncated"] = True
            envelope = dict(envelope)
            envelope["data"] = new_data

    return envelope


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------


def run_cli(
    args: list[str],
    *,
    tool_name: str = "",
    max_chars: int | None = _DEFAULT_MAX_CHARS,
    inject_agent_safe: bool = False,
    binary_output_path: str | None = None,
) -> dict[str, Any]:
    """Execute flarecrawl CLI in-process, return parsed envelope dict.

    Parameters
    ----------
    args:
        CLI arguments list (without ``flarecrawl``), e.g. ``["scrape", URL, "--json"]``.
    tool_name:
        Name of the calling MCP tool (for error messages).
    max_chars:
        Truncation limit for content fields. None = no truncation.
    inject_agent_safe:
        If True, inject ``--agent-safe`` unless already present.
    binary_output_path:
        For binary-output commands (screenshot/pdf), the output path.
        Returns ``{"path": ..., "bytes": N}`` instead of JSON.
    """
    # Inject --agent-safe for T1/T2 tools
    if inject_agent_safe and "--agent-safe" not in args:
        args = list(args) + ["--agent-safe"]

    runner = _get_runner()
    app = _get_app()

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            result = runner.invoke(app, args, catch_exceptions=False)
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
        raw = stdout_buf.getvalue() or stderr_buf.getvalue()
        return exit_code_error(exit_code, raw, tool_name)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": str(exc),
                "category": "upstream_error",
                "tool": tool_name,
                "next_steps": [
                    {
                        "try": "diagnostics",
                        "with": {},
                        "why": "Check for configuration issues.",
                    }
                ],
            },
        }

    exit_code = result.exit_code

    # Handle binary output specially
    if binary_output_path is not None:
        p = Path(binary_output_path)
        if p.exists():
            return {"ok": True, "data": {"path": str(p), "bytes": p.stat().st_size}}
        # Fall through to error handling

    # Try to parse JSON from stdout
    raw = result.output or stdout_buf.getvalue()

    if exit_code != 0:
        return exit_code_error(exit_code, raw, tool_name)

    # Parse JSON envelope
    envelope: dict[str, Any]
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        # Some commands (guide, plain text output) don't emit JSON
        envelope = {"ok": True, "data": {"text": raw}, "meta": {}}

    # Ensure ok field
    if "ok" not in envelope:
        envelope["ok"] = True

    # meta.blocked passthrough with next_steps derivation
    meta = envelope.get("meta", {})
    blocked = meta.get("blocked")
    if blocked and isinstance(blocked, dict) and blocked.get("detected"):
        return blocked_error(blocked, tool_name)

    # Apply max_chars truncation
    envelope = _apply_truncation(envelope, max_chars)

    return envelope


# ---------------------------------------------------------------------------
# Binary output helper
# ---------------------------------------------------------------------------


def resolve_binary_output(
    output_path: str | None,
    prefix: str = "fc-mcp",
    suffix: str = ".bin",
) -> str:
    """Return an output path, creating the directory if needed."""
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    base = Path(tempfile.gettempdir()) / "flarecrawl-mcp"
    base.mkdir(parents=True, exist_ok=True)
    # Generate a unique filename
    import uuid

    return str(base / f"{prefix}-{uuid.uuid4().hex[:8]}{suffix}")


# ---------------------------------------------------------------------------
# Optional dependency matrix
# ---------------------------------------------------------------------------


def _check_optional_deps() -> dict[str, bool]:
    """Return availability of optional extras."""
    return {
        "stealth (curl_cffi)": importlib.util.find_spec("curl_cffi") is not None,
        "cdp (websockets)": importlib.util.find_spec("websockets") is not None,
        "local-browser (playwright)": importlib.util.find_spec("playwright") is not None,
        "search (JINA_API_KEY)": bool(os.environ.get("JINA_API_KEY")),
        "recipes (pyyaml)": importlib.util.find_spec("yaml") is not None,
        "videos (yt-dlp)": importlib.util.find_spec("yt_dlp") is not None,
    }


def missing_dep_install_hint(dep_name: str) -> str:
    """Return the uv install command for a missing dep."""
    _hints = {
        "stealth (curl_cffi)": "uv tool install 'flarecrawl[stealth]'  # in a project: uv add 'flarecrawl[stealth]'",
        "cdp (websockets)": "uv tool install 'flarecrawl[cdp]'  # in a project: uv add 'flarecrawl[cdp]'",
        "local-browser (playwright)": "uv tool install 'flarecrawl[local-browser]' && playwright install chromium",
        "search (JINA_API_KEY)": "export JINA_API_KEY=<your-key>  # free at jina.ai",
        "recipes (pyyaml)": "uv tool install 'flarecrawl[recipes]'  # in a project: uv add 'flarecrawl[recipes]'",
        "videos (yt-dlp)": "uv tool install 'flarecrawl[videos]'  # in a project: uv add 'flarecrawl[videos]'",
    }
    return _hints.get(dep_name, f"uv add {dep_name}")
