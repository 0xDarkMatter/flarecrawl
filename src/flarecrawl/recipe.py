"""YAML interaction recipes for flarecrawl — v0.25.0 P3.1.

A recipe is a declarative spec for a multi-step browser flow:

    version: 1
    goto: https://example.com
    browser: local
    headed: true
    steps:
      - wait_for: networkidle
      - capture:
          pattern: "*.csv,*.json"
          to: ./out/
      - click: "[data-record-trigger]"
      - wait_for: ".record-modal-shell"
      - capture_download:
          to: ./pdfs/
      - press: Escape
      - wait: 500ms

The runner translates each step into the corresponding CDP / fetch / capture
calls already implemented in flarecrawl. Resume support is journal-based:
each completed step is appended to ``./recipe-state-<hash>.ndjson``;
``--resume`` skips up to the last journaled step.

Step types implemented in v0.25.0:
  - goto             navigate (top-level field; rarely used in steps)
  - click            CSS selector click
  - fill             type into input
  - press            keyboard key
  - wait             explicit sleep (e.g. "500ms", "2s")
  - wait_for         CSS selector or "load"/"networkidle"
  - eval             run JS (returns typed value)
  - capture          enable response-body capture (pattern + to)
  - screenshot       page screenshot to file
  - get_content      grab page HTML/markdown into a result var

Out of scope for v0.25.0 (deferred):
  - for_each         template iteration over a selector list
  - capture_download triggered file download (use --then-fetch instead)
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


class RecipeError(Exception):
    """Raised when a recipe is malformed or a step fails."""


# ---------------------------------------------------------------------------
# Result schema contract (F2) — frozen.  Bump RECIPE_SCHEMA_VERSION on any
# breaking change to the shapes below; connectors key off schema_version.
#
# run() returns a dict with this stable shape:
#   schema_version : int   — this contract's version (currently 1)
#   recipe         : str   — recipe file path
#   goto           : str   — top-level navigation URL
#   browser        : str   — "cf" | "local"
#   started_at     : float — epoch seconds
#   completed_at   : float — epoch seconds (present on non-error completion)
#   status         : str   — "ok" | "error"
#   steps          : list  — per-step records, each:
#       { step:int, kind:str, status:"ok"|"error"|"skipped"|"pre-armed",
#         elapsed_ms:int,
#         result?:any   — present for steps that produce output
#                          (eval return value, get_content text);
#                          JSON-serialisable or stringified,
#         error?:str    — present when status == "error",
#         reason?:str, note?:str }
#   captured_count : int   — total response bodies captured
#   captured       : list  — captured body descriptors (canonical key is
#                            "captured", NOT "captures")
#   blocked        : dict  — blockdetect verdict for the landing page:
#                            { blocked:bool, vendor:str, kind:str,
#                              terminal:bool, signal:str }
#   plan           : list  — present only for --dry-run
#   dry_run        : bool  — present only for --dry-run
# ---------------------------------------------------------------------------
RECIPE_SCHEMA_VERSION = 1


REQUIRED_TOP_LEVEL_KEYS = {"goto"}
ALLOWED_TOP_LEVEL_KEYS = {
    "version", "goto", "browser", "headed", "steps", "stealth",
    "viewport", "user_agent", "timeout",
}
ALLOWED_STEP_KEYS = {
    "click", "fill", "press", "wait", "wait_for", "eval",
    "capture", "screenshot", "get_content",
    # v0.25.1 follow-ups
    "for_each", "capture_download",
}


def _parse_duration(s: str | int | float) -> float:
    """Convert '500ms' / '2s' / '1m' / 500 / 2.0 to seconds."""
    if isinstance(s, (int, float)):
        return float(s)
    s = s.strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|s|m)?$", s)
    if not m:
        raise RecipeError(f"Invalid duration: {s!r}")
    value, unit = float(m.group(1)), (m.group(2) or "s")
    if unit == "ms":
        return value / 1000
    if unit == "m":
        return value * 60
    return value


def load_recipe(path: Path) -> dict:
    """Load and validate a recipe YAML file."""
    try:
        import yaml
    except ImportError as exc:
        raise RecipeError("YAML support requires PyYAML. Install the recipes extra: uv tool install 'flarecrawl[recipes]'") from exc

    if not path.exists():
        raise RecipeError(f"Recipe not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RecipeError(f"Recipe parse error in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RecipeError(f"Recipe root must be a mapping, got {type(data).__name__}")

    validate_recipe(data)
    return data


def validate_recipe(data: dict) -> None:
    """Raise RecipeError if the recipe shape is wrong."""
    extra = set(data.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if extra:
        raise RecipeError(
            f"Unknown top-level keys: {sorted(extra)}. "
            f"Allowed: {sorted(ALLOWED_TOP_LEVEL_KEYS)}"
        )
    missing = REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise RecipeError(f"Missing required keys: {sorted(missing)}")
    if "version" in data and data["version"] not in (1, "1"):
        raise RecipeError(f"Unsupported recipe version: {data['version']!r} (only 1 supported)")
    goto = data.get("goto")
    if not isinstance(goto, str) or not goto.startswith(("http://", "https://")):
        raise RecipeError(f"goto must be an http(s) URL, got {goto!r}")
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise RecipeError("steps must be a list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise RecipeError(f"step {i}: must be a mapping, got {type(step).__name__}")
        if len(step) != 1:
            raise RecipeError(f"step {i}: each step is one key/value, got {sorted(step.keys())}")
        key = next(iter(step.keys()))
        if key not in ALLOWED_STEP_KEYS:
            raise RecipeError(f"step {i}: unknown step type {key!r}. Allowed: {sorted(ALLOWED_STEP_KEYS)}")


def recipe_id(path: Path) -> str:
    """Stable id for journal filename."""
    return hashlib.sha256(path.resolve().as_posix().encode()).hexdigest()[:16]


def _journal_path(recipe_path: Path) -> Path:
    return recipe_path.parent / f".recipe-state-{recipe_id(recipe_path)}.ndjson"


def load_journal(recipe_path: Path) -> set[int]:
    """Return set of completed step indices from a previous run."""
    journal = _journal_path(recipe_path)
    if not journal.exists():
        return set()
    completed: set[int] = set()
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            if entry.get("status") == "ok" and isinstance(entry.get("step"), int):
                completed.add(entry["step"])
        except json.JSONDecodeError:
            continue
    return completed


def append_journal(recipe_path: Path, entry: dict) -> None:
    journal = _journal_path(recipe_path)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def clear_journal(recipe_path: Path) -> None:
    journal = _journal_path(recipe_path)
    if journal.exists():
        journal.unlink()


def run(
    recipe_path: Path,
    *,
    resume: bool = False,
    dry_run: bool = False,
    output_handler: Any = None,
) -> dict:
    """Execute a recipe.

    Returns a summary dict with per-step results.
    """
    data = load_recipe(recipe_path)

    completed_idx: set[int] = load_journal(recipe_path) if resume else set()
    if not resume and not dry_run:
        clear_journal(recipe_path)

    summary: dict = {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "recipe": str(recipe_path),
        "goto": data["goto"],
        "browser": data.get("browser", "cf"),
        "steps": [],
        "started_at": time.time(),
    }

    if dry_run:
        # Just validate + print plan
        plan: list[str] = []
        steps = data.get("steps") or []
        plan.append(f"goto: {data['goto']}")
        if data.get("browser") == "local":
            plan.append(f"  browser=local headed={data.get('headed', False)}")
        for i, step in enumerate(steps):
            kind, val = next(iter(step.items()))
            plan.append(f"  step {i}: {kind} = {val!r}")
        summary["plan"] = plan
        summary["dry_run"] = True
        return summary

    # Late import — runtime CDP session lives only inside this branch.
    from .cdp import BodyCapture, CDPClient, _require_websockets
    from .config import get_account_id, get_api_token
    from .local_browser import LocalBrowser

    # Guard the optional `websockets` dependency before launching a browser or
    # touching the network, so a missing install yields an actionable
    # MISSING_DEPENDENCY error (caught cleanly by recipe_command) instead of a
    # raw traceback from deep inside CDPClient. Every non-dry-run recipe needs
    # CDP (both browser: local and CF-hosted connect over a WebSocket).
    _require_websockets()

    use_local = data.get("browser") == "local"
    headed = bool(data.get("headed"))
    viewport = data.get("viewport") or [1440, 900]
    timeout_ms = int(data.get("timeout", 30000))

    # Capture steps rely on CDP Network.getResponseBody, which CF-hosted
    # browser does not expose.  Fail fast rather than silently capturing 0.
    if not use_local:
        has_capture = any(
            next(iter(step)) in ("capture", "capture_download")
            for step in (data.get("steps") or [])
            if isinstance(step, dict)
        )
        if has_capture:
            raise RecipeError(
                "capture / capture_download steps require browser: local — "
                "CF-hosted browser does not support CDP response body interception. "
                "Add 'browser: local' to your recipe."
            )

    local_ctx = None
    if use_local:
        local_ctx = LocalBrowser(
            headless=not headed,
            viewport=tuple(viewport),  # type: ignore[arg-type]
        ).__enter__()

    cdp_client = CDPClient(
        account_id=get_account_id() or "local",
        api_token=get_api_token() or "local",
    )

    try:
        page = cdp_client.new_page()
        body_captures: list[BodyCapture] = []
        results: dict[str, Any] = {}

        try:
            page.apply_stealth()
        except Exception:
            pass

        # Pre-arm capture steps before navigation so resources loaded during
        # goto are intercepted.  A `capture` step placed after a wait/wait_for
        # in the recipe would otherwise miss the initial navigation waterfall.
        steps = data.get("steps") or []
        _pre_armed: set[int] = set()
        for i, step in enumerate(steps):
            kind, val = next(iter(step.items()))
            if kind == "capture":
                _run_step(page, kind, val, body_captures, results, timeout_ms)
                _pre_armed.add(i)
                summary["steps"].append({
                    "step": i, "kind": kind, "status": "pre-armed",
                    "note": "armed before navigation",
                })

        # Navigation
        page.navigate(data["goto"], timeout=timeout_ms)

        # T4: machine-readable bot-wall verdict for the landing page.
        try:
            from .blockdetect import detect_block
            summary["blocked"] = detect_block(
                200, {}, page.get_content()).as_dict()
        except Exception:
            summary["blocked"] = {"blocked": False}

        for i, step in enumerate(steps):
            if i in _pre_armed:
                continue  # already armed before navigation; don't re-run
            if i in completed_idx:
                summary["steps"].append({"step": i, "status": "skipped", "reason": "resume"})
                continue
            kind, val = next(iter(step.items()))
            t0 = time.time()
            try:
                step_result = _run_step(page, kind, val, body_captures, results, timeout_ms)
                entry = {
                    "step": i, "kind": kind, "status": "ok",
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
                if step_result is not None:
                    try:
                        json.dumps(step_result)  # only include if JSON-serialisable
                        entry["result"] = step_result
                    except (TypeError, ValueError):
                        entry["result"] = str(step_result)
            except Exception as exc:
                entry = {
                    "step": i, "kind": kind, "status": "error",
                    "error": str(exc)[:300],
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
                append_journal(recipe_path, entry)
                summary["steps"].append(entry)
                if output_handler:
                    output_handler(entry)
                # Stop on first error — recipes are linear
                summary["status"] = "error"
                return summary
            append_journal(recipe_path, entry)
            summary["steps"].append(entry)
            if output_handler:
                output_handler(entry)

        # Resolve any pending body captures
        for bc in body_captures:
            page.fetch_captured_bodies(bc)
        captured_total = sum(len(bc.captured) for bc in body_captures)
        summary["captured_count"] = captured_total
        summary["captured"] = [c for bc in body_captures for c in bc.captured]
        summary["status"] = "ok"

    finally:
        try:
            cdp_client.close()
        except Exception:
            pass
        if local_ctx is not None:
            try:
                local_ctx.__exit__(None, None, None)
            except Exception:
                pass

    summary["completed_at"] = time.time()
    return summary


def _run_step(
    page: Any,
    kind: str,
    val: Any,
    body_captures: list,
    results: dict,
    default_timeout_ms: int,
) -> Any:
    """Dispatch to the right CDP page action for the step kind.

    Returns the step's output value when applicable (eval result, screenshot
    path, etc.) so callers can surface it in the summary JSON.  Returns None
    for steps that produce no typed output.
    """
    from .cdp import BodyCapture

    if kind == "click":
        if not isinstance(val, str):
            raise RecipeError(f"click expects a CSS selector string, got {type(val).__name__}")
        page.click(val, timeout=default_timeout_ms)

    elif kind == "fill":
        if not isinstance(val, dict) or "selector" not in val or "text" not in val:
            raise RecipeError("fill expects {selector: ..., text: ...}")
        page.type(val["selector"], val["text"])

    elif kind == "press":
        if not isinstance(val, str):
            raise RecipeError("press expects a key name string")
        # Best-effort via Input.dispatchKeyEvent
        page.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": val})
        page.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": val})

    elif kind == "wait":
        time.sleep(_parse_duration(val))

    elif kind == "wait_for":
        if not isinstance(val, str):
            raise RecipeError("wait_for expects a CSS selector or 'load'/'networkidle' string")
        if val in ("load", "networkidle", "networkidle0"):
            # Already waited at navigate time; treat as no-op to keep recipes terse
            return
        page.wait_for_selector(val, timeout=default_timeout_ms)

    elif kind == "eval":
        if not isinstance(val, str):
            raise RecipeError("eval expects a JS expression string")
        eval_result = page.evaluate(val)
        results[f"step_eval_{len(results)}"] = eval_result
        return eval_result

    elif kind == "capture":
        if not isinstance(val, dict) or "pattern" not in val or "to" not in val:
            raise RecipeError("capture expects {pattern: ..., to: ...}")
        patterns = (
            [p.strip() for p in val["pattern"].split(",")]
            if isinstance(val["pattern"], str)
            else list(val["pattern"])
        )
        bc = BodyCapture(
            patterns=patterns,
            output_dir=Path(val["to"]),
            content_types=val.get("content_types"),
        )
        page.enable_network(body_capture=bc)
        body_captures.append(bc)

    elif kind == "screenshot":
        if not isinstance(val, (str, dict)):
            raise RecipeError("screenshot expects a path string or {to: path}")
        target = val if isinstance(val, str) else val.get("to")
        full = isinstance(val, dict) and val.get("full_page", False)
        if not target:
            raise RecipeError("screenshot needs a 'to' path")
        data = page.screenshot(full_page=full)
        Path(target).write_bytes(data)

    elif kind == "get_content":
        var = val if isinstance(val, str) else (val.get("var") if isinstance(val, dict) else "content")
        _content = page.get_content()
        results[var] = _content
        # F2: surface the captured content so it appears in steps[].result
        # (previously stored only in the internal results dict — callers saw
        # length 0 where page text was expected).
        return _content

    elif kind == "for_each":
        if not isinstance(val, dict):
            raise RecipeError("for_each expects {selector: ..., steps: [...]}")
        selector = val.get("selector")
        sub_steps = val.get("steps") or val.get("do") or []
        max_iter = int(val.get("max", 1000))
        if not isinstance(selector, str):
            raise RecipeError("for_each.selector must be a CSS selector string")
        if not isinstance(sub_steps, list):
            raise RecipeError("for_each.steps must be a list of steps")
        # Count matching elements via JS — CSS :nth-of-type isn't reliable for
        # Web Components / mixed-tag matches, so we iterate by click-and-mark.
        count = int(page.evaluate(
            f"document.querySelectorAll({json.dumps(selector)}).length"
        ) or 0)
        n = min(count, max_iter)
        for i in range(n):
            # Substitute @current → position-aware selector.
            # Strategy: tag the nth match with a unique data attribute via JS,
            # then steps reference @current to act on that tagged element.
            tag = f"__flarecrawl_for_each_{i}"
            page.evaluate(
                f"((sel, idx, tag) => {{ const els = document.querySelectorAll(sel); "
                f"if (els[idx]) els[idx].setAttribute('data-flarecrawl-iter', tag); }})"
                f"({json.dumps(selector)}, {i}, {json.dumps(tag)})"
            )
            current_sel = f'[data-flarecrawl-iter="{tag}"]'
            for sub_step in sub_steps:
                if not isinstance(sub_step, dict) or len(sub_step) != 1:
                    raise RecipeError(f"for_each: malformed sub-step at iteration {i}: {sub_step!r}")
                sub_kind, sub_val = next(iter(sub_step.items()))
                # Substitute @current placeholder in string values
                if isinstance(sub_val, str):
                    sub_val = sub_val.replace("@current", current_sel)
                elif isinstance(sub_val, dict):
                    sub_val = {
                        k: (v.replace("@current", current_sel) if isinstance(v, str) else v)
                        for k, v in sub_val.items()
                    }
                if sub_kind not in ALLOWED_STEP_KEYS:
                    raise RecipeError(
                        f"for_each: unknown step kind {sub_kind!r} at iteration {i}"
                    )
                _run_step(page, sub_kind, sub_val, body_captures, results, default_timeout_ms)

    elif kind == "capture_download":
        # v0.25.1: passive download capture. Sets Page.setDownloadBehavior to
        # 'allow' with the configured save dir, then any subsequent click that
        # triggers a download lands on disk. Caller is responsible for the
        # click — capture_download just enables the path.
        if not isinstance(val, dict) or "to" not in val:
            raise RecipeError("capture_download expects {to: <dir>}")
        dest_dir = Path(val["to"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Use the new behaviour API (Browser.setDownloadBehavior was deprecated).
        page.send("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(dest_dir.resolve()),
        })

    else:
        raise RecipeError(f"Unimplemented step kind: {kind}")
