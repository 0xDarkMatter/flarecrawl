"""Orientation tool handlers for the flarecrawl MCP surface.

Five tools: capabilities, guide, diagnostics, permissions_check, schema_generate.

All handlers are pure functions returning dicts.  No ``mcp`` package import.
The ``capabilities()`` response is assembled from the live registry so it can
never drift from the actual tool list.
"""

from __future__ import annotations

import os
from typing import Any

from ._exec import _check_optional_deps, missing_dep_install_hint, run_cli

# ---------------------------------------------------------------------------
# Coverage gaps (§30.11.3 — declared, honest, verbatim from manifest)
# ---------------------------------------------------------------------------

COVERAGE_GAPS: list[dict[str, str]] = [
    {
        "command": "videos",
        "reason": "Niche; yt-dlp pipeline is CLI-shaped (pipes)",
        "workaround": "flarecrawl videos URL --json  # via CLI",
    },
    {
        "command": "authcrawl",
        "reason": "Long-running (hours), resume-oriented — wrong shape for MCP call lifetime",
        "workaround": "flarecrawl authcrawl URL --resume  # via CLI",
    },
    {
        "command": "frontier",
        "reason": "Debug tool for authcrawl job DBs",
        "workaround": "flarecrawl frontier dead-letter JOB_ID --json  # via CLI",
    },
    {
        "command": "batch",
        "reason": "YAML-config driver; agents issue parallel tool calls instead",
        "workaround": "Multiple MCP calls or: flarecrawl batch config.yml  # via CLI",
    },
    {
        "command": "auth login/logout",
        "reason": "Credential entry is human-in-the-loop; MCP must never handle secrets",
        "workaround": "flarecrawl auth login  # run once; MCP uses the auth chain",
    },
    {
        "command": "cache clear / negotiate clear",
        "reason": "Destructive config management, low agent value",
        "workaround": "flarecrawl cache clear  # or: flarecrawl negotiate clear",
    },
    {
        "command": "session save/delete/show/validate",
        "reason": "Cookie-jar management writes/removes secrets on disk — CLI-shaped, low agent value",
        "workaround": (
            "flarecrawl session save/show/delete/validate via CLI; "
            "MCP exposes session_list + session_inspect (read-only)"
        ),
    },
    {
        "command": "rules *",
        "reason": "User config management",
        "workaround": "flarecrawl rules list --json  # or rules show/add via CLI",
    },
    {
        "command": "cdp *",
        "reason": "Session lifecycle tied to terminal workflows",
        "workaround": "flarecrawl cdp connect --json  # via CLI",
    },
    {
        "command": "webmcp *",
        "reason": "Calling third-party WebMCP tools through our MCP = confusing double-hop",
        "workaround": "flarecrawl webmcp discover URL --json  # via CLI",
    },
    {
        "command": "--interactive/--live-view/--headed flags",
        "reason": "Require a human at a browser",
        "workaround": (
            "Run CLI interactively, save session, then use session_list + raw tools: "
            "flarecrawl scrape URL --interactive --json"
        ),
    },
]

# Canonical tool catalogue (used by capabilities and schema_generate)
# Populated lazily from the registry to avoid circular import
_CAPABILITIES_CACHE: dict[str, Any] | None = None


def _build_capabilities(registry: dict[str, Any], read_only: bool = False) -> dict[str, Any]:
    """Assemble the §30.2.1 capabilities response from the live registry."""
    from flarecrawl import __version__

    # Group tools by tier
    orientation: list[dict[str, Any]] = []
    t1: list[dict[str, Any]] = []
    t2: list[dict[str, Any]] = []
    t3: list[dict[str, Any]] = []

    for name, defn in registry.items():
        tier = defn.get("tier", "t2")
        entry = {
            "name": name,
            "description": defn.get("short_description", ""),
            "personas": defn.get("personas", []),
        }
        if tier == "orientation":
            orientation.append(entry)
        elif tier == "t1":
            t1.append({
                "name": name,
                "personas": defn.get("personas", []),
                "description": defn.get("short_description", ""),
            })
        elif tier == "t2":
            t2.append({
                "name": name,
                "personas": defn.get("personas", []),
                "description": defn.get("short_description", ""),
            })
        elif tier == "t3":
            t3.append({"name": name, "description": defn.get("short_description", "")})

    # Auth / permissions check (best-effort, no live call)
    cf_authed = bool(os.environ.get("FLARECRAWL_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN"))
    extras = _check_optional_deps()

    return {
        "tool": "flarecrawl",
        "version": __version__,
        "protocol": "forma/0.9",
        "mode": "read_only" if read_only else "full",
        "mcp_profile": "curated+raw",
        "permissions": {
            "cf_auth": "present" if cf_authed else "missing — run: flarecrawl auth login",
            "read_only": read_only,
            "extras": extras,
            "missing_extras": {
                dep: missing_dep_install_hint(dep)
                for dep, avail in extras.items()
                if not avail
            },
        },
        "features": {
            "agent_safe_default": True,
            "token_caps": True,
            "composite_tools": True,
            "raw_passthrough": True,
            "async_crawl_jobs": True,
            "bot_wall_verdicts": True,
        },
        "api_coverage": {
            "cli_commands_total": 31,
            "cli_commands_covered": 21,
            "gaps": COVERAGE_GAPS,
        },
        "tools": {
            "orientation": orientation,
            "t1_composite": t1,
            "t2_curated": t2,
            "t3_raw": t3,
        },
        "recipes": [
            {
                "task": "Read an article behind a soft paywall",
                "tools": ["read_page"],
                "example": {"url": "https://example.com/article", "js": False},
            },
            {
                "task": "What CMS/framework does this site run?",
                "tools": ["tech_detect"],
                "example": {"url": "https://example.com"},
            },
            {
                "task": "Crawl a docs site to markdown",
                "tools": ["crawl_start", "crawl_status", "crawl_results"],
                "example": (
                    "crawl_start(url, limit=50) → poll crawl_status(job_id) "
                    "→ crawl_results(job_id, fields='url,markdown')"
                ),
            },
            {
                "task": "Blocked by Akamai/Cloudflare — escalate to P6",
                "tools": ["session_inspect", "p6_raw"],
                "example": "p6_raw(mint_url=..., targets=[...], jar='./jar.json')",
            },
            {
                "task": "Search the web and digest the top 5 results",
                "tools": ["research_web"],
                "example": {"query": "...", "top_n": 5},
            },
            {
                "task": "Profile a company's web presence",
                "tools": ["site_overview"],
                "example": {"url": "https://example.com", "include": ["tech", "schema", "links", "favicon", "openapi"]},
            },
            {
                "task": "Extract structured data from multiple URLs",
                "tools": ["extract_data"],
                "example": {"urls": ["https://example.com/products"], "prompt": "Extract product names and prices"},
            },
        ],
        "known_limitations": [
            "Screenshots/PDFs return file paths, not image data — read the file separately",
            "crawl_results caps at max_chars per call — paginate with offset",
            "Interactive auth (OAuth/2FA/CAPTCHA) requires the CLI — see coverage gaps",
            "Free CF tier = 10 min browser time/day — check diagnostics() before large jobs",
            "meta.blocked.terminal=true (Cloudflare 1020) is non-bypassable — do not retry",
        ],
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def capabilities_handler(registry: dict[str, Any], read_only: bool = False) -> dict[str, Any]:
    """Return server capabilities. Call this first.

    Return server capabilities, tool catalogue, and recipes.
    Call this first to understand the full MCP surface.

    Use this for: session orientation, tool selection, understanding what is
    and isn't possible without trial-and-error.

    Parameters:
      (none)

    Returns:
      Rich §30.2.1 capabilities object: version, mode, permissions, features,
      api_coverage (with gap list), tools grouped t1/t2/t3/orientation, recipes,
      known_limitations.

    Default behaviour:
      Returns the full capabilities object assembled from the live registry.
      Response is deterministic within a session; re-fetch if auth changes.

    Limitations:
      - CF auth status is checked via env vars only (no live network call)
      - extras matrix reflects installed packages, not runtime availability

    When to use vs alternatives:
      This is always the first call. Use permissions_check for a specific action.
      Use diagnostics for live auth/quota status. Use schema_generate for full
      machine-readable tool schemas.
    """
    return _build_capabilities(registry, read_only)


def guide_handler(topic: str | None = None) -> dict[str, Any]:
    """Return the agent orientation guide, whole or by topic.

    Return the flarecrawl agent orientation guide (AGENTS.md content),
    optionally filtered to a specific topic.

    Use this for: understanding flarecrawl's mental model, escalation doctrine,
    JSON shapes, exit codes, hard-target patterns.

    Parameters:
      topic (str, optional) — Topic filter. Valid values: hard-targets, json,
        errors, rules, auth, or None for the full guide.

    Returns:
      {"data": {"text": "<guide text>", "topic": "<topic or 'full'>"}, "meta": {}}

    Default behaviour:
      Returns the full AGENTS.md guide when topic is None.

    Limitations:
      - Guide content is static (matches installed flarecrawl version)
      - Topics are keyword-filtered sections, not structured API

    When to use vs alternatives:
      capabilities() — structured data about available tools
      diagnostics() — live auth/quota status
      schema_generate() — machine-readable tool schemas
    """
    args = ["guide"]
    if topic:
        args.append(topic)
    result = run_cli(args, tool_name="guide", max_chars=None)
    if result.get("ok") is False:
        return result
    # guide outputs plain text, not JSON — normalise
    data = result.get("data", result)
    if isinstance(data, dict) and "text" in data:
        text = data["text"]
    elif isinstance(data, str):
        text = data
    else:
        text = str(data)
    return {
        "ok": True,
        "data": {"text": text, "topic": topic or "full"},
        "meta": {},
    }


def diagnostics_handler() -> dict[str, Any]:
    """Return auth status, CF usage/quota, cache stats, optional-dep availability.

    Return a health and configuration snapshot: CF auth status, account info,
    browser time usage/quota, response cache stats, negotiate domain cache,
    and the optional-dependency availability matrix.

    Use this for: debugging why a tool call failed, checking how much browser
    quota remains before a large job, verifying optional extras are installed.

    Parameters:
      (none)

    Returns:
      {"data": {"auth": {...}, "usage": {...}, "cache": {...}, "negotiate": {...},
        "extras": {...}}, "meta": {}}

    Default behaviour:
      Aggregates auth status + usage + cache status + negotiate status into one
      response. Each section independently try/except — partial results on failure.

    Limitations:
      - Live auth check requires CF API token — missing token yields auth.status=missing
      - Usage data requires CF auth
      - Cache/negotiate stats are local-process metadata

    When to use vs alternatives:
      permissions_check — preflight for a specific action
      capabilities — full tool catalogue with static permissions
    """
    sections: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []

    # Auth status
    try:
        auth_result = run_cli(["auth", "status", "--json"], tool_name="diagnostics", max_chars=None)
        sections["auth"] = auth_result.get("data", auth_result)
    except Exception as exc:  # noqa: BLE001
        sections["auth"] = {"status": "error", "message": str(exc)}
        errors.append({"section": "auth", "error": str(exc)})

    # Usage
    try:
        usage_result = run_cli(["usage", "--json"], tool_name="diagnostics", max_chars=None)
        sections["usage"] = usage_result.get("data", usage_result)
    except Exception as exc:  # noqa: BLE001
        sections["usage"] = {"status": "error", "message": str(exc)}
        errors.append({"section": "usage", "error": str(exc)})

    # Cache status
    try:
        cache_result = run_cli(["cache", "status", "--json"], tool_name="diagnostics", max_chars=None)
        sections["cache"] = cache_result.get("data", cache_result)
    except Exception as exc:  # noqa: BLE001
        sections["cache"] = {"status": "error", "message": str(exc)}
        errors.append({"section": "cache", "error": str(exc)})

    # Negotiate status
    try:
        neg_result = run_cli(["negotiate", "status", "--json"], tool_name="diagnostics", max_chars=None)
        sections["negotiate"] = neg_result.get("data", neg_result)
    except Exception as exc:  # noqa: BLE001
        sections["negotiate"] = {"status": "error", "message": str(exc)}
        errors.append({"section": "negotiate", "error": str(exc)})

    # Optional extras matrix
    extras = _check_optional_deps()
    sections["extras"] = {
        dep: {
            "available": avail,
            "install": missing_dep_install_hint(dep) if not avail else None,
        }
        for dep, avail in extras.items()
    }

    result: dict[str, Any] = {"ok": True, "data": sections, "meta": {}}
    if errors:
        result["_errors"] = errors
    return result


def permissions_check_handler(action: str) -> dict[str, Any]:
    """Check whether an action is possible with current auth and installed extras.

    Return a preflight verdict: allowed/denied + reason + next_steps for the
    specified action.

    Use this for: checking before a potentially expensive call, understanding
    why a previous call returned AUTH_REQUIRED or CAPABILITY_MISMATCH.

    Parameters:
      action (str, required) — The action to check. Examples: "scrape", "stealth",
        "local-browser", "search", "recipe", "auth", "read-only", "screenshot",
        "crawl".

    Returns:
      {"data": {"allowed": bool, "action": str, "reason": str, "next_steps": [...]}}

    Default behaviour:
      Checks env vars, installed packages, and auth chain state.

    Limitations:
      - Does not make a live CF API call — env/keyring state only
      - "search" checks JINA_API_KEY only (alternative: DuckDuckGo fallback)

    When to use vs alternatives:
      diagnostics — full health snapshot including live auth
      capabilities — static permissions overview
    """
    action_lower = action.lower().replace("-", "_")

    # Canonical credential chain: env -> keyring -> .env -> legacy config
    try:
        from flarecrawl.config import get_api_token

        cf_authed = bool(get_api_token())
    except Exception:  # noqa: BLE001
        cf_authed = False

    extras = _check_optional_deps()

    checks: dict[str, tuple[bool, str]] = {
        "scrape": (cf_authed, "CF API token required" if not cf_authed else "CF auth present"),
        "crawl": (cf_authed, "CF API token required" if not cf_authed else "CF auth present"),
        "screenshot": (cf_authed, "CF API token required" if not cf_authed else "CF auth present"),
        "pdf": (cf_authed, "CF API token required" if not cf_authed else "CF auth present"),
        "fetch": (True, "fetch uses content negotiation — no auth needed for public URLs"),
        "search": (
            extras["search (JINA_API_KEY)"],
            "JINA_API_KEY env var required" if not extras["search (JINA_API_KEY)"] else "JINA_API_KEY present",
        ),
        "stealth": (
            extras["stealth (curl_cffi)"],
            "curl_cffi not installed" if not extras["stealth (curl_cffi)"] else "curl_cffi available",
        ),
        "local_browser": (
            extras["local-browser (playwright)"],
            "playwright not installed" if not extras["local-browser (playwright)"] else "playwright available",
        ),
        "recipe": (
            extras["recipes (pyyaml)"],
            "pyyaml not installed" if not extras["recipes (pyyaml)"] else "pyyaml available",
        ),
        "auth": (
            cf_authed,
            "CF API token required — run: flarecrawl auth login" if not cf_authed else "CF auth present",
        ),
        "cdp": (
            extras["cdp (websockets)"],
            "websockets not installed" if not extras["cdp (websockets)"] else "websockets available",
        ),
        "videos": (
            extras["videos (yt-dlp)"],
            "yt-dlp not installed" if not extras["videos (yt-dlp)"] else "yt-dlp available",
        ),
    }

    # Normalise action
    for key in checks:
        if action_lower in (key, key.replace("_", "-"), key.replace("_", "")):
            allowed, reason = checks[key]
            next_steps: list[dict[str, Any]] = []
            if not allowed:
                install_hint = missing_dep_install_hint(
                    next(
                        (dep for dep in extras if key.replace("_", "-") in dep),
                        action,
                    )
                )
                next_steps = [
                    {"try": install_hint, "with": {}, "why": reason},
                    {"try": "diagnostics", "with": {}, "why": "Full health status including auth."},
                ]
            return {
                "ok": True,
                "data": {
                    "allowed": allowed,
                    "action": action,
                    "reason": reason,
                    "next_steps": next_steps,
                },
            }

    # Unknown action — return generic
    return {
        "ok": True,
        "data": {
            "allowed": cf_authed,
            "action": action,
            "reason": f"Unknown action '{action}' — defaulting to CF auth check",
            "next_steps": [
                {"try": "diagnostics", "with": {}, "why": "See full auth and capability status."},
                {"try": "capabilities", "with": {}, "why": "Browse the full tool catalogue."},
            ],
        },
    }


def schema_generate_handler(registry: dict[str, Any]) -> dict[str, Any]:
    """Return the full tool catalogue as one machine-readable document.

    Return complete JSON Schema definitions for all registered MCP tools,
    enriched with Forma-specific metadata (tier, personas, covers).

    Use this for: building multi-step plans, programmatically inspecting
    parameter schemas, offline reference.

    Parameters:
      (none)

    Returns:
      {"data": {"tools": [{"name": ..., "tier": ..., "parameters": {...},
        "personas": [...], "short_description": ..., "covers": ...}]}}

    Default behaviour:
      Returns all tools in registration order.

    Limitations:
      - Parameter schemas are hand-authored (no --describe auto-generation)
      - Return value schemas are descriptive, not machine-validated

    When to use vs alternatives:
      capabilities() — summarised catalogue (lighter, use first)
      Individual tool schemas — fetch on demand when planning a specific call
    """
    tools_out = []
    for name, defn in registry.items():
        tools_out.append({
            "name": name,
            "tier": defn.get("tier", "t2"),
            "short_description": defn.get("short_description", ""),
            "personas": defn.get("personas", []),
            "covers": defn.get("covers", []),
            "parameters": defn.get("parameters", {}),
        })

    return {
        "ok": True,
        "data": {"tools": tools_out},
        "meta": {"count": len(tools_out)},
    }
