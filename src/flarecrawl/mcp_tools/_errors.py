"""Error envelope construction for the flarecrawl MCP surface.

All errors follow the §30.9 structured envelope shape. No error dead-ends an
agent — every failure includes ``next_steps`` unless genuinely unrecoverable.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Exit code → error code mapping
# ---------------------------------------------------------------------------

_EXIT_CODE_MAP: dict[int, tuple[str, str]] = {
    0: ("OK", "success"),
    1: ("UPSTREAM_ERROR", "upstream_error"),
    2: ("AUTH_REQUIRED", "permission_denied"),
    3: ("NOT_FOUND", "not_found"),
    4: ("VALIDATION_ERROR", "validation_error"),
    5: ("CAPABILITY_MISMATCH", "capability_mismatch"),
    6: ("NOT_FOUND", "not_found"),
    7: ("RATE_LIMITED", "upstream_error"),
}

_AUTH_NEXT_STEPS: list[dict[str, Any]] = [
    {
        "try": "flarecrawl auth login",
        "with": {},
        "why": "Run this CLI command once to store credentials — MCP reuses the auth chain.",
    },
    {
        "try": "diagnostics",
        "with": {},
        "why": "Check current auth status and which credentials are available.",
    },
]

_RATE_NEXT_STEPS: list[dict[str, Any]] = [
    {
        "try": "retry after delay",
        "with": {"wait_seconds": 60},
        "why": "Cloudflare Browser Rendering quota resets on a rolling window.",
    },
    {
        "try": "diagnostics",
        "with": {},
        "why": "Check current usage/quota with diagnostics().",
    },
]

_PERMISSION_DENIED_NEXT_STEPS: list[dict[str, Any]] = [
    {
        "try": "permissions_check",
        "with": {"action": "<the action you need>"},
        "why": "Determine exactly which permission or capability is missing.",
    },
]


def exit_code_error(
    exit_code: int,
    raw_output: str,
    tool_name: str = "",
) -> dict[str, Any]:
    """Build a §30.9 error envelope from a CLI exit code."""
    code, category = _EXIT_CODE_MAP.get(exit_code, ("UPSTREAM_ERROR", "upstream_error"))

    next_steps: list[dict[str, Any]]
    if exit_code == 2:
        next_steps = _AUTH_NEXT_STEPS
    elif exit_code == 7:
        next_steps = _RATE_NEXT_STEPS
    else:
        next_steps = [
            {
                "try": "diagnostics",
                "with": {},
                "why": "Check server health and auth status.",
            }
        ]

    return {
        "ok": False,
        "error": {
            "code": code,
            "message": raw_output.strip() or f"CLI exited with code {exit_code}",
            "category": category,
            "exit_code": exit_code,
            "tool": tool_name,
            "next_steps": next_steps,
        },
    }


def blocked_error(
    blocked: dict[str, Any],
    tool_name: str = "",
) -> dict[str, Any]:
    """Build a §30.9 error envelope from a meta.blocked verdict."""
    vendor = blocked.get("vendor", "")
    kind = blocked.get("kind", "")
    terminal = blocked.get("terminal", False)

    next_steps: list[dict[str, Any]] = []

    if terminal or kind in ("cf_1020_hard",):
        next_steps = [
            {
                "try": "none — this block is non-bypassable",
                "with": {},
                "why": (
                    "Cloudflare 1020 (Access Denied) is a hard server-side block. "
                    "The target has blocked this IP/ASN at policy level. Do not retry."
                ),
            }
        ]
    elif vendor in ("akamai", "datadome", "perimeterx"):
        next_steps = [
            {
                "try": "scrape_raw",
                "with": {"url": "<same url>", "options": {"stealth": True}},
                "why": f"{vendor} blocks CF Browser Rendering; TLS impersonation via curl_cffi may bypass it.",
            },
            {
                "try": "p6_raw",
                "with": {"mint_url": "<same url>", "targets": ["<target url>"]},
                "why": "The P6 mint→replay pattern acquires a valid session token then replays it.",
            },
        ]
    else:
        next_steps = [
            {
                "try": "scrape_raw",
                "with": {"url": "<same url>", "options": {"stealth": True, "js": True}},
                "why": "Try stealth TLS + JS rendering to bypass the bot wall.",
            },
            {
                "try": "permissions_check",
                "with": {"action": "stealth"},
                "why": "Verify curl_cffi is installed for stealth mode.",
            },
        ]

    msg = f"Page blocked: vendor={vendor or 'unknown'}, kind={kind or 'bot_wall'}"
    if terminal:
        msg += " [TERMINAL — do not retry]"

    return {
        "ok": False,
        "error": {
            "code": "BLOCKED",
            "message": msg,
            "category": "upstream_error",
            "blocked": blocked,
            "tool": tool_name,
            "next_steps": next_steps,
        },
    }


def permission_denied(
    message: str,
    tool_name: str = "",
    next_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a permission_denied envelope (e.g. read-only mode)."""
    return {
        "ok": False,
        "error": {
            "code": "READ_ONLY_MODE",
            "message": message,
            "category": "permission_denied",
            "tool": tool_name,
            "next_steps": next_steps or _PERMISSION_DENIED_NEXT_STEPS,
        },
    }


def validation_error(
    message: str,
    tool_name: str = "",
) -> dict[str, Any]:
    """Build a validation_error envelope."""
    return {
        "ok": False,
        "error": {
            "code": "VALIDATION_ERROR",
            "message": message,
            "category": "validation_error",
            "tool": tool_name,
            "next_steps": [
                {
                    "try": "schema_generate",
                    "with": {},
                    "why": "Review the full tool catalogue and parameter schemas.",
                }
            ],
        },
    }
