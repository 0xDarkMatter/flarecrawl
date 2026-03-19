# Feature Evaluation Matrix

> Candidates for future Flarecrawl releases. Each feature includes the user problem it solves, who needs it, and why it matters — not just what it does.

**Scoring:** 1 (low) — 3 (high) | **Status:** Evaluating | Approved | Deferred | Discarded

---

## Tier 1 — High Confidence

### 1. `--js "expression"` — Run JavaScript in page, return result

**Status:** Built (v0.6.0) | **Score:** 3.0 (Impact: 3, Effort: 3, Feasibility: 3)

**The problem:** SPAs and dynamic sites render content via JavaScript that isn't in the initial HTML. Users currently have no way to extract computed values, DOM state, or JS variables — they can only get the static HTML/markdown output.

**Who needs this:** Developers scraping React/Vue/Angular apps, anyone extracting prices from e-commerce sites (often rendered client-side), agents that need to read JS-rendered data tables.

**Why it matters:** This is the #1 feature that separates a "fetch and parse" tool from a real browser automation tool. Shot-scraper built its reputation on this. Example: `flarecrawl scrape URL --js "document.querySelectorAll('.price').map(e => e.textContent)"` returns `["$9.99", "$19.99"]`.

**Prior art:** Shot-scraper `javascript` subcommand. CF API has no direct JS eval endpoint, but the `/json` endpoint with a prompt could approximate this, or we could use `--body` with a custom payload.

---

### 2. `--selector ".main"` — Extract content inside a CSS selector

**Status:** Built (v0.6.0) | **Score:** 3.0 (Impact: 3, Effort: 3, Feasibility: 3)

**The problem:** `--only-main-content` uses heuristics to find the main content area, but sometimes users know exactly which element they want. A documentation site might use `#docs-content`, a blog might use `.post-body`. There's no way to say "just give me this specific element."

**Who needs this:** Anyone scraping a specific section of a page — API docs, product descriptions, article bodies, pricing tables. Also useful for LLM agents that need focused context, not the whole page.

**Why it matters:** Reduces noise dramatically. Instead of getting 70K chars of a Wikipedia page, get just the 5K chars inside `#mw-content-text`. Less tokens = cheaper LLM calls, more relevant context. Jina Reader's `X-Target-Selector` is one of their most-used features.

**Prior art:** Jina (`X-Target-Selector`), Shot-scraper (`--selector`). CF `/scrape` endpoint already supports `elements` field — this is just exposing existing plumbing as a CLI flag.

---

### 3. `--wait-for ".content"` — Wait for CSS element before capture

**Status:** Built (v0.6.0) | **Score:** 3.0 (Impact: 3, Effort: 3, Feasibility: 3)

**The problem:** `--js` flag waits for `networkidle0` which is a blunt instrument — it waits until ALL network activity stops. Some pages never reach idle (analytics pings, WebSocket connections). Users need to say "capture the page once THIS specific element appears" rather than waiting for all network to settle.

**Who needs this:** Anyone scraping SPAs where content loads asynchronously — dashboards, Swagger UIs, lazy-loaded feeds. Also critical for pages behind loading spinners.

**Why it matters:** More reliable than timeout-based waiting. Instead of "wait 5 seconds and hope", it's "wait until the data table actually renders." Already plumbed in `_build_body()` as `waitForSelector` — just needs a `--wait-for` CLI flag.

**Prior art:** Jina (`X-Wait-For-Selector`), Playwright (`waitForSelector`). Zero new code in client.py — pure CLI wiring.

---

### 4. Stdin piping — `cat page.html | flarecrawl scrape --stdin`

**Status:** Built (v0.6.0) | **Score:** 2.6 (Impact: 2, Effort: 3, Feasibility: 3)

**The problem:** Users who already have HTML (from curl, wget, another tool, or saved files) have to save it to disk, then point flarecrawl at a URL. There's no way to pipe HTML directly into the extraction pipeline.

**Who needs this:** Shell scripting workflows, CI/CD pipelines that chain tools together, users processing saved HTML archives, anyone debugging by testing extraction against a local file.

**Why it matters:** Makes flarecrawl composable in Unix pipelines — a core CLI design principle. `curl https://example.com | flarecrawl scrape --stdin --only-main-content` avoids a redundant second fetch. Processing happens locally via extract.py with zero API calls.

**Prior art:** Trafilatura accepts stdin natively. This is table-stakes for any CLI content extraction tool.

---

### 5. Feed/sitemap discovery — `flarecrawl discover URL`

**Status:** Built (v0.6.0) | **Score:** 2.7 (Impact: 3, Effort: 2, Feasibility: 3)

**The problem:** Before crawling a site, users need to know what's there. `flarecrawl map` discovers links on a single page, but misses RSS feeds and XML sitemaps that often contain the complete URL inventory. Users manually check `/sitemap.xml`, `/feed`, `/rss` — tedious and error-prone.

**Who needs this:** SEO auditors, content archivists, anyone building a crawl pipeline. "Show me every URL this site publishes" is the first question before any large crawl.

**Why it matters:** Sitemaps are the canonical URL list for a site — they're how search engines discover pages. RSS feeds are how content sites publish updates. Combining both gives a much more complete picture than link crawling alone. A `discover` command that checks sitemaps, feeds, AND link crawling would be unique.

**Prior art:** Trafilatura (`--feed`, `--sitemap`, `--explore`). No other scraping CLI combines all three discovery methods.

---

### 6. `--format accessibility` — ARIA tree dump

**Status:** Evaluating | **Score:** 2.0 (Impact: 2, Effort: 2, Feasibility: 2)

**The problem:** LLM agents using browser tools need a structured representation of the page for interaction — not HTML (too verbose) or screenshots (requires vision). The accessibility tree provides a compact, semantic view of interactive elements with stable references.

**Who needs this:** AI agent builders, MCP server developers, accessibility auditors. Playwright MCP uses the accessibility tree as its primary interaction surface.

**Why it matters:** An accessibility snapshot is 10-50x smaller than raw HTML while preserving all interactive structure. It's how Playwright MCP achieves reliable browser automation without vision models. However, CF Browser Rendering may not expose the accessibility tree API — needs investigation.

**Prior art:** Playwright MCP (`browser_snapshot`), Shot-scraper (`accessibility` subcommand).

---

## Tier 2 — Needs Investigation

### 7. `--query "topic"` — Relevance filter

**Status:** Evaluating | **Score:** 2.3 (Impact: 2, Effort: 2, Feasibility: 3)

**The problem:** Pages contain lots of irrelevant content — sidebars, related articles, comments, navigation text. Even with `--only-main-content`, the output may contain sections unrelated to what the user actually wants. There's no way to say "I only care about the pricing information on this page."

**Who needs this:** RAG pipeline builders, research agents, anyone extracting specific information from long pages. Crawl4AI calls this "fit markdown" — content filtered by relevance to a query.

**Why it matters:** Dramatically reduces token count for LLM processing. A 50K char page might have only 2K chars relevant to your query. BM25 scoring is fast, well-understood, and runs locally — no API calls needed.

**Prior art:** Crawl4AI (`BM25ContentFilter`, `fit_markdown`). Pure Python post-processing.

---

### 8. Auth state save/load — `--session cookies.json`

**Status:** Evaluating | **Score:** 2.4 (Impact: 3, Effort: 2, Feasibility: 2)

**The problem:** `--auth` only handles HTTP Basic Auth. Many sites use cookie-based sessions (login forms, OAuth redirects, SSO). Users can't scrape authenticated content unless they manually extract cookies and pass them via `--headers "Cookie: ..."` — fragile and tedious.

**Who needs this:** Anyone scraping behind a login — intranets, dashboards, member-only content, admin panels. This is one of the most-requested features in every scraping tool.

**Why it matters:** A `flarecrawl auth save` flow that opens a browser, lets you log in manually, then saves the session cookies to a JSON file would make authenticated scraping trivial. Subsequent runs use `--session cookies.json` to replay the auth state. CF API supports the `cookies` field.

**Prior art:** Shot-scraper (`auth` subcommand), Playwright MCP (`browser_storage_state`).

---

### 9. `--precision` / `--recall` toggle

**Status:** Evaluating | **Score:** 2.3 (Impact: 2, Effort: 2, Feasibility: 3)

**The problem:** `--only-main-content` has one fixed behavior. Some users want aggressive filtering (less noise, risk losing content) while others want conservative filtering (more content, risk including nav/ads). There's no way to tune the trade-off.

**Who needs this:** Data scientists building training datasets (want recall), LLM applications (want precision), content migrators (want everything), researchers (want clean text only).

**Why it matters:** Different use cases have fundamentally different quality requirements. A single toggle that shifts the extraction aggressiveness would serve all of them. Trafilatura proves this is a popular feature.

**Prior art:** Trafilatura (`--precision`, `--recall`). Adjusts internal scoring thresholds.

---

### 10. `--deduplicate` on crawl

**Status:** Evaluating | **Score:** 2.3 (Impact: 2, Effort: 2, Feasibility: 3)

**The problem:** Crawled sites often have duplicate content — print-friendly versions, paginated pages with repeated boilerplate, HTTP/HTTPS duplicates, trailing-slash variants. Users get charged browser time for duplicate pages and have to deduplicate downstream.

**Who needs this:** Anyone doing large crawls for content indexing, RAG pipelines, or site archival. Duplicates waste browser time (cost) and pollute embeddings.

**Why it matters:** Content-hash dedup at the crawl level saves money and produces cleaner datasets. A simple SimHash or content fingerprint comparison can catch near-duplicates, not just exact matches.

**Prior art:** Trafilatura (`--deduplicate`). Scrapy has built-in dedup middleware.

---

### 11. YAML batch config — `flarecrawl batch config.yml`

**Status:** Evaluating | **Score:** 2.0 (Impact: 2, Effort: 1, Feasibility: 3)

**The problem:** Complex scraping jobs require many flags — different URLs need different formats, selectors, auth, mobile settings. Users resort to shell scripts wrapping multiple flarecrawl commands, losing error handling and parallelism.

**Who needs this:** Teams running recurring scrape jobs, CI/CD pipelines, monitoring setups. "Scrape these 5 URLs with these specific settings each" as a single config file.

**Why it matters:** Declarative configuration is easier to version control, review, and share than imperative shell scripts. Also enables parallel execution across different URL+config combos.

**Prior art:** Shot-scraper (`multi` subcommand with YAML). Crawl4AI uses YAML config files for browser/crawler/extraction settings.

---

### 12. `--scroll` — Auto-scroll before capture

**Status:** Evaluating | **Score:** 2.0 (Impact: 2, Effort: 2, Feasibility: 2)

**The problem:** Infinite-scroll pages (social feeds, product listings, search results) only load content as the user scrolls. The initial page load captures just the first "fold" of content. There's no way to trigger lazy loading without manual browser interaction.

**Who needs this:** Anyone scraping social media feeds, e-commerce product pages, news aggregators, image galleries — any page that lazy-loads content on scroll.

**Why it matters:** Without scrolling, you might get 20 items from a page that has 200. CF API doesn't have a native scroll command, but it could potentially be triggered via a JS injection pattern using `--body`.

**Prior art:** Crawl4AI (`scan_full_page: true` with `scroll_delay`). Playwright has `page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))`.

---

### 13. HAR capture — `--har output.har`

**Status:** Built (v0.6.0, lightweight) | **Score:** 1.4 (Impact: 2, Effort: 1, Feasibility: 1)

**The problem:** When debugging scraping failures — blocked requests, missing resources, redirect chains — users have no visibility into what the browser actually did. They see the final content but not the network journey.

**Who needs this:** Developers debugging anti-bot blocks, performance auditors, security researchers analyzing request flows.

**Why it matters:** HAR files are the standard for network debugging. However, CF Browser Rendering likely doesn't expose the Chrome DevTools Protocol needed to capture network events — making this infeasible without significant workarounds.

**Prior art:** Shot-scraper (`har` subcommand), Playwright (`recordHar`).

---

## Tier 3 — Nice to Have

### 14. Internet Archive fallback — `--archived`

**Status:** Built (v0.7.0) | **Score:** 1.9 (Impact: 1, Effort: 2, Feasibility: 3)

**The problem:** URLs go stale — pages get deleted, moved, or paywalled. When a scrape returns 404 or empty content, users have to manually check the Wayback Machine.

**Who needs this:** Researchers, journalists, anyone working with historical content or broken links.

**Prior art:** Trafilatura (`--archived`). Wayback Machine API is free and well-documented.

---

### 15. Language targeting — `--language de`

**Status:** Built (v0.7.0) | **Score:** 1.9 (Impact: 1, Effort: 2, Feasibility: 3)

**The problem:** Multilingual sites serve different content per language. Crawls pick up all languages indiscriminately, wasting browser time on irrelevant translations.

**Who needs this:** Localization teams, multilingual content pipelines, researchers building language-specific corpora.

**Prior art:** Trafilatura (`--target-language`). Language detection via HTTP headers or content analysis.

---

### 16. `--backup-dir` — Save raw HTML alongside output

**Status:** Built (v0.6.1) | **Score:** 2.2 (Impact: 1, Effort: 3, Feasibility: 3)

**The problem:** Extraction is lossy — once HTML is converted to markdown, the original structure is gone. If the extraction algorithm improves or users need different output later, they have to re-scrape everything.

**Who needs this:** Content archivists, data pipeline builders who want to re-process later, anyone who needs audit trails.

**Prior art:** Trafilatura (`--backup-dir`). Trivial to implement alongside download command.

---

### 17. VLM image captioning

**Status:** Evaluating | **Score:** 1.4 (Impact: 2, Effort: 1, Feasibility: 1)

**The problem:** Images without alt text are invisible to LLMs — they see `![](image.jpg)` with no description. Auto-captioning would make visual content accessible in text-only pipelines.

**Who needs this:** RAG pipeline builders, accessibility auditors, anyone feeding scraped content to LLMs.

**Prior art:** Jina Reader (`X-With-Generated-Alt`). Would need Workers AI vision model — feasibility unclear.

---

### 18. Cookie banner / overlay removal — `--magic`

**Status:** Built (v0.7.0) | **Score:** 1.7 (Impact: 2, Effort: 1, Feasibility: 2)

**The problem:** GDPR cookie banners, newsletter popups, and modal overlays cover page content and pollute extracted text. Users see "Accept all cookies" in their markdown output.

**Who needs this:** Anyone scraping European sites, news sites with popups, any site with modal overlays.

**Prior art:** Crawl4AI (`magic: true`, `remove_overlay_elements`). Could use CSS injection via `addStyleTag` to hide common overlay patterns.

---

## Decision Log

| Date | # | Feature | Decision | Rationale |
|------|---|---------|----------|-----------|
| 2026-03-19 | 1 | `--js-eval` | **Built (v0.6.0)** | Uses addScriptTag + DOM injection workaround since CF has no page.evaluate() |
| 2026-03-19 | 2 | `--selector` | **Built (v0.6.0)** | Maps directly to CF /scrape endpoint elements field |
| 2026-03-19 | 3 | `--wait-for-selector` | **Built (v0.6.0)** | Was already plumbed in client, just needed CLI flag |
| 2026-03-19 | 4 | `--stdin` | **Built (v0.6.0)** | Local processing via extract.py, zero API calls |
| 2026-03-19 | 5 | `flarecrawl discover` | **Built (v0.6.0)** | Combines sitemap + RSS/Atom + page links discovery |
| 2026-03-19 | 13 | `--har` | **Built (v0.6.0)** | Lightweight version — request metadata only (CF doesn't expose full HAR) |
| 2026-03-19 | 16 | `--backup-dir` | **Built (v0.6.1)** | Saves raw HTML alongside extracted output for archival/reprocessing |
| 2026-03-20 | 14 | `--archived` | **Built (v0.7.0)** | Wayback Machine fallback on 404/empty content |
| 2026-03-20 | 15 | `--language` | **Built (v0.7.0)** | Sets Accept-Language header for multilingual sites |
| 2026-03-20 | 18 | `--magic` | **Built (v0.7.0)** | CSS injection hides cookie banners, GDPR modals, popups |

---

## How to Use This Document

1. **Evaluate** — Read the problem/use case, discuss whether it applies to our users
2. **Decide** — Move to Approved / Deferred / Discarded with rationale in Decision Log
3. **Build** — Approved items move to implementation (task list)
4. **Review** — Re-evaluate Deferred items each release cycle

Inspired by: Jina Reader, Crawl4AI, Trafilatura, Shot-scraper, Playwright MCP.
