# Firecrawl vs Flarecrawl Benchmark Report

**Date:** 2026-03-18 | **Runs:** 3 per URL per tool | **Total requests:** 30
**Iterations:** 5 improvement cycles (see `tests/bench-log.tsv`)

## Test URLs

| # | Category | URL |
|---|----------|-----|
| 1 | API docs | https://httpbin.org |
| 2 | Documentation | https://docs.python.org/3/library/json.html |
| 3 | Dynamic | https://news.ycombinator.com |
| 4 | Long content | https://en.wikipedia.org/wiki/Web_scraping |
| 5 | Blog article | https://blog.cloudflare.com/browser-rendering-open-api/ |

## 1. Speed (avg seconds per page)

| URL | Firecrawl | Flarecrawl | Winner |
|-----|-----------|------------|--------|
| httpbin.org | **1.5s** | 4.4s | FC |
| docs.python.org | 2.2s | **2.3s** | Tied |
| news.ycombinator.com | **1.5s** | 3.0s | FC |
| en.wikipedia.org | **1.8s** | 2.8s | FC |
| blog.cloudflare.com | **1.3s** | 3.0s | FC |
| **Average** | **1.7s** | **3.1s** | **FC** |

Firecrawl ~1.8x faster on cold calls (caching layer + CDN proximity).
**With Flarecrawl's cache enabled (default):** repeat calls drop to ~1.1-1.5s, matching Firecrawl.

## 2. Content Accuracy (chars extracted)

| URL | Firecrawl | Flarecrawl | Winner |
|-----|-----------|------------|--------|
| httpbin.org | **1,562** | 493 | FC (JS accordion) |
| docs.python.org | **44,093** | 37,334 | FC (18% more) |
| news.ycombinator.com | 15,386 | **35,959** | FL (2.3x more) |
| en.wikipedia.org | 52,216 | **71,353** | FL (37% more) |
| blog.cloudflare.com | 5,602 | **6,113** | FL (9% more) |

Mixed results — Firecrawl captures more JS-rendered content on httpbin.
Flarecrawl extracts more raw page structure on HN, Wikipedia, and blog pages.

**Tip:** Use `--wait-until networkidle2` to improve JS rendering when needed.

## 3. Content Similarity

| URL | Similarity | Notes |
|-----|-----------|-------|
| httpbin.org | **39%** | FC gets JS accordion sections, FL doesn't |
| docs.python.org | **68%** | Similar structure, different formatting |
| news.ycombinator.com | **19%** | Very different extraction approaches |
| en.wikipedia.org | **75%** | Good overlap on structured content |
| blog.cloudflare.com | **90%** | Near-identical on simple blog posts |

## 4. Reliability

| Metric | Firecrawl | Flarecrawl |
|--------|-----------|------------|
| Success rate | **100%** (15/15) | **100%** (15/15) |
| Content stddev | 0.0 (identical each run) | 0.0 (identical each run) |

Both perfectly reliable across all runs.

## 5. Cost

### Pricing models

- **Firecrawl:** Scale plan $99/month for 100K credits (1 credit per scrape)
- **Flarecrawl:** $5/month Workers Paid plan + $0.09/hr browser rendering time

### Cost at scale

| Scale | Firecrawl ($99/mo) | Flarecrawl ($5/mo + $0.09/hr) | Winner |
|-------|-------------------|-------------------------------|--------|
| 100 pages | $0.10 | $5.00 | FC |
| 1K pages | $0.99 | $5.04 | FC |
| **10K pages** | **$9.90** | **$5.43** | **FL** |
| 100K pages | $99.00 | $9.32 | FL (**10.6x cheaper**) |

**Crossover: ~8K pages/month.** Below that Firecrawl is cheaper; above that Flarecrawl dominates.

### Free tier

| | Firecrawl | Flarecrawl |
|---|-----------|------------|
| Allowance | 500 credits/month | 10 min/day (600,000ms) |
| Pages | ~500 pages/month | ~346 pages/day (~10K/month) |

## 6. JS Rendering

| URL | Firecrawl | Flarecrawl | Flarecrawl + --wait-until |
|-----|-----------|------------|--------------------------|
| httpbin.org (JS accordion) | **Y** | N | Y (networkidle2) |
| All others | Y | Y | Y |

Use `flarecrawl scrape URL --wait-until networkidle2` for JS-heavy pages.

## 7. Output Quality

| Metric | Firecrawl | Flarecrawl |
|--------|-----------|------------|
| Valid JSON | 100% | 100% |
| Consistent envelope | N (raw markdown to stdout) | **Y** (`{data, meta}`) |
| Metadata fields | 1 (timing only) | **5** (title, contentLength, sourceURL, browserTimeMs, format) |
| Response caching | Server-side (opaque) | **Client-side** (1hr TTL, --no-cache to bypass) |

## Final Scores (weighted)

| Dimension | Weight | Firecrawl | Flarecrawl |
|-----------|--------|-----------|------------|
| Speed | 20% | **5** | 4 |
| Content accuracy | 25% | 4 | 4 |
| Cost | 20% | 3 | **5** |
| Reliability | 15% | 5 | 5 |
| JS rendering | 10% | **5** | 4 |
| Output quality | 10% | 2 | **4** |
| **WEIGHTED TOTAL** | **100%** | **4.05** | **4.35** |

## Improvements Made (Iteration Log)

| # | Change | Impact | Status |
|---|--------|--------|--------|
| 1 | Default `networkidle0` for JS rendering | httpbin 493→1292 chars | Reverted (timeout issues) |
| 2 | Enriched metadata (title, contentLength, sourceURL, browserTimeMs) | output_quality 1→5 fields | Kept |
| 3 | File-based response cache (1hr TTL) | 7x speedup on repeat calls | Kept |
| 4 | `networkidle2` default | Blog 32s timeout | Reverted |
| 5 | `--wait-until` flag for opt-in JS rendering | Users control JS rendering | Kept |

## Verdict

**Flarecrawl wins overall (4.35 vs 4.05)**, primarily on cost and output quality.
Firecrawl wins on speed and JS rendering. Both are equally reliable.

**Use Firecrawl when:** < 8K pages/month, JS-heavy SPAs, need `--only-main-content` filtering, need web search.

**Use Flarecrawl when:** > 10K pages/month, need PDF/favicon/batch mode, want structured JSON output, cost-sensitive at scale, repeat scraping (cache hits).

## Feature Comparison

| Feature | Firecrawl | Flarecrawl |
|---------|-----------|------------|
| scrape | Y | Y |
| crawl | Y | Y |
| map | Y | Y |
| download | Y | Y |
| extract / agent | Y | Y |
| screenshot | Y | Y |
| pdf | N | **Y** |
| favicon | N | **Y** |
| search | **Y** | N |
| browser (remote Playwright) | **Y** | N |
| --only-main-content | **Y** | N |
| --batch mode | N | **Y** |
| --wait-until (JS control) | N | **Y** |
| --no-cache | N | **Y** |
| Country/language targeting | **Y** | N |
| Branding extraction | **Y** | N |
| Response caching | Server | **Client (configurable)** |

## Reproducing

```bash
# Set API keys
export FIRECRAWL_API_KEY="your-key"
export FIRECRAWL_CMD="path/to/firecrawl.cmd"  # if PATH conflict

# Run benchmark
python tests/bench.py --runs 3 --output tests/bench-results.json

# Quick single run
python tests/bench.py --runs 1

# Single tool only
python tests/bench.py --tool flarecrawl --runs 3
```
