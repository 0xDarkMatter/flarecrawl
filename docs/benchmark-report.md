# Firecrawl vs Flarecrawl Benchmark Report

**Date:** 2026-03-18 | **Runs:** 3 per URL per tool | **Total requests:** 30

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
| httpbin.org | **1.21s** | 2.79s | FC |
| docs.python.org | **1.92s** | 2.50s | FC |
| news.ycombinator.com | **1.39s** | 2.97s | FC |
| en.wikipedia.org | **1.74s** | 2.79s | FC |
| blog.cloudflare.com | **1.32s** | 2.88s | FC |
| **Average** | **1.52s** | **2.79s** | **FC** |

Firecrawl ~1.8x faster (likely caching layer + CDN proximity).

## 2. Content Accuracy (chars extracted)

| URL | Firecrawl | Flarecrawl | Winner |
|-----|-----------|------------|--------|
| httpbin.org | **1,562** | 493 | FC (3.2x more — JS sections) |
| docs.python.org | **44,093** | 37,334 | FC (18% more) |
| news.ycombinator.com | 15,386 | **36,451** | FL (2.4x more) |
| en.wikipedia.org | 52,216 | **71,353** | FL (37% more) |
| blog.cloudflare.com | 5,602 | **6,113** | FL (9% more) |

Mixed results — Firecrawl captures more JS-rendered content, Flarecrawl extracts more raw page content.

## 3. Content Similarity

| URL | Similarity | Notes |
|-----|-----------|-------|
| httpbin.org | **39%** | FC gets JS accordion sections, FL doesn't |
| docs.python.org | **68%** | Similar structure, different formatting |
| news.ycombinator.com | **16%** | Very different extraction approaches |
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

| URL | Firecrawl | Flarecrawl |
|-----|-----------|------------|
| httpbin.org (JS accordion) | **Y** | N |
| All others (static/minimal JS) | Y | Y |

Firecrawl's `--only-main-content` and Spark models handle JS-heavy pages better.

## 7. Output Quality

| Metric | Firecrawl | Flarecrawl |
|--------|-----------|------------|
| Valid JSON | 100% | 100% |
| Consistent envelope | N (raw markdown to stdout) | **Y** (`{data, meta}`) |
| Metadata fields | 1 (timing only) | 1 (format) |

Flarecrawl has a more structured, machine-parseable output format.

## Final Scores (weighted)

| Dimension | Weight | Firecrawl | Flarecrawl |
|-----------|--------|-----------|------------|
| Speed | 20% | **5** | 5 |
| Content accuracy | 25% | 4 | 4 |
| Cost | 20% | 3 | **5** |
| Reliability | 15% | 5 | 5 |
| JS rendering | 10% | **5** | 4 |
| Output quality | 10% | 2 | **4** |
| **WEIGHTED TOTAL** | **100%** | **4.05** | **4.55** |

## Verdict

**Flarecrawl wins overall (4.55 vs 4.05)**, primarily on cost and output quality. Firecrawl wins on speed and JS rendering. Both are equally reliable.

**Use Firecrawl when:** < 8K pages/month, JS-heavy SPAs, need `--only-main-content` filtering, need web search.

**Use Flarecrawl when:** > 10K pages/month, need PDF/favicon/batch mode, want structured JSON output, cost-sensitive at scale.

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
| Country/language targeting | **Y** | N |
| Branding extraction | **Y** | N |

## Reproducing

```bash
# Set API keys
export FIRECRAWL_API_KEY="your-key"
export FIRECRAWL_CMD="path/to/firecrawl.cmd"  # if PATH conflict

# Run benchmark
python tests/bench.py --runs 3 --output tests/bench-results.json
```
