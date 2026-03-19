# Feature Evaluation Matrix

> Candidates for future Flarecrawl releases. Score each on Impact (user value), Effort (dev cost), and Feasibility (CF API support). Decide: **Build**, **Defer**, or **Discard**.

**Scoring:** 1 (low) — 3 (high)

**Status:** Evaluating | Approved | Deferred | Discarded

## Evaluation Criteria

| Criterion | Weight | Definition |
|-----------|--------|------------|
| **Impact** | 40% | How much value does this add for users? |
| **Effort** | 30% | Dev time — 1=days, 2=hours, 3=trivial |
| **Feasibility** | 30% | Does CF API support this natively or do we need workarounds? |

---

## Candidates

### Tier 1 — High confidence

| # | Feature | Impact | Effort | Feasibility | Score | Status | Notes |
|---|---------|--------|--------|-------------|-------|--------|-------|
| 1 | `--js "expression"` — run JS in page, return result | 3 | 3 | 3 | 3.0 | Evaluating | Shot-scraper pattern. CF has no direct JS eval endpoint, but `/json` with prompt could work. Alternatively use `--body` with custom JS. |
| 2 | `--selector ".main"` — extract content inside CSS selector | 3 | 3 | 3 | 3.0 | Evaluating | CF `/scrape` endpoint supports `elements` field. Already plumbed in client. |
| 3 | `--wait-for ".content"` — wait for CSS element | 3 | 3 | 3 | 3.0 | Evaluating | Already in `_build_body()` as `waitForSelector`. Just needs CLI flag exposure. |
| 4 | Stdin piping — `cat page.html \| flarecrawl scrape --stdin` | 2 | 3 | 3 | 2.6 | Evaluating | Process local HTML without API call. Uses extract.py locally. |
| 5 | Feed/sitemap discovery — `flarecrawl discover URL` | 3 | 2 | 3 | 2.7 | Evaluating | Parse RSS/Atom feeds + XML sitemaps. New command. |
| 6 | `--format accessibility` — ARIA tree dump | 2 | 2 | 2 | 2.0 | Evaluating | Useful for LLM agents. CF may not expose accessibility tree directly. |

### Tier 2 — Needs investigation

| # | Feature | Impact | Effort | Feasibility | Score | Status | Notes |
|---|---------|--------|--------|-------------|-------|--------|-------|
| 7 | `--query "topic"` — BM25 relevance filter | 2 | 2 | 3 | 2.3 | Evaluating | Filter output to content matching a query. Pure Python post-processing. |
| 8 | Auth state save/load — `--session cookies.json` | 3 | 2 | 2 | 2.4 | Evaluating | Save cookies after login, reuse across runs. CF supports `cookies` field. |
| 9 | `--precision` / `--recall` toggle | 2 | 2 | 3 | 2.3 | Evaluating | Tune extract_main_content aggressiveness. Trafilatura pattern. |
| 10 | `--deduplicate` on crawl | 2 | 2 | 3 | 2.3 | Evaluating | Hash-based dedup to skip duplicate content in crawl results. |
| 11 | YAML batch config — `flarecrawl batch config.yml` | 2 | 1 | 3 | 2.0 | Evaluating | Declarative multi-URL, multi-format operations. Shot-scraper pattern. |
| 12 | `--scroll` — auto-scroll before capture | 2 | 2 | 2 | 2.0 | Evaluating | Infinite scroll / lazy-load pages. May need JS injection. |
| 13 | HAR capture — `--har output.har` | 2 | 1 | 1 | 1.4 | Evaluating | Save all network requests. CF API unlikely to expose this. |

### Tier 3 — Nice to have

| # | Feature | Impact | Effort | Feasibility | Score | Status | Notes |
|---|---------|--------|--------|-------------|-------|--------|-------|
| 14 | Internet Archive fallback — `--archived` | 1 | 2 | 3 | 1.9 | Evaluating | Query Wayback Machine on 404. Trafilatura pattern. |
| 15 | Language targeting — `--language de` | 1 | 2 | 3 | 1.9 | Evaluating | Filter crawl results by detected language. |
| 16 | `--backup-dir` — save raw HTML alongside output | 1 | 3 | 3 | 2.2 | Evaluating | Archival use case. |
| 17 | VLM image captioning | 2 | 1 | 1 | 1.4 | Evaluating | Auto-caption images via Workers AI. Jina pattern. |
| 18 | Cookie banner / overlay removal — `--magic` | 2 | 1 | 2 | 1.7 | Evaluating | Crawl4AI pattern. Would need JS injection or heuristic CSS removal. |

---

## Decision Log

| Date | Feature | Decision | Rationale |
|------|---------|----------|-----------|
| | | | |

---

## How to Use This Document

1. **Evaluate** — Score candidates, discuss trade-offs
2. **Decide** — Move to Approved / Deferred / Discarded with rationale in Decision Log
3. **Build** — Approved items move to implementation (task list)
4. **Review** — Re-evaluate Deferred items each release cycle

Inspired by: Jina Reader, Crawl4AI, Trafilatura, Shot-scraper, Playwright MCP.
