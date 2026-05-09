# Flarecrawl v0.22.2 → v0.23.0 Upgrade Spec — Hard Targets

## Goal

Make flarecrawl effective on heavily-defended SPAs (Cloudflare bot-protected,
JS-app-state data, modal-driven downloads, embedded video providers).

Driving incident (2026-05-09): scraping `https://www.war.gov/UFO/` — 162-record
UAP disclosure SPA — required dropping flarecrawl entirely and reaching for
stealth-Playwright + curl_cffi + yt-dlp. Eleven concrete gaps surfaced. This
spec closes them.

## Constraints

- Don't regress the 1112+ test suite.
- Free-tier CF Browser Rendering must keep working — improvements may
  *prefer* the local-Chrome path on hard targets but not require it.
- `--paywall --stealth` cascade stays the default. New features compose with
  it, don't replace it.
- All public flag/JSON shape changes go through CHANGELOG with `BREAKING
  (minor)` / `Added` / `Changed` semantics from v0.22.0 onward.

## Phasing

The 11 items group into three releases:

| Release | Theme | Scope |
|---------|-------|-------|
| **v0.23.0** | Foundation: fix what's broken | CDP error surface, REST `--js-eval` truthfulness, cache poisoning, `fetch` paywall/stealth parity |
| **v0.24.0** | Capabilities: unlock SPAs | Response-body interception, real stealth in CF browser, local-Chrome backend, `--then-fetch` flow |
| **v0.25.0** | Productivity: less Python wrapper code | YAML recipes, yt-dlp passthrough, auto-data-discovery |

Each phase ships independently. v0.23.0 is shippable in ~1 sprint; v0.24.0 is
the bulk of the work; v0.25.0 is polish.

---

# v0.23.0 — Foundation

Goal: every existing flag does what its `--help` says. No new surface area.

## P1.1 — Diagnose & fix CDP rejection (item #2)

**Current state:** CDP fails on a fresh-token, free-tier account with
`HTTP 400` rejecting the WebSocket. Repro:

```bash
flarecrawl scrape https://example.com --cdp --json
# CDPConnectionError: WebSocket connection failed: server rejected
# WebSocket connection: HTTP 400
```

This breaks `--cdp`, `--interactive`, `--live-view`, `--har`, `--record`,
`--save-cookies`/`--load-cookies` (when CDP-only paths trigger), and the
`interact` command in full.

**Hypothesis ranking (verify in order):**

1. **Free-tier accounts can't open `wss://...browser-rendering/devtools/browser`** — the Workers Paid tier may be required. Check via `curl -X POST .../browser-rendering/sessions` with the same token. If 4xx → free-tier limitation.
2. **Endpoint URL drifted** — CF's docs may have moved the path (e.g. `/sessions/{id}/devtools` instead of `/devtools/browser`). Check current Browser Rendering REST shape.
3. **Auth header format** — Bearer vs `X-Auth-Email`/`X-Auth-Key`. Test both.
4. **Account ID encoding** — current code does `.format(account_id=...)`; verify no escaping issue for accounts containing dashes.

**Files to touch:**

- `src/flarecrawl/cdp.py:400-454` — connect path
- `src/flarecrawl/credentials.py` — surface "tier" if CF reports it on `auth status`
- `src/flarecrawl/cli.py` — improve error message

**Target state:**

- `cdp.py` distinguishes the failure modes:
  - 400 with body containing `"requires_paid_tier"` (or equivalent) → raise `CDPTierError("CDP requires Workers Paid plan")`
  - 401/403 → raise `CDPAuthError("Token lacks 'Browser Rendering - Edit' permission")`
  - Generic 4xx → raise `CDPConnectionError(status, body)` including the response body for triage
- CLI catches each, prints one-line actionable error, exits with appropriate code (`AUTH_REQUIRED=2`, `FORBIDDEN=5`, generic `1`)
- `auth status --json` adds `"cdp_eligible": true|false|unknown` based on a HEAD/OPTIONS probe at status time (cached 24h)

**Sample CLI surface:**

```bash
$ flarecrawl scrape https://example.com --cdp
Error: CDP unavailable on this account.
  Reason: Workers Paid tier required for browser-rendering CDP WebSocket.
  REST mode (default scrape) still works on free tier.
  Upgrade: https://dash.cloudflare.com/?to=/:account/workers/plans
```

**Tests:**

- `tests/test_cdp_errors.py` (new): mock 400/401/403 responses, assert correct exception type and exit code.
- Live test (auth-gated): `tests/live/test_cdp_live.py` — only runs if `FLARECRAWL_CDP_LIVE=1`. Smoke: connect, evaluate `1+1`, disconnect.

---

## P1.2 — REST `--js-eval` truthfulness (item #11)

**Current state:** `--js-eval` is documented on `scrape` (item 36 in AGENTS.md
qualifies it as "proper JS eval (async, typed)" *with* `--cdp`). Without
`--cdp`, the value silently disappears:

```bash
flarecrawl scrape https://example.com --js --js-eval "document.title" --json
# Returns: {"data": {"content": "...", "elapsed": 0.5}}  -- no jsEvalResult key
```

This is the source of "I added `--js-eval` and got nothing back" confusion.

**Files to touch:**

- `src/flarecrawl/cli.py` — `scrape` command flag handling (`js_expression`)
- `src/flarecrawl/client.py` (or wherever the REST `/scrape` body is built)

**Target state:** three options, picking option B:

| Option | What | Tradeoff |
|--------|------|----------|
| A | Make REST js-eval work via CF's `/content` endpoint with eval | CF doesn't expose a public REST hook for return values. Punt. |
| B | Auto-promote to `--cdp` when `--js-eval` is set | Same pattern as `--interactive`/`--live-view` already do (item 44). Consistent. |
| C | Hard error — refuse silently | Forces user understanding but breaks people relying on side-effect-only eval (e.g. `document.scrollTo(0, 9999)`) |

**Implementation (option B):**

```python
# cli.py scrape command — near other auto-promote logic
if js_expression and not use_cdp:
    use_cdp = True
    if not quiet:
        console.print("[dim]auto-promoting to --cdp for --js-eval (returns typed result)[/dim]", file=sys.stderr)
```

**Edge case:** `--js-eval` with no `--js` should still imply `--js` (current
behaviour) AND `--cdp` (new). Document the chain in the flag help.

**Tests:**

- `tests/test_cli_promotion.py::test_js_eval_promotes_to_cdp` — assert `--js-eval foo` sets `use_cdp=True`
- Update existing tests that pass `--js-eval` and expect REST path

---

## P1.3 — Don't cache empty / non-200 responses (item #10)

**Current state:** From AGENTS.md item 13: "Responses are cached for 1 hour
by default — use `--no-cache` for fresh data." But this caches *all*
responses including:

- 403 / 451 / 5xx
- 200 with content length 0 or near-0 (the 293-byte SPA stub case)
- Auth failures that succeeded HTTP-wise but returned an error page

Concrete bite during the war.gov dogfood: an early empty scrape was cached.
Subsequent `--no-cache`-less attempts kept returning the same empty result
for an hour. ~5min wasted before figuring out the cache was the problem.

**Files to touch:**

- `src/flarecrawl/cache.py` — write path

**Target state:** add a `cacheable_response()` predicate:

```python
def cacheable_response(status: int, content: bytes | str, format_: str) -> bool:
    """Decide if a response should be cached. Errs toward NOT caching."""
    if status != 200:
        return False
    if isinstance(content, str):
        body = content.encode("utf-8")
    else:
        body = content
    # Empty body
    if len(body) == 0:
        return False
    # Suspiciously small for a full page render
    if format_ in ("html", "markdown") and len(body) < 1024:
        # Heuristic: real pages render to at least 1KB of content
        return False
    return True
```

Wire into `cache.py:store()`. Add `[cache] skipped (status=403)` debug log
under `--debug`.

**New flag:** `--cache-empty` — opt-in to keeping the old (broken) behaviour
in case anyone depends on it. Default off.

**Tests:**

- `tests/test_cache_predicate.py`: 200 empty → no cache, 200 large → cache, 403 → no cache, 200 with 500 bytes HTML → no cache
- Regression: existing cache hit-rate tests must still pass for the ≥1KB happy path

---

## P1.4 — `fetch` inherits paywall + stealth TLS (item #4)

**Current state:** `flarecrawl fetch URL -o file.pdf` uses a vanilla `httpx`
call. On war.gov, this returns 403 even for URLs that the same browser
session loads fine. Reason: war.gov fingerprints TLS handshakes (JA3/JA4)
and rejects non-Chrome.

`scrape --paywall --stealth` already plumbs `curl_cffi` for this exact
problem. `fetch` doesn't.

**Files to touch:**

- `src/flarecrawl/fetch.py` (159 lines, easy refactor target)
- `src/flarecrawl/paywall.py` — extract reusable `make_stealth_client()`
- `src/flarecrawl/cli.py` — add `--paywall --stealth` flags to `fetch`
  command for parity with `scrape`

**Target state:**

```python
# fetch.py
def fetch(url: str, *, output: Path | None = None,
          stealth: bool = False, paywall: bool = False,
          session: dict | None = None) -> FetchResult:
    """Fetch a URL — content-aware, optional stealth/paywall cascade."""
    client = _make_client(stealth=stealth, paywall=paywall, cookies=session)
    # ... existing logic, but client may now be curl_cffi.Session
```

`_make_client` lives in a new shared `_client_factory.py` so both `fetch`
and `scrape` use the same construction path. This is also where future
features (cookie handoff from a prior `scrape`) will hook in.

**CLI:**

```bash
flarecrawl fetch https://protected.example.com/file.pdf -o out.pdf --paywall --stealth
flarecrawl fetch https://x.com/y.pdf -o out.pdf --load-cookies session.json --stealth
```

**Tests:**

- `tests/test_fetch_stealth.py`: assert `--stealth` builds a curl_cffi-backed client
- Live test (gated): `tests/live/test_fetch_protected.py` — fetch a known TLS-fingerprint-protected URL, assert 200 with `--stealth` and 403 without

---

## v0.23.0 release checklist

- [ ] All four phases land
- [ ] CHANGELOG: `Fixed` entries for P1.1, P1.3; `Changed` for P1.2 (auto-promotion is observable); `Added` for P1.4 flags on `fetch`
- [ ] AGENTS.md: update items 11, 13, 36, 44 to reflect new behaviour
- [ ] `pytest tests/ -v` clean (1112+ tests)
- [ ] Docs: update `docs/` with a "hard target playbook" page using war.gov as the worked example

---

# v0.24.0 — Capabilities

Goal: bypass *every* defence the v0.23.0 fixes still can't punch through, by
giving users two new tools — response interception, and a local-Chrome path.

## P2.1 — Response body interception (item #3) — **highest ROI**

**Current state:** `--har` captures URLs but not bodies. SPAs increasingly
load their data layer (CSV, JSON, XHR) on page load. Today flarecrawl can
*see* those requests but not *save* them, forcing users to drop into a real
browser just to capture one file.

**Concrete win on war.gov dogfood:** all 162 records lived in one 185KB
`uap-csv.csv` that the page fetched on init. Capturing its body would have
collapsed the entire scraping problem to "parse this CSV".

**Files to touch:**

- `src/flarecrawl/cdp.py` — wire `Network.responseReceived` + `Network.getResponseBody`
- `src/flarecrawl/cli.py` — `scrape` command: new flags `--capture-pattern`, `--capture-dir`
- `src/flarecrawl/client.py` — REST path: pass-through to `--har` machinery? (REST can't intercept bodies; only CDP can)

**Target state:**

```bash
# Capture all .csv and .json responses while scraping the page
flarecrawl scrape https://www.war.gov/UFO/ \
  --js \
  --capture-pattern '*.csv,*.json' \
  --capture-dir ./captured/ \
  --json

# Output (stderr):
#   [capture] uap-csv.csv (185114 bytes) -> ./captured/uap-csv.csv
#   [capture] manifest.json (12kB) -> ./captured/manifest.json
```

**JSON output adds a `captured` array:**

```json
{
  "data": {"url": "...", "content": "..."},
  "meta": {
    "captured": [
      {"url": "https://.../uap-csv.csv", "path": "./captured/uap-csv.csv", "size": 185114, "content_type": "text/csv"}
    ]
  }
}
```

**Pattern matching:** `fnmatch` on the URL path component (after the last `/`)
+ optional `--capture-content-type` for MIME-based filtering (`application/json`,
`text/csv`, `application/octet-stream`).

**Implementation notes:**

- CDP-only feature (REST has no body access). Auto-promote to `--cdp` when
  `--capture-pattern` is set, same pattern as #11.
- Bodies > 50MB stream to disk, don't buffer.
- File-name collision: append `.1`, `.2`, etc. Or `--capture-overwrite`.
- For non-text bodies, write binary; don't try to decode.

**Tests:**

- `tests/test_capture_response.py`: pattern match, size limit, content-type filter, collision handling
- Live: `tests/live/test_capture_live.py` — scrape a page that fetches a known JSON, assert capture worked

---

## P2.2 — Real stealth in CF Browser Rendering (item #1)

**Current state:** `--stealth` only impersonates TLS for direct HTTP. The
CF-hosted Chromium that `--js` mode uses lacks the patches that
`playwright-stealth` applies (canvas fingerprint, WebGL vendor, navigator
properties, etc.). Result: protected sites return a 293-byte stub.

**Two paths, do both:**

### P2.2a — Push patches into the CF browser session (best-effort)

CF Browser Rendering accepts `Page.evaluateOnNewDocument` via CDP. Inject
the playwright-stealth init script before navigation:

```python
# stealth.py — new
STEALTH_INIT_JS = importlib.resources.read_text("flarecrawl.assets", "stealth_init.js")

async def apply_stealth(page: CDPPage) -> None:
    """Inject stealth patches before any user JS runs."""
    await page.send("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_INIT_JS})
```

Vendor `stealth_init.js` from the upstream `playwright-stealth` (MIT) into
`src/flarecrawl/assets/`. License attribution in `THIRD_PARTY_LICENSES.md`.

This *only* works in CDP mode. REST `/scrape` can't inject pre-navigation
scripts. Document the limitation.

### P2.2b — Local Chrome backend (item #8) — primary fallback

When `--browser local` is set (or CF stealth fails), use a local headed
Chromium via Playwright. Already supported via `FLARECRAWL_CDP_ENDPOINT` but
hidden. Make it first-class:

```bash
flarecrawl scrape URL --browser local              # spawn local Chromium
flarecrawl scrape URL --browser local --headed     # visible window (debugging)
flarecrawl scrape URL --browser local --keep-alive 60  # session reuse
```

Implementation: extend `cdp.py:_AsyncCDPClient` to support local CDP. When
`--browser local`:

1. Launch `playwright.chromium.launch(headless=not args.headed)`.
2. Get the CDP WebSocket URL via `browser.ws_endpoint`.
3. Reuse all existing CDP machinery — same code path, different transport.
4. Auto-apply `STEALTH_INIT_JS` on every page (always, no flag).

**New optional dep:** `pip install flarecrawl[local-browser]` pulls
`playwright` + `playwright-stealth`. Without it, `--browser local` errors
with installation hint.

**Files to touch:**

- `src/flarecrawl/cdp.py` — add local-launch branch
- `src/flarecrawl/stealth.py` — new init-script injection
- `src/flarecrawl/assets/stealth_init.js` — vendored
- `pyproject.toml` — `local-browser` extra
- `THIRD_PARTY_LICENSES.md` — playwright-stealth attribution

**Tests:**

- `tests/test_local_browser.py`: launch, scrape, close (skipped without `playwright`)
- Live: war.gov as a fixture URL — must return >50KB content with `--browser local`, fail gracefully without

---

## P2.3 — `--then-fetch URL_LIST` flow (item #6)

**Current state:** Common pattern: load page in browser to establish session
+ pass anti-bot, then mass-fetch a list of URLs reusing those cookies. Today
this requires `scrape --save-cookies` + a shell loop of `fetch
--load-cookies`. The latter still 403s before P1.4 lands; even after P1.4
ships, the cookie+TLS handshake is awkward to chain.

**Files to touch:**

- `src/flarecrawl/cli.py` — new `--then-fetch` and `--then-fetch-from` flags on `scrape`
- `src/flarecrawl/_client_factory.py` (added in P1.4) — the path that builds the post-scrape fetch client

**Target state:**

```bash
flarecrawl scrape https://www.war.gov/UFO/ \
  --js --browser local \
  --capture-pattern '*.csv' --capture-dir ./csv/ \
  --then-fetch-from ./csv/uap-csv.csv \
  --then-fetch-column "PDF | Image Link" \
  --then-fetch-output ./pdfs/ \
  --then-fetch-workers 8
```

CSV column extraction is built-in. For non-CSV inputs:

```bash
flarecrawl scrape PAGE --then-fetch "url1,url2,url3" -o ./files/
flarecrawl scrape PAGE --then-fetch-from urls.txt -o ./files/
```

**Behaviour:**

1. Run the scrape exactly as before, applying `--capture-pattern` if set.
2. After scrape completes, extract URLs from `--then-fetch*` arg (priority: `--then-fetch` > `--then-fetch-from`).
3. Hand the captured cookies + chosen browser TLS profile to a `curl_cffi` thread pool.
4. Download in parallel with `--then-fetch-workers` (default 4).
5. Resume-safe: skip files that already exist with non-zero size.

**Output (NDJSON to stdout, like batch mode):**

```json
{"index": 0, "status": "ok", "url": "...", "path": "./pdfs/foo.pdf", "size": 12345}
{"index": 1, "status": "error", "url": "...", "error": {"code": "TIMEOUT"}}
```

**Tests:**

- `tests/test_then_fetch.py`: URL list parsing (CSV col, plain list, comma-string), worker pool, resume-skip
- Integration test using a fixture HTTP server with a CSV manifest + 3 PDFs

---

## v0.24.0 release checklist

- [ ] P2.1, P2.2a, P2.2b, P2.3 land
- [ ] `--browser local` documented as the recommended path for hard targets
- [ ] CHANGELOG: `Added` for all four; mention `local-browser` extra
- [ ] New `docs/HARD-TARGETS.md` walkthrough using war.gov as a worked example, end-to-end with `--browser local --capture-pattern --then-fetch-from`

---

# v0.25.0 — Productivity

## P3.1 — YAML interaction recipes (item #5)

**Goal:** capture multi-step browser flows declaratively. Replaces the
~40-line Python wrappers users currently write.

**Files to touch:**

- `src/flarecrawl/recipe.py` (new)
- `src/flarecrawl/cli.py` — new `recipe` subcommand
- `src/flarecrawl/schemas/recipe.schema.json` — JSON Schema

**Target state:**

```yaml
# scrape-uap-records.yml
version: 1
goto: https://www.war.gov/UFO/
browser: local
stealth: true

steps:
  - wait_for: networkidle
  - capture:
      pattern: "*.csv"
      to: ./csv/

  # Iterate every record: open modal → click download
  - for_each:
      selector: "[data-record-trigger]"
      max: 200
      do:
        - click: "{el}"
        - wait_for: ".record-modal-shell"
        - click: ".record-modal-download"
        - capture_download:
            to: ./pdfs/
        - press: Escape
        - wait: 500ms

output:
  format: ndjson
  manifest: ./manifest.json
```

**Run:**

```bash
flarecrawl recipe scrape-uap-records.yml
flarecrawl recipe scrape-uap-records.yml --dry-run   # validate, print step plan
flarecrawl recipe scrape-uap-records.yml --resume     # skip steps already completed
```

**Step types (initial):** `goto`, `click`, `fill`, `press`, `wait`,
`wait_for`, `capture`, `capture_download`, `for_each`, `eval`, `screenshot`.

**Resume support:** journal each completed step to
`./recipe-state-<hash>.ndjson`. On `--resume`, skip until first uncompleted.

**Tests:**

- `tests/test_recipe_parser.py`: schema validation, malformed YAML, missing required fields
- `tests/test_recipe_runner.py`: each step type against a fixture HTTP server
- `tests/live/test_recipe_real.py`: small live recipe against a stable test page

---

## P3.2 — yt-dlp passthrough on `videos` (item #7)

**Current state:** `flarecrawl videos URL --json` discovers `<video>` and
embeds via DOM scraping. Misses provider-specific players (DVIDS, YouTube
private embeds, Vimeo with auth). yt-dlp has 1500+ extractors that solve
this exact problem.

**Files to touch:**

- `src/flarecrawl/videos.py`
- `pyproject.toml` — add `yt-dlp` to a new `videos` extra

**Target state:**

```bash
flarecrawl videos https://example.com --json
# Now also includes provider-resolved entries:
{
  "data": [
    {"url": "https://...", "type": "video/mp4", "source": "dom"},
    {"url": "DVIDS:1006083", "title": "...", "source": "yt-dlp", "extractor": "dvidshub"}
  ]
}

flarecrawl videos URL --download -o ./videos/
# When yt-dlp entry: shells to yt-dlp -o ... URL
# When dom-discovered direct URL: uses fetch with stealth
```

**Auto-discovery flow:**

1. Run existing DOM-based discovery.
2. For each `<iframe src=...>` that matches a known yt-dlp extractor host (DVIDS, YouTube, Vimeo, etc.), invoke `yt_dlp.YoutubeDL().extract_info(url, download=False)`.
3. Merge results, deduplicating by canonical video ID.

**Subprocess vs library:** prefer `yt_dlp` as a Python library (faster, no
spawn overhead). Fall back to subprocess if extractor needs a feature only
the CLI exposes.

**Tests:**

- `tests/test_videos_yt_dlp.py`: mock yt-dlp `extract_info`, assert merge logic
- Live (gated, opt-in): `tests/live/test_videos_dvids.py` — known DVIDS URL, assert resolution to mp4

---

## P3.3 — Auto data-discovery (item #9)

**Goal:** during a scrape, detect when the page fetched a structured-data
file (CSV/JSON/XLSX) that probably contains the bulk of the page's content.
Surface it in the result.

**Heuristic:**

- Response with `Content-Type: text/csv | application/json | application/vnd.ms-excel | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Body size ≥ 1KB
- URL fetched same-origin or from a small allowlist of known data CDNs
- Not in `--exclude-pattern`

**Output:**

```json
{
  "data": {"url": "...", "content": "..."},
  "meta": {
    "data_sources": [
      {"url": "https://.../uap-csv.csv", "content_type": "text/csv", "size": 185114, "rows_estimate": 573}
    ]
  }
}
```

**New flag:** `--auto-data` (default on with `--js`). Includes the data file
URL in `meta.data_sources` and saves to `--capture-dir` if set; otherwise
just lists.

**Files to touch:**

- `src/flarecrawl/cdp.py` — leverage P2.1 capture machinery
- `src/flarecrawl/cli.py` — flag + meta plumbing

**Tests:**

- `tests/test_auto_data.py`: response classification, size threshold, exclusion
- Live: scrape a page known to load JSON data, assert it appears in `data_sources`

---

## v0.25.0 release checklist

- [ ] P3.1, P3.2, P3.3 land
- [ ] Three example recipes shipped in `examples/recipes/`
- [ ] CHANGELOG entries
- [ ] Docs: a "Recipes cookbook" page
- [ ] `videos` extra documented as opt-in for the heavyweight extractors

---

# Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| CDP failure (P1.1) is a paid-tier requirement we can't fix | Medium | If true, document loudly in error and steer free-tier users to P2.2b local browser path. v0.23.0 still ships P1.2/P1.3/P1.4. |
| Vendoring playwright-stealth init JS conflicts with their license | Low | MIT — straightforward attribution in `THIRD_PARTY_LICENSES.md`. |
| Local browser extra adds 200MB+ to install footprint | Cert. | It's *opt-in* via `[local-browser]` extra. Default install unchanged. |
| Recipe DSL grows into a maintenance pit | Medium | Keep step types minimal (~10 in v0.25.0). Resist adding control flow beyond `for_each`. |
| Auto data-discovery false-positives flood `meta.data_sources` | Low-Med | Size threshold + content-type allowlist + `--no-auto-data` opt-out. |
| `curl_cffi` Chrome131 impersonation drifts as Chrome version moves | Med | Pin in extra; add an integration test that re-checks against war.gov-class targets quarterly. |

---

# Cross-cutting concerns

## Telemetry

The new code paths (capture, then-fetch, recipe runner) need timing/usage
hooks parallel to existing `telemetry.py` patterns. Add per-feature counters
so `flarecrawl usage --json` reflects them.

## Cache interaction

P2.1 capture results should *not* be cached by default — capturing implies
the user wants fresh content. P3.3 auto-data results may be cached (just
metadata, small).

## Backward compat

| Flag/output | Status |
|-------------|--------|
| `--js-eval` without `--cdp` | Auto-promotes (observable change but matches existing `--interactive`/`--live-view` pattern) |
| Cache no longer stores empty/non-200 | Behaviour change. `--cache-empty` opt-in for old behaviour. CHANGELOG note. |
| `fetch` gains `--paywall --stealth` | Pure addition. |
| `meta.captured`, `meta.data_sources` | Pure addition to JSON output. |
| `--browser local` | Pure addition. Default unchanged. |
| `recipe` subcommand | Pure addition. |

No `BREAKING` changes across the three releases. All `BREAKING (minor)` if
the cache-no-empty default surprises someone (justify in CHANGELOG).

---

# Open questions

1. **Should `recipe` have a streaming `--resume` resume-from-step or just
   resume-from-output?** Suggest output-based: simpler, idempotent.
2. **`--capture-pattern` syntax** — fnmatch (`*.csv`) vs regex (`/\.csv$/`)?
   Suggest fnmatch with `--capture-regex` escape hatch.
3. **`--browser local` — Firefox option?** Defer to v0.26.0 unless trivial.
   Chromium covers the dogfood case.
4. **Does P2.2a stealth-injection raise the per-page browser_seconds bill?**
   Spike before commit. If >5% overhead, gate behind `--stealth-cdp` flag
   rather than always-on.

---

# Definition of done (across the spec)

- [ ] All three releases tagged + published
- [ ] `pytest tests/ -v` green for each release
- [ ] `python tests/corpus.py` ≥ same pass rate as v0.22.2
- [ ] Live-test fixture page (war.gov-class hardness) added; baseline
      pass rate documented
- [ ] AGENTS.md updated to reflect new flags + which features need which
      tier / extras
- [ ] At least one external dogfood project (this workspace's
      `playbooks/ufo_docs_download.py`) successfully ports to using *only*
      flarecrawl with the new features — Python wrapper deleted
