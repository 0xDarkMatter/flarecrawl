---
name: flarecrawl-ops
description: "Use flarecrawl CLI for web scraping via Cloudflare Browser Rendering. Triggers: flarecrawl, scrape, crawl, browser rendering, cloudflare scrape, web content, tech-detect, anti-bot, mcp server"
version: 1.1.0
category: operations
tool: flarecrawl
requires:
  bins: ["flarecrawl"]
  skills: []
allowed-tools: "Read Bash Grep"
---

# Flarecrawl Operations

Cloudflare Browser Rendering CLI — Firecrawl-compatible, cost-efficient at scale.
Renders JavaScript, extracts markdown/HTML, crawls sites, takes screenshots,
generates PDFs, AI-extracts structured data, fingerprints tech stacks, and
cracks anti-bot walls. Also ships an **MCP server** and a **CDP** mode for
persistent, authenticated browser sessions.

> **Orient first.** `flarecrawl guide` prints the packaged agent reference
> (when/why each command, JSON shapes, exit codes, footguns). `flarecrawl guide
> <topic>` drills in (aliases: `hard-targets`, `json`, `errors`, `rules`,
> `auth`). `flarecrawl guide --list` shows topics. Treat `guide` as the source
> of truth; this skill is the quick map.

## Auth Check

```bash
flarecrawl auth status --json
# Exit code 2 = not authenticated → run: flarecrawl auth login
flarecrawl usage --json          # free tier: 10 min/day (600,000ms) — check before big jobs
```

## MCP Server (for agents)

Flarecrawl exposes a Model Context Protocol stdio server — **36 tools** in a
three-tier surface — so an agent can call the toolkit directly instead of
shelling out.

```bash
uv pip install 'flarecrawl[mcp]'
flarecrawl mcp                 # start stdio server
flarecrawl mcp --read-only     # excludes page_interact, site_download, p6_raw, recipe_run_raw, spider_raw
```

Wire into `.mcp.json`:

```json
{"mcpServers": {"flarecrawl": {"command": "flarecrawl", "args": ["mcp"]}}}
```

| Tier | Count | When | Examples |
|------|------:|------|----------|
| Orientation | 5 | session start / capability check | `capabilities()`, `guide()`, `diagnostics()` |
| T1 Composite | 5 | ~80% of tasks | `read_page()`, `research_web()`, `site_overview()` |
| T2 Curated | 17 | single-resource ops | `web_search()`, `tech_detect()`, `crawl_start()` |
| T3 Raw | 9 | full CLI fidelity | `scrape_raw()`, `p6_raw()`, `spider_raw()` |

**Always call `capabilities()` first** — one call returns the full catalogue,
permissions, coverage gaps, and worked recipes. Content tools default to
`--agent-safe`. Errors return `{"ok": false, "error": {"code", "next_steps"}}`;
`meta.blocked` verdicts auto-suggest recovery (Akamai → stealth/p6; CF-1020 →
terminal). Coverage gaps (CLI only): `videos`, `authcrawl`, `frontier`, `batch`,
`auth login/logout`, `cache clear`, `rules *`, `cdp *`, `webmcp *`, some
`session` subcommands, `--interactive/--headed`.

## Core Operations

```bash
# Scrape → markdown (or html/links/images/summary/schema)
flarecrawl scrape https://example.com --json
flarecrawl scrape URL --format html --json
flarecrawl scrape URL --js                 # SPAs: wait for networkidle0
flarecrawl scrape URL --only-main-content  # strip nav/footer (link-density gated)

# Crawl a site (async; --wait to block)
flarecrawl crawl https://docs.example.com --wait --limit 50 --json
flarecrawl crawl JOB_ID --ndjson --fields url,markdown   # stream large results

# Discover URLs
flarecrawl map URL --json                  # links on one page
flarecrawl discover URL --json             # sitemaps + RSS/Atom + links
flarecrawl spider URL --limit 500          # direct HTTP crawl, NO browser cost, resumable

# Fetch by content type (4-branch routing: binary / JSON / raw-text / HTML→CF)
flarecrawl fetch URL --json                # XML/CSV/RSS/KML/YAML returned verbatim, no CF auth
flarecrawl fetch URL -o file.pdf           # binary → file

# AI extraction (Workers AI, $0) — see flarecrawl-extraction skill
flarecrawl extract "Get product names and prices" --urls URL --json

# Save site / media
flarecrawl download URL --limit 50         # → .flarecrawl/<domain>/<page>.md
flarecrawl screenshot URL -o page.png --full-page
flarecrawl pdf URL -o page.pdf
flarecrawl favicon URL --all --json
```

## Tech Detection (Wappalyzer)

```bash
flarecrawl tech-detect URL --json          # CMS, framework, CDN, analytics, SaaS — single GET, 0 CF time
flarecrawl tech-detect URL --json --exclude-categories "Miscellaneous,Security,Tag managers,RUM"  # clean stack picture
flarecrawl tech-detect -i urls.txt --workers 10 --json     # batch
flarecrawl tech-detect URL --render --json # Playwright + JS-globals probe (~880 more fingerprints)
flarecrawl scrape URL --tech-detect --json # inline on scrape/crawl/fetch too
```

## Hard Targets & Anti-Bot

For sites that fingerprint TLS, return stubs to CF, or hide data in JS state:

```bash
# Enhanced extraction + TLS impersonation (needs curl_cffi)
flarecrawl scrape URL --paywall --stealth --json   # metadata.source shows the strategy that won

# Local Chromium backend (bypass CF stub) + capture XHR bodies
flarecrawl scrape URL --js --browser local --headed
flarecrawl scrape URL --js --capture-pattern "*.csv,*.json" --capture-dir ./out/

# Mass-download from a captured manifest, reusing session + stealth TLS
flarecrawl scrape URL --then-fetch-from manifest.csv --then-fetch-column "Link" --then-fetch-output ./files/

# P6: mint→replay (local Chromium mints cookie shells → curl_cffi replays w/ real JA3/JA4)
flarecrawl p6 https://site/ --jar jar.json --target https://site/api --output-dir ./out
flarecrawl session inspect @site           # offline jar freshness; exit ≠0 unless fresh

# YAML multi-step browser flows (resumable)
flarecrawl recipe flow.yml --dry-run
flarecrawl recipe flow.yml --resume
```

`meta.blocked {blocked, vendor, kind, terminal, signal}` is the bot-wall source
of truth (`scrape` CDP, `fetch --json`, `recipe`). `terminal: true` (CF-1020) =
non-bypassable, don't burn a re-mint. Don't string-match block pages yourself.

## CDP — Persistent / Authenticated Sessions

```bash
uv pip install websockets                   # or flarecrawl[cdp]
flarecrawl scrape URL --interactive --json  # log in via DevTools (OAuth/2FA/CAPTCHA), cookies auto-saved
flarecrawl scrape URL --cdp --keep-alive 60 --save-cookies session.json --har traffic.har --json
flarecrawl scrape URL --load-cookies session.json --cdp --json
flarecrawl cdp sessions --json && flarecrawl cdp close
```
CDP flags (`--interactive`, `--live-view`, `--record`, `--save/load-cookies`,
`--keep-alive`, `--browser-cookies`) all auto-promote to `--cdp`.

## Agent-Safe Mode

```bash
flarecrawl scrape URL --agent-safe --json   # sanitise adversarial content (DeepMind "AI Agent Traps")
```
13 sanitisers, two phases (HTML + text). `metadata.agentSafety` reports findings.
Prompt injection removed (short-line bias <200 chars); semantic manipulation
flagged, not removed. Works on scrape/crawl/download/extract and `--stdin`.

## Batch & Parallel

`scrape` and `extract` support `--batch`/`-b` and `--workers`/`-w`:

```bash
flarecrawl scrape --batch urls.txt --workers 5
flarecrawl map URL --json | jq -r '.data[]' > urls.txt && flarecrawl scrape --batch urls.txt --workers 5
```

- Input: plain text (one/line, `#` comments), JSON array, or NDJSON (auto-detected)
- Output: NDJSON `{index, status, data/error}`, sorted by index
- **Workers: default 3, max 50** (CF raised concurrency to 120). Override with `FLARECRAWL_MAX_WORKERS`
- Partial failures don't stop processing; batch fails fast on auth/permission errors

## Output Interpretation

```json
{"data": {"url": "...", "content": "# Markdown...", "elapsed": 2.9}, "meta": {"format": "markdown"}}
```

| Field | Where | Description |
|-------|-------|-------------|
| `data.content` | scrape | Markdown or HTML |
| `data.elapsed` | scrape | Seconds to fetch |
| `data.records[]` | crawl | Crawled pages (`.url`, `.markdown`, `.status`) |
| `data.job_id` / `data.total` / `data.browser_seconds` | crawl | Job id, page count, browser time |
| `metadata.source` | scrape | `content-negotiation` (0 browser time) vs `browser-rendering` |
| `meta.blocked` | scrape/fetch/recipe | Bot-wall verdict |

## Gotchas

- **Free tier: 10 min/day**, 3 concurrent browsers → `--workers 3`. Paid: up to `--workers 50`
- **Crawl is async** — use `--wait` (or poll `--status`); jobs persist 14 days on CF
- **Markdown content negotiation is automatic** — `scrape` tries `Accept: text/markdown` first (0 browser time); `--no-negotiate` to force browser
- **`--input` is legacy** — use `--batch`
- **Binary outputs** (screenshot/pdf) save to files; `--json` for base64
- **Auto-retry** on 429/502/503 with backoff (3 attempts)
- **`--cdp`, `--stealth`, `--paywall`, `--browser local`, recipes** need optional extras (websockets / curl_cffi / playwright / PyYAML)
- **Exit codes:** 0 ok · 1 error · 2 auth · 3 not-found · 4 validation · 5 forbidden · 7 rate-limited

## Pipe Patterns

| Chain | Command |
|-------|---------|
| → content | `flarecrawl scrape URL --json \| jq '.data.content'` |
| → map then scrape | `flarecrawl map URL --json \| jq -r '.data[]' > urls.txt && flarecrawl scrape --batch urls.txt` |
| → filter batch | `flarecrawl scrape --batch urls.txt \| jq 'select(.status=="ok") \| .data.content'` |
| → stream crawl | `flarecrawl crawl JOB_ID --ndjson --fields url,markdown \| jq -r '.markdown'` |
| → tech-detect feed | `flarecrawl tech-detect -i urls.txt --workers 10 --json \| jq -r '.data[].technologies[].name'` |

For full flag reference and JSON shapes, run `flarecrawl guide <topic>` or read the repo `AGENTS.md`.
