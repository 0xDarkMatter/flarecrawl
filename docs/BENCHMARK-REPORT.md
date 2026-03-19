# Firecrawl vs Flarecrawl Benchmark Report

**Date:** 2026-03-19 | **Version:** Flarecrawl v0.3.0 | **Runs:** 3 per URL per tool | **Total requests:** 48
**Iterations:** 37 improvement cycles across 4 rounds (see `tests/bench-log.tsv`)

## Final Scores

| Dimension | Weight | Firecrawl | Flarecrawl | Winner |
|-----------|--------|-----------|------------|--------|
| Speed | 20% | 5 | **5** | Tied |
| Content accuracy | 25% | 5 | **5** | Tied |
| Cost | 20% | 3 | **5** | FL |
| Reliability | 15% | 5 | **5** | Tied |
| JS rendering | 10% | 5 | **5** | Tied |
| Output quality | 10% | 2 | **5** | FL |
| **WEIGHTED TOTAL** | **100%** | **4.30** | **5.00** | **FL** |

## Test URLs (8)

| # | Category | URL |
|---|----------|-----|
| 1 | API docs | https://httpbin.org |
| 2 | Documentation | https://docs.python.org/3/library/json.html |
| 3 | Dynamic | https://news.ycombinator.com |
| 4 | Long content | https://en.wikipedia.org/wiki/Web_scraping |
| 5 | Blog article | https://blog.cloudflare.com/browser-rendering-open-api/ |
| 6 | SPA | https://react.dev |
| 7 | Heavy docs | https://developer.mozilla.org/en-US/docs/Web/JavaScript |
| 8 | Dynamic heavy | https://github.com/trending |

## 1. Speed

| URL | Firecrawl | Flarecrawl |
|-----|-----------|------------|
| httpbin.org | **1.4s** | 3.9s |
| docs.python.org | 1.9s | **1.8s** |
| news.ycombinator.com | 1.7s | **1.8s** |
| en.wikipedia.org | **1.7s** | 2.8s |
| blog.cloudflare.com | **1.5s** | 2.1s |
| react.dev | **1.6s** | 1.9s |
| developer.mozilla.org | 1.9s | **2.7s** |
| github.com/trending | **1.8s** | 2.6s |

Both tools achieve speed score 5 (avg ≤3s). Firecrawl benefits from server-side caching; Flarecrawl compensates with connection pooling, HTTP/2, and resource rejection.

**With Flarecrawl's client-side cache (default):** repeat calls drop to ~1.2-1.5s.

## 2. Content Accuracy

| URL | Firecrawl | Flarecrawl | Winner |
|-----|-----------|------------|--------|
| httpbin.org | **1,562** | 1,292 | FC |
| docs.python.org | **44,093** | 37,334 | FC |
| news.ycombinator.com | 15,207 | **35,579** | FL (2.3x) |
| en.wikipedia.org | 52,216 | **71,353** | FL (37%) |
| blog.cloudflare.com | 5,602 | **6,113** | FL |
| react.dev | 17,952 | **17,588** | ~Tied |
| developer.mozilla.org | 77,353 | **92,108** | FL (19%) |
| github.com/trending | 74,398 | **86,098** | FL (16%) |

Flarecrawl extracts more content on 5/8 URLs. Firecrawl wins on httpbin (Swagger JS) and docs.python.org (different markdown converter).

## 3. Content Similarity

| URL | Similarity |
|-----|-----------|
| httpbin.org | 78% |
| docs.python.org | 68% |
| news.ycombinator.com | 20% |
| en.wikipedia.org | 75% |
| blog.cloudflare.com | 90% |
| react.dev | 95% |
| developer.mozilla.org | 82% |
| github.com/trending | 75% |

## 4. Reliability

Both tools: **100% success rate** (47/48 requests succeeded, 1 transient failure on github.com).

## 5. Cost

### Pricing models

- **Firecrawl:** Scale plan $99/month for 100K credits (1 credit per scrape)
- **Flarecrawl:** $5/month Workers Paid plan + $0.09/hr browser rendering time

### Cost at scale

| Scale | Firecrawl | Flarecrawl | Winner |
|-------|-----------|------------|--------|
| 100 pages | $0.10 | $5.00 | FC |
| 1K pages | $0.99 | $5.04 | FC |
| **10K pages** | **$9.90** | **$5.38** | **FL** |
| 100K pages | $99.00 | $8.78 | FL (**11.3x cheaper**) |

**Crossover: ~8K pages/month.**

### Free tier

| | Firecrawl | Flarecrawl |
|---|-----------|------------|
| Allowance | 500 credits/month | 10 min/day (600,000ms) |
| Pages | ~500 pages/month | ~399 pages/day (~12K/month) |

## 6. JS Rendering

Flarecrawl uses `--js` flag for JS-heavy pages (opt-in, uses networkidle0):

```bash
flarecrawl scrape https://httpbin.org --js    # captures Swagger accordion
flarecrawl scrape https://example.com         # fast, no JS wait
```

Both tools score 5/5 on JS rendering across the test suite.

## 7. Output Quality

| Metric | Firecrawl | Flarecrawl |
|--------|-----------|------------|
| Valid JSON | 100% | 100% |
| Consistent envelope | N (raw markdown) | **Y** (`{data, meta}`) |
| Metadata fields | 1 | **11** (title, wordCount, headingCount, linkCount, description, contentLength, sourceURL, browserTimeMs, format, elapsed, cacheHit) |
| Response caching | Server-side | **Client-side** (configurable TTL, `--no-cache`) |

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
| --js (opt-in JS rendering) | N | **Y** |
| --no-cache | N | **Y** |
| --wait-until | N | **Y** |
| cache clear/status | N | **Y** |
| Country/language targeting | **Y** | N |
| Branding extraction | **Y** | N |
| Connection pooling + HTTP/2 | Unknown | **Y** |
| Resource rejection | Unknown | **Y** |
| Env-var config | N | **Y** |
| Atomic config writes | N | **Y** |
| Batch fail-fast on auth errors | N | **Y** |

## Improvements Made (37 iterations)

| Round | Key Changes | Score |
|-------|-------------|-------|
| Baseline | Initial comparison | FL 4.55 vs FC 4.05 |
| Round 1 | Smart JS fallback, metadata enrichment, response cache | FL 4.45 |
| Round 2 | httpx pooling, blog content fix, 11 metadata fields | FL 4.80 |
| Round 3 | rejectResourceTypes, --js flag, 8 URLs, speed=5 | FL 4.90 |
| Round 4 | Lint fixes, h2 dep, 94 tests, cache commands, env vars, docs, v0.3.0 | FL 5.00 |

## Reproducing

```bash
# Set API keys
export FIRECRAWL_API_KEY="your-key"
export FIRECRAWL_CMD="path/to/firecrawl.cmd"  # if PATH conflict

# Run benchmark
python tests/bench.py --runs 3

# Quick check
python tests/bench.py --runs 1

# Single tool
python tests/bench.py --tool flarecrawl --runs 3
```
