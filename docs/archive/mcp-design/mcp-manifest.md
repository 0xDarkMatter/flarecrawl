# Flarecrawl MCP Surface — Design Manifest

**Status**: ARCHIVED — shipped in v0.31.0 (2026-06-13). Frozen design record.
**Date**: 2026-06-10 (design) · archived 2026-06-16
**Spec**: Forma Protocol §29 (transport) + §30 (tool design)
**Tool**: flarecrawl v0.30.1 (at design time; surface shipped in v0.31.0)

---

## Summary

**36 tools** across orientation + 3 tiers, serving **5 adapted personas**, with
**11 declared coverage gaps** (all config/interactive commands with CLI workarounds).

| Group | Count | Examples |
|---|---:|---|
| Orientation | 5 | `capabilities`, `guide`, `diagnostics` |
| T1 Composite | 5 | `read_page`, `research_web`, `site_overview` |
| T2 Curated | 17 | `web_search`, `tech_detect`, `crawl_start` |
| T3 Raw | 9 | `scrape_raw`, `p6_raw`, `spider_raw` |

**Key design decisions** (deviations from §30 defaults, see §"Deviations"):

1. **Action-shaped, not resource-shaped.** Flarecrawl operates on arbitrary URLs,
   not API entities. There are no FKs to resolve, no reference tables, no
   `MCP_DEFAULT_FIELDS`/`MCP_REFS_RESOLVE` Pydantic machinery. "Materialisation"
   here means **token-budget discipline**: content tools accept `max_chars`
   (default 40,000) and flag truncation in `meta`.
2. **`--agent-safe` defaults ON for T1/T2 content tools.** The MCP consumer is
   by definition an LLM context window — the DeepMind-taxonomy sanitiser is
   exactly for this. T3 raw preserves CLI defaults (off). Overridable per call.
3. **Binary outputs return file paths, never base64.** Screenshots/PDFs are
   written to disk and the path returned. Base64 blobs in tool results blow
   agent context for zero gain (stdio MCP servers are local; the path works).
4. **Async crawl jobs use the start/status/results triplet** rather than a
   blocking `--wait` call — MCP tool calls shouldn't block for minutes.
5. **`meta.blocked` is surfaced verbatim** in every envelope — flarecrawl's
   existing machine-readable bot-wall verdict is already §30.9-grade structured
   error material; the MCP layer adds `next_steps` derived from it (e.g.
   `vendor: akamai` → "try scrape_raw with stealth=true, or p6_raw").

---

## Prerequisite deviations (must be acknowledged at sign-off)

| §30 prerequisite | Status | Resolution |
|---|---|---|
| `describe --json` (§09) | **Missing** | MCP tool schemas hand-authored in `mcp_serve.py` (the §29 describe→schema codegen path is unavailable). Optionally add `describe` later; not blocking. |
| `docs/api-spec/*.md` | **Missing** | Coverage audit runs against the **CLI command list** instead of API endpoints. The "API" here is CF Browser Run + the open web; CLI commands are the canonical surface. |
| Pydantic `resources/*_models.py` | **Missing** | No entity models exist or are needed. Envelope shapes are defined per-tool in this manifest. |
| forma-verifier verdict | **Not run** | Flarecrawl predates the verifier. Risk accepted: the CLI is mature (1,400+ tests, 30 releases). |

---

## Stage 1.1 — Inventory

**CLI commands (top-level)**: guide, scrape, search, fetch, crawl, map, download,
extract, screenshot, pdf, favicon, recipe, p6, batch, discover, schema, usage,
openapi, interact, spider, videos, tech-detect, authcrawl, frontier

**CLI sub-apps**: auth (login/status/logout), cache (status/clear),
negotiate (status/clear), rules (list/show/add/path),
session (save/list/show/delete/validate/inspect), cdp (connect/sessions/close),
webmcp (discover/call), design (extract/coherence/diff)

**Substrate for in-process execution**: `flarecrawl.client.Client`,
`flarecrawl.fetch`, `flarecrawl.search`, `flarecrawl.wappalyzer`, etc. —
the MCP server calls **library functions**, not the Typer CLI via CliRunner,
wherever a clean function exists (most commands delegate to a module function).
Where CLI-only logic exists (flag interaction rules), the build phase extracts
it into a shared function as part of the cli.py split.

---

## Stage 1.2 — Persona pass

§30.3's five business personas (support/accounts/BI/sales/marketing) target
CRM-style tools. Flarecrawl's MCP consumers are **agents doing web work**.
Adapted personas:

| Persona | Typical questions | Tools that answer them |
|---|---|---|
| **Research agent** | "Read this page." "What does this article say?" "Search the web for X and digest the top 5 results." "This page is paywalled/blocked — get it anyway." | `read_page` (T1), `research_web` (T1), `web_search` (T2) |
| **Data-extraction agent** | "Get all products and prices from these URLs." "Extract fields matching this schema." "Pull the LD+JSON." "Download the CSV this page loads on init." | `extract_data` (T1), `page_schema` (T2), `fetch_url` (T2), `scrape_raw` (T3, capture flags) |
| **Site-intelligence agent** | "What tech does this site run?" "Profile this company's web presence." "Do they have an API/OpenAPI spec?" "What's their favicon/branding?" | `site_overview` (T1), `tech_detect` (T2), `openapi_discover` (T2), `page_favicon` (T2) |
| **Crawl/archival agent** | "Crawl these docs into markdown." "Map every URL on the site." "Save the whole site to disk." "Is the crawl done yet?" | `crawl_start/status/results` (T2), `page_links` (T2), `urls_discover` (T2), `site_download` (T2) |
| **Monitoring agent** | "Has this page changed since last check?" "Screenshot it." "How much browser quota have I used?" | `check_page_changes` (T1), `page_screenshot` (T2), `diagnostics` |
| **Power user** (overlay) | Hard targets: TLS fingerprinting, Akamai/CF walls, cookie jars, mint→replay, high-volume spidering, multi-step recipes | All `_raw` T3 tools, `session_list/inspect` (T2) |

---

## Stage 1.3 — Tier catalogue

### Orientation (5)

| Tool | Short description (≤80 chars) |
|---|---|
| `capabilities` | Return server capabilities, tool catalogue, recipes. Call this first. |
| `guide` | Return the agent orientation guide, whole or by topic (hard-targets, json…) |
| `diagnostics` | Return auth status, CF usage/quota, cache stats, optional-dep availability |
| `permissions_check` | Check whether an action is possible with current auth and installed extras |
| `schema_generate` | Return the full tool catalogue as one machine-readable document |

Notes:
- `guide(topic=None)` wraps the existing `flarecrawl guide` — flarecrawl already
  ships its own orientation doc; the MCP surface reuses it instead of inventing one.
- `diagnostics` absorbs `usage`, `auth status`, `cache status`, `negotiate status`
  and adds the optional-extras matrix (websockets/curl_cffi/playwright/yt-dlp/pyyaml
  present or not) — each missing extra reported with its install command.
- `permissions_check(action)` covers: CF auth present? `JINA_API_KEY` set (search)?
  curl_cffi installed (stealth)? playwright installed (local browser)? read-only mode?
- `explore_fields` from §30.8.2 is **not applicable** (no entity schemas) — `guide`
  fills the orientation role. Deviation declared.

### T1 Composite (5)

| Tool | Signature (sketch) | Substrate |
|---|---|---|
| `read_page` | `(url, js=False, max_chars=40000, fresh=False)` | negotiate → scrape → paywall cascade; agent-safe ON; returns markdown + metadata + `meta.blocked` |
| `research_web` | `(query, top_n=5, scrape=True, max_chars_per_result=15000)` | search --scrape pipeline; per-result digest envelope |
| `site_overview` | `(url, include=["tech","schema","links","favicon","openapi"])` | tech-detect + schema + map + favicon + openapi probe; §30.7.4 partial-error accumulation |
| `extract_data` | `(urls, prompt, json_schema=None, max_urls=10)` | extract via Workers AI; NDJSON-style per-URL results |
| `check_page_changes` | `(url, max_chars=10000)` | scrape --diff; returns changed/unchanged verdict + diff summary |

`read_page` is the flagship — the one tool 80% of sessions will use. Its routing
mirrors the CLI's escalation doctrine automatically: content negotiation (zero
browser time) → browser render → paywall cascade on extraction failure, reporting
which strategy won in `meta.source`.

### T2 Curated (17)

| Tool | Signature (sketch) | Wraps |
|---|---|---|
| `web_search` | `(query, limit=10)` | search (no scrape) |
| `fetch_url` | `(url, output_path=None, max_chars=40000, stealth=False)` | fetch 4-branch routing; binary requires output_path |
| `page_links` | `(url, include_subdomains=False, limit=200)` | map |
| `urls_discover` | `(url, limit=500, sitemaps=True, feeds=True, links=True)` | discover |
| `page_schema` | `(url, type=None)` | schema (LD+JSON/OG/Twitter) |
| `page_favicon` | `(url, all=False)` | favicon |
| `page_screenshot` | `(url, output_path, full_page=False, selector=None, width=None, height=None)` | screenshot → returns path |
| `page_pdf` | `(url, output_path, landscape=False)` | pdf → returns path |
| `page_interact` | `(url, fill=[], click=[], screenshot_path=None, max_chars=40000)` | interact |
| `tech_detect` | `(url, cdp=False, min_confidence=0, only_categories=None, exclude_categories="Miscellaneous,Security,Tag managers,RUM")` | tech-detect; **noise filter ON by default** (CLI default off) |
| `openapi_discover` | `(url, probe=True, download_dir=None)` | openapi |
| `crawl_start` | `(url, limit=50, include_paths=None, exclude_paths=None, no_render=False)` | crawl (fire-and-forget) |
| `crawl_status` | `(job_id)` | crawl --status |
| `crawl_results` | `(job_id, fields="url,markdown", limit=20, offset=0, max_chars=60000)` | crawl + pagination; §30.6.5 truncation meta |
| `site_download` | `(url, limit=50, format="markdown", output_dir=None)` | download → returns file manifest |
| `session_list` | `()` | session list |
| `session_inspect` | `(name_or_path)` | session inspect (jar freshness verdict) |

All content-returning T2 tools: `max_chars` cap + `meta.truncated`, agent-safe ON
(overridable `agent_safe=False`), `meta.blocked` passthrough.

### T3 Raw (9)

Full CLI fidelity. Naming per §30.4.2 (`_raw` suffix = deliberate friction).
Each accepts the core arguments explicitly plus `options: dict` mapping any
remaining CLI flag (`{"wait_until": "networkidle2", "capture_pattern": "*.csv"}`).
Agent-safe OFF by default (CLI parity). No `max_chars` cap by default.

| Tool | Wraps | Notes |
|---|---|---|
| `scrape_raw` | scrape (all ~50 flags) | incl. capture, then-fetch, browser=local, cdp, har, recordings |
| `fetch_raw` | fetch (all flags) | incl. session/impersonate TLS path |
| `crawl_raw` | crawl (all flags) | incl. --wait blocking mode for short crawls |
| `extract_raw` | extract (all flags) | incl. schema-file, batch |
| `tech_detect_raw` | tech-detect (all flags) | incl. --render (local Playwright), --stdin html |
| `spider_raw` | spider (all flags) | direct-HTTP high-volume; resume, adaptive-delay |
| `p6_raw` | p6 (all flags) | mint→replay; jar, targets-from, cool-down |
| `recipe_run_raw` | recipe (headless steps only) | headed recipes = declared gap |
| `design_extract_raw` | design extract/coherence/diff via `mode` param | design-system extraction |

---

## Stage 1.4 — Resolution policy (adapted)

No FK resolution applies. The flarecrawl equivalents:

| §30.5 concept | Flarecrawl adaptation |
|---|---|
| Materialise FK → label | `meta.source` names the winning strategy; `meta.blocked` materialises the bot-wall verdict; tech-detect results carry category names inline |
| Sparse fieldsets | `max_chars` content caps; `fields=` on `crawl_results` (already a CLI concept); compact per-result digests in `research_web` |
| `_refs` preservation | `job_id` (crawl), file paths (screenshot/pdf/download), session names — always returned for chaining |
| snake_case (T1/T2) vs native (T3) | T1/T2 envelopes use snake_case meta keys; T3 returns the CLI's `--json` envelope verbatim |
| Reference-table caching | negotiate domain cache + response cache already exist in the CLI; reused as-is |

---

## Stage 1.5 — Coverage map

Every CLI command reachable via T2/T3, or declared:

| CLI command | Reachable via | Status |
|---|---|---|
| guide | `guide` | covered |
| scrape | `read_page` (T1) + `scrape_raw` (T3) | covered |
| search | `web_search` (T2) + `research_web` (T1) | covered |
| fetch | `fetch_url` (T2) + `fetch_raw` (T3) | covered |
| crawl | `crawl_start/status/results` (T2) + `crawl_raw` (T3) | covered |
| map | `page_links` (T2) | covered |
| download | `site_download` (T2) | covered |
| extract | `extract_data` (T1) + `extract_raw` (T3) | covered |
| screenshot | `page_screenshot` (T2) | covered |
| pdf | `page_pdf` (T2) | covered |
| favicon | `page_favicon` (T2) | covered |
| schema | `page_schema` (T2) | covered |
| discover | `urls_discover` (T2) | covered |
| openapi | `openapi_discover` (T2) | covered |
| interact | `page_interact` (T2) | covered |
| tech-detect | `tech_detect` (T2) + `tech_detect_raw` (T3) | covered |
| spider | `spider_raw` (T3) | covered |
| p6 | `p6_raw` (T3) | covered |
| recipe | `recipe_run_raw` (T3) | partial — headed gap declared |
| design | `design_extract_raw` (T3) | covered |
| usage | `diagnostics` | covered |
| auth status | `diagnostics` + `permissions_check` | covered |
| session list/inspect | `session_list/inspect` (T2) | covered |
| session save/delete/show/validate | — | **GAP 11** |
| videos | — | **GAP 1** |
| authcrawl | — | **GAP 2** |
| frontier | — | **GAP 3** |
| batch | — | **GAP 4** |
| auth login/logout | — | **GAP 5** |
| cache clear / negotiate clear | — | **GAP 6** |
| rules list/show/add | — | **GAP 7** |
| cdp connect/sessions/close | — | **GAP 8** |
| webmcp discover/call | — | **GAP 9** |
| scrape --interactive / --live-view / --headed | — | **GAP 10** |

### Declared gaps (`[tool.forma.mcp].coverage_gaps`)

| # | Command | Reason | Workaround |
|---|---|---|---|
| 1 | `videos` | Niche; yt-dlp pipeline is CLI-shaped (pipes) | `flarecrawl videos URL --json` via CLI |
| 2 | `authcrawl` | Long-running (hours), resume-oriented — wrong shape for MCP call lifetime | CLI with `--resume` |
| 3 | `frontier` | Debug tool for authcrawl job DBs | CLI |
| 4 | `batch` | YAML-config driver; agents issue parallel tool calls instead | Multiple MCP calls or CLI |
| 5 | `auth login/logout` | Credential entry is human-in-the-loop; MCP must never handle secrets (§ rule: auth is DELEGATED) | `flarecrawl auth login` once; MCP uses the CLI's auth chain |
| 6 | `cache clear`, `negotiate clear` | Destructive config management, low agent value | CLI |
| 7 | `rules *` | User config management | CLI |
| 8 | `cdp *` | Session lifecycle tied to terminal workflows | CLI; revisit if MCP-persistent sessions are wanted later |
| 9 | `webmcp *` | Calling third-party WebMCP tools through our MCP = confusing double-hop | CLI |
| 10 | `--interactive/--live-view/--headed` flags | Require a human at a browser | Run CLI interactively, save session, then use `session_list` + raw tools with the jar |
| 11 | `session save/delete/show/validate` | Cookie-jar management writes/removes secrets on disk — CLI-shaped | CLI; MCP exposes `session_list` + `session_inspect` (read-only) |

---

## Stage 1.6 — `capabilities()` draft

```json
{
  "tool": "flarecrawl",
  "version": "0.31.0",
  "protocol": "forma/0.9",
  "mode": "full",
  "mcp_profile": "curated+raw",

  "permissions": {
    "cf_auth": "ok",
    "cf_account": "5e08…",
    "read_only": false,
    "extras": {
      "stealth (curl_cffi)": true,
      "cdp (websockets)": true,
      "local-browser (playwright)": false,
      "search (JINA_API_KEY)": true,
      "recipes (pyyaml)": true
    }
  },

  "features": {
    "agent_safe_default": true,
    "token_caps": true,
    "composite_tools": true,
    "raw_passthrough": true,
    "async_crawl_jobs": true,
    "bot_wall_verdicts": true
  },

  "api_coverage": {
    "cli_commands_total": 31,
    "cli_commands_covered": 21,
    "gaps": [ "…the 10 declared gaps with reason + workaround…" ]
  },

  "tools": {
    "orientation": ["capabilities", "guide", "diagnostics", "permissions_check", "schema_generate"],
    "t1_composite": [
      {"name": "read_page", "personas": ["research"], "description": "Read any URL as clean markdown — auto-routes negotiate/browser/paywall"},
      {"name": "research_web", "personas": ["research"], "description": "Search the web and read the top results in one call"},
      {"name": "site_overview", "personas": ["intel"], "description": "Profile a site: tech stack, structured data, links, favicon, API specs"},
      {"name": "extract_data", "personas": ["extraction"], "description": "Extract structured data from URLs with an AI prompt or JSON schema"},
      {"name": "check_page_changes", "personas": ["monitoring"], "description": "Check whether a page changed since it was last read"}
    ],
    "t2_curated": ["…17 tools as catalogued…"],
    "t3_raw": ["…9 tools as catalogued…"]
  },

  "recipes": [
    {"task": "Read an article behind a soft paywall", "tools": ["read_page"], "example": {"url": "https://…", "js": false}},
    {"task": "What CMS/framework does this site run?", "tools": ["tech_detect"], "example": {"url": "https://…"}},
    {"task": "Crawl docs site to markdown", "tools": ["crawl_start", "crawl_status", "crawl_results"], "example": "crawl_start(url, limit=50) → poll crawl_status(job_id) → crawl_results(job_id, fields='url,markdown')"},
    {"task": "Blocked by Akamai/Cloudflare", "tools": ["session_inspect", "p6_raw"], "example": "p6_raw(mint_url, jar='./jar.json', targets=[…])"},
    {"task": "Search + digest the top 5 results", "tools": ["research_web"], "example": {"query": "…", "top_n": 5}}
  ],

  "known_limitations": [
    "Screenshots/PDFs return file paths, not image data — read the file separately",
    "crawl_results caps at max_chars per call — paginate with offset",
    "Interactive auth (OAuth/2FA/CAPTCHA) requires the CLI — see gap list",
    "Free CF tier = 10 min browser time/day — check diagnostics() before large jobs",
    "meta.blocked.terminal=true (Cloudflare 1020) is non-bypassable — do not retry"
  ]
}
```

---

## Build-phase notes (Phase 2 preview — not yet authorised)

- **Ordering**: lands **after** the cli.py split so `mcp_serve.py` imports clean
  per-command modules instead of reaching into a 6,882-line file.
- **New files**: `src/flarecrawl/mcp_serve.py` (server + tool registry),
  `src/flarecrawl/mcp_tools/` if the registry warrants splitting.
- **Subcommand**: `flarecrawl mcp serve [--read-only]` (lazy import).
- **Dependency**: `[project.optional-dependencies] mcp = ["mcp>=1.0.0"]`.
- **Read-only mode**: excludes `page_interact`, `site_download`, `p6_raw`,
  `recipe_run_raw`, `spider_raw`, write-path of `fetch_url`.
- **Errors**: every failure returns §30.9 envelope; `meta.blocked` verdicts map to
  `next_steps` automatically (akamai → stealth/p6 escalation; cf_1020_hard →
  "terminal, do not retry").
- **Tests**: Stage 2.7 list + integration smoke per Stage 2.8.
- **AGENTS.md + README**: MCP section per Stage 2.6.

## Squint-test log

Every name reviewed against §30.12.4. Renames applied during drafting:
`scrape` → `read_page` (T1 describes the task, not the mechanism);
`search_and_scrape` → `research_web`; `diff_page` → `check_page_changes`;
`favicon` → `page_favicon` (consistent `page_` prefix for single-page T2 tools).
`tech_detect` kept (domain term, self-describing).
