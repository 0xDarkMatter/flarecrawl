---
name: flarecrawl-ops
description: "Use flarecrawl CLI for web scraping via Cloudflare Browser Rendering. Triggers: flarecrawl, scrape, crawl, browser rendering, cloudflare scrape, web content"
version: 1.0.0
category: operations
tool: flarecrawl
requires:
  bins: ["flarecrawl"]
  skills: []
allowed-tools: "Read Bash Grep"
---

# Flarecrawl Operations

Cloudflare Browser Rendering CLI — drop-in replacement for `firecrawl`, much cheaper. Renders JavaScript, extracts markdown/HTML, crawls sites, takes screenshots, generates PDFs, and does AI-powered data extraction.

## Auth Check

```bash
flarecrawl auth status --json
# Exit code 2 = not authenticated → run: flarecrawl auth login
```

## Common Operations

### 1. Scrape a page to markdown

```bash
flarecrawl scrape https://example.com
flarecrawl scrape https://example.com --json
```

### 2. Batch scrape from file

```bash
# urls.txt: one URL per line, # comments supported
flarecrawl scrape --batch urls.txt --workers 5
# Output: NDJSON — {"index": 0, "status": "ok", "data": {...}}
```

### 3. Crawl a site

```bash
flarecrawl crawl https://docs.example.com --wait --limit 50 --json
flarecrawl crawl https://docs.example.com --wait --progress --limit 100 --ndjson --fields url,markdown
```

### 4. Discover URLs

```bash
flarecrawl map https://example.com --json | jq '.data[]'
```

### 5. Download site as files

```bash
flarecrawl download https://docs.example.com --limit 50
# Creates: .flarecrawl/<domain>/<page>.md
```

## Batch & Parallel

`scrape` and `extract` support `--batch`/`-b` and `--workers`/`-w`:

```bash
# Scrape
flarecrawl scrape --batch urls.txt --workers 5

# Extract
flarecrawl extract "Get page title" --batch urls.txt --workers 3
```

- Input: plain text (one per line), JSON array, or NDJSON (auto-detected)
- Output: NDJSON with `{index, status, data/error}` envelope
- Workers: default 3, max 10 (CF concurrency limit)
- Partial failures don't stop processing — errors reported inline

### Map → batch scrape pipeline

```bash
flarecrawl map https://docs.example.com --json | jq -r '.data[]' > urls.txt
flarecrawl scrape --batch urls.txt --workers 5
```

## Output Interpretation

### JSON envelope (`--json`)

```json
{"data": {"url": "...", "content": "# Markdown...", "elapsed": 2.9}, "meta": {"format": "markdown"}}
```

### Batch NDJSON (`--batch`)

```json
{"index": 0, "status": "ok", "data": {"url": "...", "content": "...", "elapsed": 1.2}}
{"index": 1, "status": "error", "error": {"code": "TIMEOUT", "message": "..."}}
```

### Key fields

| Field | Where | Description |
|-------|-------|-------------|
| `data.content` | scrape | Markdown or HTML content |
| `data.elapsed` | scrape | Seconds to fetch |
| `data.records[]` | crawl | Array of crawled pages |
| `data.records[].markdown` | crawl | Page markdown content |
| `data.records[].url` | crawl | Page URL |
| `data.job_id` | crawl | Job ID for status checks |
| `data.total` | crawl | Total pages crawled |
| `data.browser_seconds` | crawl | Total browser time used |

## Gotchas

- **Free tier: 10 min/day** — check with `flarecrawl usage --json` before large batches
- **Free tier: 3 concurrent browsers** — use `--workers 3` max
- **Paid tier: 10 concurrent** — use `--workers 10` max
- **Crawl is async** — always use `--wait` or poll with `--status`
- **Crawl jobs persist 14 days** on Cloudflare
- **`--input` is legacy** — use `--batch` for new workflows
- **Binary outputs** (screenshot/pdf) save to files by default, use `--json` for base64
- **Auto-retry** on 429/502/503 with exponential backoff (3 attempts)

## Pipe Patterns

| Chain | Command | Use Case |
|-------|---------|----------|
| → jq | `flarecrawl scrape URL --json \| jq '.data.content'` | Extract content |
| → file | `flarecrawl scrape URL > page.md` | Save markdown |
| → batch | `flarecrawl map URL --json \| jq -r '.data[]' > urls.txt && flarecrawl scrape --batch urls.txt` | Map then scrape |
| → jq filter | `flarecrawl scrape --batch urls.txt \| jq 'select(.status=="ok") \| .data.content'` | Filter batch results |
| → crawl ndjson | `flarecrawl crawl JOB_ID --ndjson --fields url,markdown \| jq -r '.markdown'` | Stream crawl content |
| → fslack | `flarecrawl usage --json \| jq '"Usage: " + (.data.today_seconds\|tostring) + "s"' \| xargs fslack messages send --channel ops --text` | Alert on usage |
