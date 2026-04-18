# Frontier Architecture Comparison

Written 2026-04-18 to inform flarecrawl's frontier redesign. We write all code
from spec; this document captures patterns and features we may adopt, with
citations to canonical docs or source files. No code is copied from any
surveyed project; paraphrased behaviour only.

Scope: frontier, scheduler, dedup, politeness, and resume semantics. Workload
context: flarecrawl drives Cloudflare Browser Run across ~54k domains with
weekly refresh cadence and aims for millions of URLs per job.

## TL;DR

The current `src/flarecrawl/frontier.py` is a single-queue priority scheduler
with raw-URL dedup. Against the frontier ecosystem, five patterns stand out as
must-adopt for the OTDB workload:

| Pattern | Source | Why it matters for us |
|---------|--------|-----------------------|
| Canonicalise URL before dedup (sort query args, drop fragment, strip blank values) | Scrapy via `w3lib.url.canonicalize_url` | Today `?utm_source=a` vs `?utm_source=b` are distinct rows — wastes budget on 54k-domain refresh |
| Per-host FIFO + round-robin over queues | Heritrix `BdbFrontier` | One global queue lets a fast domain starve 53,999 others; round-robin fairness is the point of a multi-tenant crawl |
| Crash-rollback of `in_flight` on startup | Inherent to Heritrix BDB + Nutch CrawlDB generate-segment model | Our explicit known gap — one SQL statement closes it |
| Per-URL retry budget with dead-letter terminal state | Scrapy `RetryMiddleware` / Crawlee `failedRequestHandler` | Our retries are only at HTTP layer; frontier has no memory across job restarts |
| Snooze vs sick distinction (transient delay vs circuit break) | Heritrix `DispositionProcessor` + `sick_until` idiom | Current breaker is one-shot — no way to express "back off 60s then try again" vs "this host is dead" |

## Feature matrix

| Feature | Flarecrawl | Scrapy | Heritrix 3 | Colly | Crawlee | Nutch | Frontera |
|---------|-----------|--------|------------|-------|---------|-------|----------|
| URL canonicalisation before dedup | No | Yes (`w3lib.canonicalize_url`) | Yes (SURT) | No (raw URL hash) | Partial (configurable `uniqueKey`) | Yes | Yes (canonical URL solver middleware) |
| Per-host queues | No (one global queue) | Approximated via `CONCURRENT_REQUESTS_PER_DOMAIN` | Yes, native (BdbFrontier per-queue) | Per-domain LimitRule | Per-session concurrency, not native per-host FIFO | Yes (queues generated per host) | Yes (partitioned by host) |
| Dedup key is hash, not raw URL string | No | Yes (fingerprint) | Yes | No (URL string) | No (uniqueKey string, URL-derived by default) | Yes (fingerprint) | Yes (`URL_FINGERPRINT_FUNCTION`) |
| Fingerprint includes method + body | N/A | Yes (request fingerprint) | N/A (typically GET) | HasPosted variant | uniqueKey can be overridden | N/A | Configurable |
| Retry budget per URL | No (terminal `mark_failed`) | Yes (`RETRY_TIMES` default 2) | Yes (`maxRetries` default) | Via callbacks | Yes (`maxRequestRetries` default 3) | Yes (generate cycle) | Yes (backend-defined) |
| Dead-letter terminal handler | No | Implicit (errback) | Yes (discard after `maxRetries`) | Error callback | Yes (`failedRequestHandler`) | Implicit (status in CrawlDB) | Yes |
| Per-host byte/URL/time budget | No (global `--limit` only) | Partial (CLOSESPIDER_*) | Yes (per-host bandwidth, `maxPerHostBandwidthUsageKbSec`) | No | `maxRequestsPerCrawl` global only | Yes (per-generate cap) | Yes (DomainMetadata counters) |
| Crash rollback (in_flight→pending) | No (known gap) | N/A (in-memory scheduler) | Yes (BDB journaled) | N/A (memory default) | Yes (RequestQueue persisted) | Yes (CrawlDB is source of truth) | Yes (DB worker state) |
| Snooze (transient delay) vs sick (extended break) | No (single `sick_until`) | Via AUTOTHROTTLE latency | Yes (`delayFactor` + per-queue snooze) | Delay field | Session `markBad` vs `retire` | Yes (per-host schedule) | Yes (domain metadata flags) |
| Adaptive throttling from response latency | No | Yes (AUTOTHROTTLE) | Yes (`delayFactor`) | Fixed delay | Auto-scaled pool (CPU/mem, not latency) | No (fixed) | Depends on strategy |
| Pluggable scheduling strategy | No | Partial (DEPTH_PRIORITY) | Partial (precedence providers) | No | Partial | Yes (ScoringFilter, OPIC default) | Yes (strategy workers) |
| Persistent storage | SQLite (WAL) | Disk queue optional | Berkeley DB | Pluggable (mem / Redis / SQLite / BoltDB) | FS / Memory / SQLite | HDFS / local FS | Memory / SQLAlchemy / HBase / Redis |
| Distributed | No | No | No | No (supports Redis for coordination) | No | Yes (Hadoop) | Yes |
| Session/proxy pool for ban avoidance | No | Via middleware | Via credential store | Cookie jar | Yes (`SessionPool`) | No | No |
| Round-robin over host queues | No | Implicit (reactor) | Yes (queue rotation) | No | No | Yes (generate step) | Partition-aware |

## Tool-by-tool analysis

### Scrapy

- **What it is**: Python single-machine crawling framework, BSD licence, first released 2008, still actively maintained. The reference implementation of the "fetch + parse + pipeline" pattern.
- **Frontier model**: In-memory priority queue by default, disk-backed via `SCHEDULER_*` settings. Requests hold a `priority` int (default 0); scheduler returns higher-priority first. Single-process — no native per-host queue structure; concurrency per host is approximated by `CONCURRENT_REQUESTS_PER_DOMAIN` (default 8 in the base config, 1 in the `scrapy startproject` template) acting as a semaphore over the downloader pool.
- **Dedup**: `RFPDupeFilter` uses a request fingerprint, *not* the raw URL string. Fingerprint inputs include method, URL, and body by default. URLs are first canonicalised through `w3lib.url.canonicalize_url`, which: makes the URL safe, sorts query arguments by key then value, normalises percent-encoding case (`%2f→%2F`), normalises `+` spaces in query args, removes empty-value query args (unless `keep_blank_values=True`), and strips the fragment unless `keep_fragments=True` ([w3lib docs](https://w3lib.readthedocs.io/en/latest/w3lib.html#w3lib.url.canonicalize_url)).
- **Politeness**: `DOWNLOAD_DELAY` (fixed per-domain delay) + `CONCURRENT_REQUESTS_PER_DOMAIN`. `AUTOTHROTTLE` extension adapts delay from observed response latencies toward a target parallelism.
- **Retries and dead-letter**: `RetryMiddleware` — `RETRY_TIMES=2` (additional attempts after first), `RETRY_HTTP_CODES=[500, 502, 503, 504, 522, 524, 408, 429]`, explicit `RETRY_EXCEPTIONS` list. Failed requests are "collected on the scraping process and rescheduled at the end, once the spider has finished crawling all regular (non failed) pages" ([Scrapy docs, RetryMiddleware section](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#module-scrapy.downloadermiddlewares.retry)). Max-retries exhaustion → request dropped; `errback` fires.
- **Crash recovery**: In-memory by default, so kill -9 loses state. Durable runs require `JOBDIR` which persists the scheduler queue and dupefilter to disk.
- **Relevant sources**:
  - `docs/topics/downloader-middleware.html#retrymiddleware-settings` — canonical retry defaults
  - `docs/topics/request-response.html` — priority, dont_filter, meta
  - `w3lib/url.py` `canonicalize_url` — canonicalisation rules
  - `docs/topics/settings.html#concurrent-requests-per-domain` — politeness knobs
- **What flarecrawl should steal**: (1) Canonicalise before dedup using the same six-step rule set; (2) Fingerprint = `sha256(method || canonical_url || body_hash)` stored in a BLOB column, not raw URL as PK; (3) Retry budget (meta-key `max_retry_times`) with a canonical retry-on code list ([500, 502, 503, 504, 408, 429, 522, 524]).

### Heritrix 3

- **What it is**: Internet Archive's production web-archive crawler (Java, Apache 2.0), origin of the WARC format. Currently in use for the IA's global archive; deep operational experience baked into the frontier.
- **Frontier model**: `BdbFrontier` — the only frontier implementation in H3. Per-host URI queues stored in Berkeley DB. Queue rotation ensures fairness: "all queues are visited even when there are far more queues than available threads." The frontier controls *when* each URI is due, not just order.
- **Dedup**: `UriUniqFilter` prevents duplicate crawling within a single job. SURT (Sort-friendly URI Reordering Transform) is used for canonicalisation and also as the prefix syntax for per-domain overrides via Sheets.
- **Politeness**: Per-queue crawl-delay enforced through `DispositionProcessor` with three settings that together shape inter-request wait time: `delayFactor` (multiplier over last fetch duration, default 5.0), `maxDelayMs` (default 30000), `minDelayMs` (default 3000) ([Heritrix docs, Politeness section](https://heritrix.readthedocs.io/en/latest/configuring-jobs.html)). Also `maxPerHostBandwidthUsageKbSec` as a byte-rate cap per host. Parallelism within a single queue is set by `queueAssignmentPolicy.parallelQueues` (default 1).
- **Retries and dead-letter**: Frontier-level, not downloader-level. `maxRetries` (example shows 30 in docs, tuneable), `retryDelaySeconds` (docs example 900s) wait period between retries. URIs failing past the limit move to a failed/discarded state.
- **Crash recovery**: Berkeley DB is journaled; checkpointing is a first-class concept. Crashed crawls resume by re-opening the BDB environment. This is architectural, not optional.
- **Relevant sources**:
  - `heritrix.readthedocs.io/en/latest/configuring-jobs.html` — Disposition bean, Retry Policy, Bandwidth Limits sections
  - `github.com/internetarchive/heritrix3/wiki/Frontier` — BdbFrontier overview
  - `org.archive.crawler.frontier.BdbFrontier` — frontier class
- **What flarecrawl should steal**: (1) Per-host FIFO queue abstraction (even if persisted as a compound index `(hostname, added_at)`); (2) Round-robin scheduler over the set of non-snoozed queues; (3) Distinct politeness knobs (`delayFactor`, `minDelayMs`, `maxDelayMs`) so we can back off proportionally to observed latency; (4) Per-host Sheet-style config overlay for known-fast or known-fragile domains.

### Colly

- **What it is**: Go crawling library by Adam Tauber, Apache 2.0, pragmatic and fast. Popular for short-run structured-data scraping.
- **Frontier model**: Not an explicit frontier object — the `Collector` walks links as they're discovered. Visited-URL tracking via `Storage` interface. `HasVisited(url)` and `HasPosted(url, data)` probes guard requests. `AlreadyVisitedError` is returned when the dedup hits.
- **Dedup**: Uses the `Storage` implementation to hold visited URLs. Backends shipped: in-memory (default), Redis, SQLite, BoltDB. Dedup is raw-URL (with method+post-data variant for POST). No documented canonicalisation pass.
- **Politeness**: `LimitRule` struct with fields: `DomainGlob` / `DomainRegexp` (domain match), `Delay` (per-domain wait), `RandomDelay` (jittered extra), `Parallelism` (concurrent request cap per match). Setting `Delay` effectively forces sequential fetches to that domain.
- **Retries and dead-letter**: No built-in retry middleware — users implement via `OnError` callback, typically calling `Visit()` again with a counter in context. No dead-letter queue.
- **Crash recovery**: Depends on storage backend. In-memory default loses state. SQLite / Redis / BoltDB backends persist visited set but not an in-flight queue; resume behaviour is "don't revisit what we already finished."
- **Relevant sources**:
  - `pkg.go.dev/github.com/gocolly/colly/v2` — Collector API, Storage interface, LimitRule
  - `go-colly.org/docs/examples/rate_limit/` — LimitRule usage
  - `github.com/gocolly/colly/storage` — backend implementations
- **What flarecrawl should steal**: (1) Pluggable storage interface as a design pattern (SQLite for dev/prod, memory for tests); (2) `LimitRule` per-domain-glob wildcarding for surgical policy — e.g. bump `.gov.uk` to 2s delay without touching per-host logic.

### Crawlee

- **What it is**: TypeScript/JS crawling library by Apify, Apache 2.0, modern (first release 2019, name change from `apify-sdk` 2022). Production-used for web scraping at Apify scale.
- **Frontier model**: `RequestQueue` — a persisted queue of `Request` objects. Deep-crawl oriented, supports BFS and DFS traversal. Backed by memory / filesystem / SQLite depending on storage config.
- **Dedup**: Each Request has a `uniqueKey`; by default derived from the URL. "The queue can only contain unique URLs. More precisely, it can only contain Request instances with distinct `uniqueKey` properties" ([Crawlee RequestQueue docs](https://crawlee.dev/js/api/core/class/RequestQueue)). Callers can override — e.g. include POST body or strip tracking params in their own key derivation. Crawlee does not ship a canonicalisation function; canonicalisation is the user's responsibility.
- **Politeness**: `AutoscaledPool` scales concurrency between `minConcurrency`/`maxConcurrency` based on CPU/memory load, not latency. `maxRequestsPerMinute` acts as global rate cap. Per-host politeness is not a first-class frontier concept — it's handled by `SessionPool` assigning proxy sessions.
- **Retries and dead-letter**: `maxRequestRetries` defaults to 3 ([BasicCrawlerOptions docs](https://crawlee.dev/js/api/basic-crawler/interface/BasicCrawlerOptions#maxRequestRetries)). `errorHandler` fires before each retry (for request mutation); `failedRequestHandler` fires after retries exhausted — explicit dead-letter callback. `maxSessionRotations` is a separate retry budget for "bad session" retries, not counted against `maxRequestRetries`.
- **Crash recovery**: RequestQueue is persistent (default to filesystem under `./storage/`). `fetchNextRequest` → `markRequestHandled` / `reclaimRequest` model; reclaim returns a failed request for retry. On restart the queue is re-opened and unhandled items are served again. No explicit documentation of in-flight rollback, but the handled/unhandled binary state achieves the same effect.
- **Relevant sources**:
  - `crawlee.dev/js/api/core/class/RequestQueue` — queue API (addRequest, fetchNextRequest, markRequestHandled, reclaimRequest)
  - `crawlee.dev/js/api/basic-crawler/interface/BasicCrawlerOptions` — maxRequestRetries, errorHandler, failedRequestHandler, maxSessionRotations
  - `crawlee.dev/js/docs/guides/session-management` — SessionPool with markBad/markGood/retire
- **What flarecrawl should steal**: (1) Two-tier retry split — URL-level retries vs transport-level retries counted separately (our HTTP-layer retries should not consume the frontier's per-URL budget); (2) Explicit `failedRequestHandler` / dead-letter table as first-class state, not implicit; (3) `SessionPool` idea of `markBad` (temporary) vs `retire` (permanent) maps directly onto our snooze/sick distinction.

### Apache Nutch

- **What it is**: Apache 2.0, the original Hadoop-era web crawler (2002), batch-oriented. Not a fit for our Python single-machine model, but its data model is instructive.
- **Frontier model**: CrawlDB — a Hadoop sequence file / HBase table keyed by URL, holding per-URL metadata: fetch status, fetch time, score, signature. The crawl is a loop over four MapReduce jobs: **generate** (emit a fetch list from CrawlDB by score/due-time), **fetch** (download), **parse** (extract links and content), **updatedb** (merge results back into CrawlDB, including newly-discovered links). Batch cadence, not continuous.
- **Dedup**: URL is the CrawlDB key; signatures detect content duplicates across URLs. URL fingerprints are used in updatedb to fold in new links.
- **Politeness**: `fetcher.server.delay` (inter-request delay), `fetcher.threads.per.queue` (parallelism within a host queue), plus `fetcher.max.crawl.delay` upper bound. Fetch lists are partitioned by host so that a single fetcher task owns a host per round.
- **Retries and dead-letter**: Each URL has a fetch-status in CrawlDB. Failed fetches are rescheduled by the scheduler's next-fetch-time rule. `db.fetch.retry.max` caps retries; beyond that, the URL transitions to `db_gone` or `db_unfetched` terminal status.
- **Scoring**: `ScoringFilter` plugin interface; default is OPIC (Online Page Importance Computation — Abiteboul, Preda, Cobena 2003) which distributes cash to outlinks as pages are fetched. OPIC is a way to prioritise the frontier without needing full link-graph convergence.
- **Crash recovery**: Trivial — CrawlDB *is* the source of truth. A failed job is rerun from the last committed segment.
- **Relevant sources**:
  - `cwiki.apache.org/confluence/display/NUTCH/NutchTutorial` — crawl cycle
  - `cwiki.apache.org/confluence/display/NUTCH/FAQ` — ScoringFilter / OPIC
  - Nutch source `org.apache.nutch.crawl.Generator` — generate step
- **What flarecrawl should steal**: (1) The generate–fetch–update cycle as a *mental model*: the frontier produces a bounded "batch" that gets drained before replanning. This maps onto our weekly-refresh job cadence. (2) A `next_fetch_time` column on the visited table so failed URLs can be retried with backoff across job runs. (3) OPIC-style score column (float, per URL) as a cheap pluggable priority signal — we don't need the full MapReduce to benefit.

### Frontera

- **What it is**: Scrapinghub's (now Zyte's) distributed frontier for Scrapy, BSD licence. Last significant release 2017-ish — largely unmaintained, but the architecture is still the canonical reference for "frontier as a service."
- **Frontier model**: Clean separation of concerns — the `FrontierManager` talks to a `Backend` through four interfaces: `Queue` (priority queue of scheduled requests), `Metadata` (content + request/response record), `States` (link state: NOT_CRAWLED, QUEUED, CRAWLED, ERROR), `DomainMetadata` (per-host counters/flags). This is the cleanest split I found in any surveyed tool.
- **Distributed shape**: Two worker pools. **Strategy workers** run crawling logic (scoring, stop conditions, URL ordering), read from the spider-log stream, write to the scoring-log. **DB workers** store metadata and generate batches for fetchers. "Each host is downloaded by no more than one spider process. This is achieved by stream partitioning" ([Frontera architecture docs](https://frontera.readthedocs.io/en/latest/topics/architecture.html)).
- **Dedup**: URL fingerprint via pluggable `URL_FINGERPRINT_FUNCTION`. `hostname_local_fingerprint` is recommended for HBase to keep a host's URLs in one block.
- **Scoring / strategy**: Pluggable crawling strategies — "Crawling strategy can be changed without having to stop the crawl." FIFO/LIFO/BFS/DFS/OPIC strategies supported by built-in backends.
- **Backends**: Memory (heapq, test only), SQLAlchemy (SQLite/Postgres), HBase (production large-scale), Redis (medium-scale). The SQLAlchemy backend is the closest analogue to our SQLite approach.
- **Retries and dead-letter**: Backend-defined; `request_error(page, error)` is the callback. Frontera itself doesn't dictate retry policy — the strategy layer does.
- **Crash recovery**: Backend-dependent. SQLAlchemy and HBase are durable; memory is not.
- **Relevant sources**:
  - `frontera.readthedocs.io/en/latest/topics/architecture.html` — two-worker model
  - `frontera.readthedocs.io/en/latest/topics/frontier-backends.html` — Backend/Queue/Metadata/States/DomainMetadata interface definitions
- **What flarecrawl should steal**: (1) The four-interface split (`Queue`, `Metadata`, `States`, `DomainMetadata`) is a better internal model than our current `frontier` / `visited` / `domain_stats` / `meta` tables because it names the *roles*, not the storage. Even in SQLite we should keep these as logical boundaries. (2) `DomainMetadata` as a keyed store of per-host counters and flags, rather than our fixed-schema `domain_stats` — lets us add per-host budgets without migrations. (3) The strategy/scheduler separation — whatever decides "what's next" should be swappable.

## Cross-cutting findings

### Patterns we should adopt (ranked by impact on OTDB workload)

1. **Canonicalisation before dedup** (S) — inspired by Scrapy/w3lib. Without this, a weekly refresh will re-fetch arbitrary tracking-param permutations. One function, one column migration (`canonical_url` or replace the PK with a fingerprint BLOB). Biggest per-dollar win.
2. **Crash rollback on startup** (S) — inspired by Heritrix BDB model. `UPDATE frontier SET status='pending' WHERE status='in_flight' RETURNING COUNT(*)`, log the count, done. Closes our known gap in one line.
3. **Per-host FIFO + round-robin scheduler** (M) — inspired by Heritrix BdbFrontier. Index frontier on `(hostname, status, added_at)` and rewrite `next_batch` to pick ≤1 URL per non-snoozed host per round. Directly addresses the 54k-domain fairness problem.
4. **Per-URL retry budget with dead-letter state** (M) — inspired by Scrapy `RetryMiddleware` + Crawlee `failedRequestHandler`. Add `attempts` column, `next_retry_at` column, status `dead` terminal state. Separate frontier retries from HTTP-layer retries — they count different things.
5. **Snooze vs sick distinction** (S) — inspired by Heritrix `DispositionProcessor` + Crawlee `markBad`/`retire`. Split `sick_until` into `snooze_until` (short, from response-driven backoff) and `sick_until` (long, from consecutive-fails breaker). Two columns, clearer semantics.
6. **Per-host budgets** (M) — inspired by Heritrix `maxPerHostBandwidthUsageKbSec` + Frontera `DomainMetadata`. A flexible `domain_budget` table keyed by host with optional `max_urls`, `max_bytes`, `max_seconds`. Weekly-refresh mode should default to `max_urls = 10000` per host to bound pathological cases.
7. **Adaptive delay from latency** (M) — inspired by Scrapy AUTOTHROTTLE + Heritrix `delayFactor`. Multiply observed response time by a factor (default 5.0) to set next-fetch delay for that host, clamped to [minDelayMs, maxDelayMs]. Replaces our current fixed `aiolimiter` rate with a responsive one.
8. **Role-separated data model** (M) — inspired by Frontera's Queue/Metadata/States/DomainMetadata split. Doesn't change SQL tables, but gives us cleaner Python abstractions — a `FrontierQueue` class, a `VisitedStore` class, etc. Makes future swapping (SQLite → Postgres → Redis) cheap.
9. **Next-fetch-time scheduling** (M) — inspired by Nutch CrawlDB. Unifies retry-backoff and refresh-cadence behind a single `due_at` column. Weekly-refresh = `due_at = last_fetched + 7d`; retry = `due_at = now + exp_backoff`. One mechanism, two use cases.

### Patterns we should NOT adopt

- **Nutch's Hadoop/MapReduce batch model**. Overkill for a single-machine CLI driving a managed browser service. Keep the *mental* generate-fetch-update cycle, drop the infrastructure.
- **Frontera's HBase or message-bus worker split**. We are not distributed. The role abstractions are useful; the distributed machinery is not.
- **Heritrix's full Spring/BDB configuration model**. Sheets and bean wiring are overkill; a simple per-host config table suffices.
- **Crawlee's `AutoscaledPool` scaling on CPU/memory**. Our bottleneck is Cloudflare Browser Run concurrency quota, not local CPU.
- **Scrapy's Twisted reactor coupling**. Our async model is asyncio-native; don't import a paradigm.
- **Distributed crawling in general** (Nutch, Frontera). Out of scope for the 54k-domain single-job use case.

### Open questions

- **Crawlee's RequestQueue in-flight rollback semantics** — documentation describes `reclaimRequest` but I could not find a definitive statement on what happens to requests that were mid-flight when the process dies. The binary handled/unhandled model suggests they come back automatically, but I did not verify against source.
- **Exact Scrapy request-fingerprint inputs by default** — docs confirm method/URL/body are involved; the role of headers in the default fingerprinter (`ScrapyRequestFingerprinter`) I did not fully pin down in two fetches. Safe default: assume method+canonical_url+body, exclude headers.
- **Nutch OPIC concrete formula** — the FAQ confirms it's the default `ScoringFilter`, but the canonical cash-distribution formula is in the 2003 paper (Abiteboul/Preda/Cobena) not the Nutch docs. For us, "pluggable score column" is the takeaway; we do not need the algorithm yet.
- **Colly's redis/sqlite storage in-flight semantics** — Colly has no explicit in-flight state (it walks links synchronously within a visit call), so the question may not apply. Did not verify.

## Recommended spec for flarecrawl frontier v2

Acceptance criteria for the next implementation session. Each bullet is implementation-ready.

**Canonicalisation** (`flarecrawl.frontier.canon`):
- Pure function `canonicalize(url: str) -> str`
- Steps in order: (1) make URL safe per RFC 3986 / WHATWG URL (2) case-fold scheme and host (3) remove default ports (`:80` for http, `:443` for https) (4) sort query args by key then value (5) drop query args in a configurable tracking-param deny-list (default: `utm_*`, `gclid`, `fbclid`, `mc_eid`, `_ga`, `ref`, `source` — extend as encountered) (6) drop empty-value query args (7) strip fragment (8) normalise percent-encoding to uppercase hex.
- Acceptance: `canonicalize("http://Example.COM:80/a?b=2&utm_source=x&a=1#top") == "http://example.com/a?a=1&b=2"`

**Dedup**:
- Fingerprint column `fp BLOB` = `blake2b(method || '\x00' || canonical_url || '\x00' || body_hash, digest_size=16)`.
- `fp` is the primary key of the frontier table; raw URL moves to a data column for logging/export.
- rbloom fast-path keyed by `fp`, not raw URL.

**Per-host queue**:
- Index: `CREATE INDEX frontier_host ON frontier(hostname, status, added_at)`.
- Scheduler `next_batch(n)` selects at most one URL per hostname where `snooze_until < now AND sick_until < now AND status='pending'`, ordered by `(priority DESC, added_at ASC)` within host, round-robin across hosts.
- Atomic flip to `in_flight` with `UPDATE ... WHERE fp IN (...) AND status='pending'` guarded.

**Retry budget**:
- `attempts INT NOT NULL DEFAULT 0`, `max_attempts INT NOT NULL DEFAULT 3` per URL.
- `next_retry_at REAL` timestamp; scheduler filters `next_retry_at IS NULL OR next_retry_at <= now`.
- Exponential backoff: `next_retry_at = now + min(600, 2 ** attempts)`.
- On `attempts >= max_attempts`, status transitions to `dead` (terminal). Separate table `dead_letter` is a view over `frontier WHERE status='dead'` — exportable for audit.
- Retry-eligible HTTP codes: `{408, 429, 500, 502, 503, 504, 522, 524}` (Scrapy defaults plus Cloudflare 522/524). Codes outside this set are immediate terminal fail.

**Budget caps** (`domain_budget` table, schema `hostname PK, max_urls INT NULL, max_bytes BIGINT NULL, max_seconds INT NULL`):
- Insert/update per host; NULL = no cap.
- Scheduler excludes hosts where `urls_fetched >= max_urls` (tracked in `domain_stats`).
- Default row inserted for weekly-refresh job: `max_urls=10000`.

**Resume / crash recovery**:
- `Frontier.open(resume=True)` first executes `UPDATE frontier SET status='pending' WHERE status='in_flight'` and logs the rolled-back count as a WARNING.
- No other startup action required — WAL checkpoint guarantees durability of committed writes.

**Snooze vs sick** (replace current `sick_until`):
- `snooze_until REAL` — short, set on 429/503 from response `Retry-After` header or from response-latency-times-delayFactor. Expires on its own. Max value: 120s.
- `sick_until REAL` — long, set when `consecutive_fails >= 10`. Default duration 600s. Reset to 0 on any successful fetch.
- Both participate in scheduler exclusion.

**Adaptive delay** (new `ratelimit.py` mode, opt-in via config):
- Per host: track EWMA of last 10 response times.
- Next-fetch delay = `clamp(ewma * delayFactor, minDelayMs, maxDelayMs)` with defaults `delayFactor=2.0`, `minDelayMs=200`, `maxDelayMs=10000`.
- Sets `snooze_until = now + delay_seconds` on every completed fetch.

**Next-fetch-time for refresh**:
- `visited.next_refresh_at REAL` — set to `fetched_at + refresh_interval` (default 7 days).
- Refresh-mode job selects `visited` rows past `next_refresh_at` and re-inserts into `frontier`.

**Role separation** (internal only, no schema change):
- `FrontierQueue` class owns `frontier` table.
- `VisitedStore` class owns `visited` table.
- `DomainRegistry` class owns `domain_stats` + `domain_budget`.
- `DeadLetter` class is a view over `FrontierQueue`.
- All take a shared `aiosqlite` connection.

**Fingerprint stability**:
- The canonicalisation + fingerprint function is part of the public SQL schema. Schema version bump required if either changes. Document both in a `frontier_schema_version` row in `meta`.

Implementer note: everything above is single-machine SQLite + WAL. No new dependencies beyond what's already imported (`hashlib` for blake2b is stdlib). The rbloom fast-path remains, keyed on `fp` instead of raw URL.
