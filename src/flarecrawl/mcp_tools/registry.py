"""Tool registry for the flarecrawl MCP surface.

The registry maps tool names to their handler functions, metadata, and
parameter schemas.  It is importable WITHOUT the ``mcp`` package installed.

Read-only mode excludes: page_interact, site_download, p6_raw,
recipe_run_raw, spider_raw.

``covers`` attributes declare which CLI commands each tool covers
(used by the §30.11 coverage audit in tests/test_mcp_coverage.py).
"""

from __future__ import annotations

import functools
from typing import Any

from .composite import (
    check_page_changes_handler,
    extract_data_handler,
    read_page_handler,
    research_web_handler,
    site_overview_handler,
)
from .curated import (
    crawl_results_handler,
    crawl_start_handler,
    crawl_status_handler,
    fetch_url_handler,
    openapi_discover_handler,
    page_favicon_handler,
    page_interact_handler,
    page_links_handler,
    page_pdf_handler,
    page_schema_handler,
    page_screenshot_handler,
    session_inspect_handler,
    session_list_handler,
    site_download_handler,
    tech_detect_handler,
    urls_discover_handler,
    web_search_handler,
)
from .orientation import (
    diagnostics_handler,
    guide_handler,
    permissions_check_handler,
)
from .raw import (
    crawl_raw_handler,
    design_extract_raw_handler,
    extract_raw_handler,
    fetch_raw_handler,
    p6_raw_handler,
    recipe_run_raw_handler,
    scrape_raw_handler,
    spider_raw_handler,
    tech_detect_raw_handler,
)

# ---------------------------------------------------------------------------
# Tools excluded in read-only mode (write/destructive operations)
# ---------------------------------------------------------------------------

READ_ONLY_EXCLUDED: frozenset[str] = frozenset({
    "page_interact",
    "site_download",
    "p6_raw",
    "recipe_run_raw",
    "spider_raw",
})

# ---------------------------------------------------------------------------
# Registry definition
# ---------------------------------------------------------------------------
# Each entry: name → {
#   "handler": callable,
#   "tier": orientation|t1|t2|t3,
#   "short_description": str (≤80 chars, verb-first),
#   "personas": list[str],
#   "covers": list[str],   # CLI commands this tool covers (§30.11 audit)
#   "parameters": dict,    # JSON-Schema-style parameter docs
# }

_RAW_REGISTRY: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # Orientation (5)
    # ------------------------------------------------------------------
    "capabilities": {
        "handler": None,  # patched at build_registry() — needs registry ref
        "tier": "orientation",
        "short_description": "Return server capabilities, tool catalogue, recipes. Call this first.",
        "personas": [],
        "covers": [],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "guide": {
        "handler": guide_handler,
        "tier": "orientation",
        "short_description": "Return the agent orientation guide, whole or by topic.",
        "personas": [],
        "covers": ["guide"],
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic filter. Valid values: hard-targets, json, errors, rules, auth. "
                        "Omit for full guide."
                    ),
                },
            },
            "required": [],
        },
    },
    "diagnostics": {
        "handler": diagnostics_handler,
        "tier": "orientation",
        "short_description": "Return auth status, CF usage/quota, cache stats, optional-dep availability.",
        "personas": [],
        "covers": ["usage", "auth status", "cache status", "negotiate status"],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "permissions_check": {
        "handler": permissions_check_handler,
        "tier": "orientation",
        "short_description": "Check whether an action is possible with current auth and installed extras.",
        "personas": [],
        "covers": ["auth status"],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The action to check. Examples: scrape, stealth, local-browser, "
                        "search, recipe, auth, read-only, screenshot, crawl."
                    ),
                },
            },
            "required": ["action"],
        },
    },
    "schema_generate": {
        "handler": None,  # patched at build_registry() — needs registry ref
        "tier": "orientation",
        "short_description": "Return the full tool catalogue as one machine-readable document.",
        "personas": [],
        "covers": [],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ------------------------------------------------------------------
    # T1 Composite (5)
    # ------------------------------------------------------------------
    "read_page": {
        "handler": read_page_handler,
        "tier": "t1",
        "short_description": "Read any URL as clean markdown with automatic routing and paywall retry.",
        "personas": ["research"],
        "covers": ["scrape"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to read."},
                "js": {"type": "boolean", "description": "Force JS rendering.", "default": False},
                "max_chars": {"type": "integer", "description": "Content truncation limit.", "default": 40000},
                "fresh": {"type": "boolean", "description": "Bypass response cache.", "default": False},
                "agent_safe": {"type": "boolean", "description": "Apply agent-safe sanitisation.", "default": True},
            },
            "required": ["url"],
        },
    },
    "research_web": {
        "handler": research_web_handler,
        "tier": "t1",
        "short_description": "Search the web and read the top N results in one call.",
        "personas": ["research"],
        "covers": ["search", "scrape"],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_n": {"type": "integer", "description": "Number of results.", "default": 5},
                "scrape": {"type": "boolean", "description": "Scrape result content.", "default": True},
                "max_chars_per_result": {"type": "integer", "default": 15000},
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
    "site_overview": {
        "handler": site_overview_handler,
        "tier": "t1",
        "short_description": "Profile a site: tech stack, structured data, links, favicon, and API specs.",
        "personas": ["intel"],
        "covers": ["tech-detect", "schema", "map", "favicon", "openapi"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The site URL."},
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["tech", "schema", "links", "favicon", "openapi"]},
                    "description": "Sections to include. Omit for all.",
                    "default": ["tech", "schema", "links", "favicon", "openapi"],
                },
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "extract_data": {
        "handler": extract_data_handler,
        "tier": "t1",
        "short_description": "Extract structured data from URLs with an AI prompt or JSON schema.",
        "personas": ["extraction"],
        "covers": ["extract"],
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to extract from."},
                "prompt": {"type": "string", "description": "Extraction instruction."},
                "json_schema": {"type": "object", "description": "Optional JSON Schema for extraction shape."},
                "max_urls": {"type": "integer", "default": 10},
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["urls", "prompt"],
        },
    },
    "check_page_changes": {
        "handler": check_page_changes_handler,
        "tier": "t1",
        "short_description": "Check whether a page changed since it was last cached.",
        "personas": ["monitoring"],
        "covers": ["scrape"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to check."},
                "max_chars": {"type": "integer", "default": 10000},
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    # ------------------------------------------------------------------
    # T2 Curated (17)
    # ------------------------------------------------------------------
    "web_search": {
        "handler": web_search_handler,
        "tier": "t2",
        "short_description": "Search the web and return URLs, titles, and snippets.",
        "personas": ["research"],
        "covers": ["search"],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    "fetch_url": {
        "handler": fetch_url_handler,
        "tier": "t2",
        "short_description": "Fetch a URL with auto-routing: binary, JSON, text, or browser HTML.",
        "personas": ["extraction", "research"],
        "covers": ["fetch"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "output_path": {"type": "string", "description": "Save binary output to this path."},
                "max_chars": {"type": "integer", "default": 40000},
                "stealth": {"type": "boolean", "default": False},
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "page_links": {
        "handler": page_links_handler,
        "tier": "t2",
        "short_description": "Discover URLs linked from a page.",
        "personas": ["crawl"],
        "covers": ["map"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "include_subdomains": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["url"],
        },
    },
    "urls_discover": {
        "handler": urls_discover_handler,
        "tier": "t2",
        "short_description": "Discover URLs from sitemaps, RSS feeds, and page links.",
        "personas": ["crawl"],
        "covers": ["discover"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "limit": {"type": "integer", "default": 500},
                "sitemaps": {"type": "boolean", "default": True},
                "feeds": {"type": "boolean", "default": True},
                "links": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "page_schema": {
        "handler": page_schema_handler,
        "tier": "t2",
        "short_description": "Extract LD+JSON, OpenGraph, and Twitter card metadata.",
        "personas": ["extraction", "intel"],
        "covers": ["schema"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "schema_type": {"type": "string", "description": "Filter by schema type (e.g. Product, Article)."},
            },
            "required": ["url"],
        },
    },
    "page_favicon": {
        "handler": page_favicon_handler,
        "tier": "t2",
        "short_description": "Find the best favicon URL for a site.",
        "personas": ["intel"],
        "covers": ["favicon"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "all_favicons": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    "page_screenshot": {
        "handler": page_screenshot_handler,
        "tier": "t2",
        "short_description": "Take a screenshot of a page. Returns file path, not image data.",
        "personas": ["monitoring"],
        "covers": ["screenshot"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "output_path": {"type": "string"},
                "full_page": {"type": "boolean", "default": False},
                "selector": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    "page_pdf": {
        "handler": page_pdf_handler,
        "tier": "t2",
        "short_description": "Generate a PDF of a page. Returns file path, not PDF data.",
        "personas": ["archival"],
        "covers": ["pdf"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "output_path": {"type": "string"},
                "landscape": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    "page_interact": {
        "handler": page_interact_handler,
        "tier": "t2",
        "short_description": "Fill forms and click elements on interactive pages.",
        "personas": ["extraction"],
        "covers": ["interact"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "fill": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fill directives: 'selector=value'.",
                },
                "click": {"type": "array", "items": {"type": "string"}, "description": "CSS selectors to click."},
                "screenshot_path": {"type": "string"},
                "max_chars": {"type": "integer", "default": 40000},
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "tech_detect": {
        "handler": tech_detect_handler,
        "tier": "t2",
        "short_description": "Detect technologies used by a website with noise filtering.",
        "personas": ["intel"],
        "covers": ["tech-detect"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "cdp": {"type": "boolean", "default": False},
                "min_confidence": {"type": "integer", "default": 0},
                "only_categories": {"type": "string"},
                "exclude_categories": {
                    "type": "string",
                    "default": "Miscellaneous,Security,Tag managers,RUM",
                },
                "agent_safe": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    "openapi_discover": {
        "handler": openapi_discover_handler,
        "tier": "t2",
        "short_description": "Discover and download OpenAPI/Swagger specs for a site.",
        "personas": ["intel"],
        "covers": ["openapi"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "probe": {"type": "boolean", "default": True},
                "download_dir": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    "crawl_start": {
        "handler": crawl_start_handler,
        "tier": "t2",
        "short_description": "Start an async site crawl. Returns a job_id — poll crawl_status.",
        "personas": ["crawl"],
        "covers": ["crawl"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "include_paths": {"type": "array", "items": {"type": "string"}},
                "exclude_paths": {"type": "array", "items": {"type": "string"}},
                "no_render": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    "crawl_status": {
        "handler": crawl_status_handler,
        "tier": "t2",
        "short_description": "Check the status of a running crawl job.",
        "personas": ["crawl"],
        "covers": ["crawl"],
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job_id from crawl_start."},
            },
            "required": ["job_id"],
        },
    },
    "crawl_results": {
        "handler": crawl_results_handler,
        "tier": "t2",
        "short_description": "Fetch paginated results from a completed crawl job.",
        "personas": ["crawl"],
        "covers": ["crawl"],
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "fields": {"type": "string", "default": "url,markdown"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["job_id"],
        },
    },
    "site_download": {
        "handler": site_download_handler,
        "tier": "t2",
        "short_description": "Download an entire site as files. Returns a file manifest.",
        "personas": ["archival"],
        "covers": ["download"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "output_format": {"type": "string", "enum": ["markdown", "html"], "default": "markdown"},
                "output_dir": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    "session_list": {
        "handler": session_list_handler,
        "tier": "t2",
        "short_description": "List saved browser sessions (cookie jars).",
        "personas": ["power"],
        "covers": ["session list"],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "session_inspect": {
        "handler": session_inspect_handler,
        "tier": "t2",
        "short_description": "Inspect a saved session's cookie freshness and validity.",
        "personas": ["power"],
        "covers": ["session inspect"],
        "parameters": {
            "type": "object",
            "properties": {
                "name_or_path": {
                    "type": "string",
                    "description": "Session name or path. Prefix with @ for named sessions.",
                },
            },
            "required": ["name_or_path"],
        },
    },
    # ------------------------------------------------------------------
    # T3 Raw (9)
    # ------------------------------------------------------------------
    "scrape_raw": {
        "handler": scrape_raw_handler,
        "tier": "t3",
        "short_description": "Scrape with full CLI flag access (~50 flags). Use for full API fidelity.",
        "personas": ["power"],
        "covers": ["scrape"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to scrape."},
                "options": {
                    "type": "object",
                    "description": (
                        "Additional CLI flags as a dict. Keys use underscores (auto-converted to --hyphens). "
                        "Bool True = bare flag. List = repeated flag. "
                        "Example: {\"stealth\": true, \"wait_until\": \"networkidle2\", "
                        "\"capture_pattern\": \"*.csv\"}"
                    ),
                },
            },
            "required": ["url"],
        },
    },
    "fetch_raw": {
        "handler": fetch_raw_handler,
        "tier": "t3",
        "short_description": "Fetch a URL with full CLI flag access. Use for full API fidelity.",
        "personas": ["power"],
        "covers": ["fetch"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "options": {"type": "object", "description": "Additional CLI flags as a dict."},
            },
            "required": ["url"],
        },
    },
    "crawl_raw": {
        "handler": crawl_raw_handler,
        "tier": "t3",
        "short_description": "Crawl with full CLI flag access and --wait mode. Use for full API fidelity.",
        "personas": ["power", "crawl"],
        "covers": ["crawl"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "options": {
                    "type": "object",
                    "description": "Additional flags. Example: {\"wait\": true, \"limit\": 100}",
                },
            },
            "required": ["url"],
        },
    },
    "extract_raw": {
        "handler": extract_raw_handler,
        "tier": "t3",
        "short_description": "Run AI data extraction with full CLI flag access. Use for full API fidelity.",
        "personas": ["power", "extraction"],
        "covers": ["extract"],
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "urls": {"type": "array", "items": {"type": "string"}},
                "options": {"type": "object"},
            },
            "required": ["prompt"],
        },
    },
    "tech_detect_raw": {
        "handler": tech_detect_raw_handler,
        "tier": "t3",
        "short_description": "Detect technologies with full CLI flag access. Use for full API fidelity.",
        "personas": ["power", "intel"],
        "covers": ["tech-detect"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL. Omit for --stdin HTML input."},
                "options": {"type": "object"},
            },
            "required": [],
        },
    },
    "spider_raw": {
        "handler": spider_raw_handler,
        "tier": "t3",
        "short_description": "Direct-HTTP spider with full CLI flag access. Use for full API fidelity.",
        "personas": ["power", "crawl"],
        "covers": ["spider"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "options": {
                    "type": "object",
                    "description": "Example: {\"limit\": 1000, \"workers\": 10, \"resume\": \"JOB_ID\"}",
                },
            },
            "required": ["url"],
        },
    },
    "p6_raw": {
        "handler": p6_raw_handler,
        "tier": "t3",
        "short_description": "Run the P6 mint-replay protocol against hard targets. Use for full API fidelity.",
        "personas": ["power"],
        "covers": ["p6"],
        "parameters": {
            "type": "object",
            "properties": {
                "mint_url": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "string"}},
                "options": {
                    "type": "object",
                    "description": "Example: {\"jar\": \"./jar.json\", \"stealth\": true}",
                },
            },
            "required": ["mint_url"],
        },
    },
    "recipe_run_raw": {
        "handler": recipe_run_raw_handler,
        "tier": "t3",
        "short_description": "Run a YAML recipe (headless steps only). Use for full API fidelity.",
        "personas": ["power"],
        "covers": ["recipe"],
        "parameters": {
            "type": "object",
            "properties": {
                "recipe_file": {"type": "string", "description": "Path to YAML recipe file."},
                "options": {
                    "type": "object",
                    "description": "Example: {\"dry_run\": true, \"resume\": true}",
                },
            },
            "required": ["recipe_file"],
        },
    },
    "design_extract_raw": {
        "handler": design_extract_raw_handler,
        "tier": "t3",
        "short_description": "Run design extract, coherence, or diff modes. Use for full API fidelity.",
        "personas": ["power"],
        "covers": ["design"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["extract", "coherence", "diff"],
                    "default": "extract",
                },
                "url2": {"type": "string", "description": "Second URL for diff mode."},
                "options": {"type": "object"},
            },
            "required": ["url"],
        },
    },
}


def build_registry(read_only: bool = False) -> dict[str, dict[str, Any]]:
    """Return the registry dict, applying read-only filtering.

    Capabilities and schema_generate handlers are bound here (they need
    a reference to the registry itself, so we can't store them in the
    static _RAW_REGISTRY).
    """
    from .orientation import capabilities_handler, schema_generate_handler  # noqa: F401

    registry = dict(_RAW_REGISTRY)

    # Patch handlers that need registry access
    registry["capabilities"]["handler"] = functools.partial(
        capabilities_handler,
        registry=registry,
        read_only=read_only,
    )
    registry["schema_generate"]["handler"] = functools.partial(
        schema_generate_handler,
        registry=registry,
    )

    # Patch orientation tools that need no args but stored as partials
    registry["guide"]["handler"] = guide_handler
    registry["diagnostics"]["handler"] = diagnostics_handler
    registry["permissions_check"]["handler"] = permissions_check_handler

    # Apply read-only filter
    if read_only:
        for name in READ_ONLY_EXCLUDED:
            registry.pop(name, None)

    return registry
