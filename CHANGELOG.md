# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **MCP server** (`flarecrawl mcp`) — exposes flarecrawl as a Model Context
  Protocol stdio server so agents (Claude Code, Cursor, …) can use the toolkit
  directly. 36 tools across a three-tier surface (Forma Protocol §30): 5
  orientation, 5 T1 composite (`read_page`, `research_web`, `site_overview`,
  `extract_data`, `check_page_changes`), 17 T2 curated, 9 T3 raw (`*_raw`, full
  CLI fidelity). `capabilities()` is the keystone — one call returns the full
  catalogue, permissions, coverage map, and worked recipes. Content tools
  default to `--agent-safe` sanitisation; `meta.blocked` bot-wall verdicts
  auto-generate recovery `next_steps` (Akamai → stealth/p6; CF-1020 → terminal).
  Binary tools (screenshot/pdf) return file paths, not base64. Read-only mode
  (`flarecrawl mcp --read-only`) excludes the 5 write/destructive tools.
  Optional dependency: `uv pip install 'flarecrawl[mcp]'`. Eleven CLI commands
  are intentionally CLI-only (declared as coverage gaps in `capabilities()`
  with workarounds — auth/secret entry, long-running crawls, config management,
  interactive browser flows).
- `.mcp.json` at the repo root wires flarecrawl as its own MCP server
  (dogfooding; requires `uv tool install --editable '.[mcp]'`).

### Changed

- **`cli.py` split into a `cli/` package** — the 6,882-line monolith is now 17
  command-family modules (`scrape`, `crawl`, `fetch`, `media`, `techdetect`,
  `search`, `recipe`, `sessions`, … + shared `_common` helpers) assembled
  behind the unchanged `flarecrawl.cli:app` entry point. Pure refactor: no
  behaviour change, `--help` byte-identical, full suite green. Note for test
  authors: patches that bound to `flarecrawl.cli.<helper>` now target the
  module that owns the name (e.g. `flarecrawl.cli._common`).

### Fixed

- README/AGENTS doc drift: test counts (was 723 / 1404+, now 1,500+),
  agent-safety test count (137 → 196), custom-overlay size (60/71 → 104),
  attack-corpus categories (12 → 13), and the Project Structure tree
  (listed 18 of 45 modules → now complete and grouped).

## [0.30.1] - 2026-06-02

Tech-detect hardening — fingerprint corpus expansion, categorisation
fixes, structural overlay-merge bug fix, and end-to-end validation
audit. No new commands; the hardening lands behind the existing
`tech-detect` / `--tech-detect` surface plus a new `--render` mode.

### Added

- `tech-detect --cdp` flag — routes the command through Cloudflare
  Browser Run CDP and injects the JS-globals probe (~5,500 property
  paths) via `Runtime.evaluate`. Reuses the same probe machinery the
  v0.30.0 `scrape --cdp --tech-detect` path uses, so JS-globals
  coverage is identical between the two surfaces. Unlocks the ~880
  Wappalyzer fingerprints that only fire via window globals
  (`jQuery.fn.jquery`, `Next.js buildId`, framework-detect markers,
  …). Costs CF browser time like any other CDP-routed command.
- 11 new custom-overlay vendors: Roam (with `X-ROAM: HIT` response-
  header detection across all 20 registered tourism sites), Mews,
  Cloudbeds, Bokun, Resy, Tock, TheFork, Triptease, Eventbrite, ATDW,
  Localis, Simpleview. Overlay grew from 60 to 71 entries.
- `_overlay_note` annotation field on overlay fingerprints —
  documents validation status (e.g. "vendor brand site does not self-
  embed widget — TODO: add a known customer site to bench corpus").
- 9 new bench corpus entries (Beds24, Bopple, Channex, Peek Pro,
  RedBalloon, Resy, Windcave, Quandoo, Triptease) as regression guards
  for the corresponding overlay fingerprints. 60-site corpus still
  scores precision = recall = F1 = 1.000.
- Operator scripts under `tests/` (`validate_custom_overlay.py`,
  `_probe_vendor_sites.py`, `_probe_vendor_sites_cdp.py`) for the next
  audit pass. Not pytest targets.
- `docs/architecture/` — interactive component/flow diagram (16
  components, 11 flows) from codebase-cartographer.

### Fixed

- 19 custom overlays were silently emitting `categories: []`, breaking
  `--only-categories` / `--exclude-categories` filtering against
  overlay detections (SevenRooms, OpenTable, ResDiary, Quandoo, Resy,
  Tock, TheFork, FareHarbor, Rezdy, Bokun, Mews, Cloudbeds, SiteMinder,
  Triptease, Square Online, Eventbrite, Localis, Simpleview, Craft CMS).
- Overlay-merge type mismatch silently dropped list-form `dom`
  selectors when upstream stored `dom` as a dict. SevenRooms regression:
  its overlay selectors `a[href*='sevenrooms.com/reservations']` and
  `iframe[src*='sevenrooms.com']` never reached the engine. The merge
  now promotes overlay list selectors to `{selector: {}}` before dict-
  merging, with a `logger.debug` line per promotion. New regression
  test: `test_custom_overlay_list_dom_merges_into_upstream_dict_dom`.
- Duplicate top-level JSON keys silently shadowed each other under
  Python's last-key-wins parser. `Bokun` and `ATDW` were each declared
  twice, losing the earlier entry's patterns. Entries merged into a
  single object holding the union of patterns. New regression test:
  `test_custom_overlay_no_duplicate_top_level_keys`
  (`object_pairs_hook` check).
- 9 non-JS/non-PHP implies chains patched: Amber/Kemal (Crystal),
  Streamlit/PyWebIO/CherryPy (Python), WEBrick (Ruby), Yaws (Erlang),
  Hugo (Go), Turbopack (Rust).
- GSAP + CloudFront + Node.js (Express/Next.js header patterns)
  upstream implies-chain gaps.
- `crawl_start` rejected CF-incompatible kwargs with confusing errors;
  now validates explicitly and accepts CLI-shaped name aliases so SDK
  calls match CLI semantics.

### Changed

- `selectolax` promoted from `[perf]` optional extra to core
  dependency. It is imported unconditionally by `extract.py` and
  `authcrawl.py` (both pulled in transitively whenever the CLI runs),
  so a bare `uv sync` install left the CLI crashing with
  `ModuleNotFoundError` on `flarecrawl scrape`.
- `keyring` promoted from optional extra to core dependency (same
  rationale).
- Bench corpus expanded to 60 sites; P/R/F1 = 1.000 maintained.

## [0.30.0] - 2026-06-01

Local technology detection. Wappalyzer fingerprint matching now ships
in the wheel; runs over the data each command already collected (HTML
+ response headers + cookies + injected JS-globals probe where the path
allows it) so detection adds zero CF browser time and zero extra API
calls.

### Added

- **`flarecrawl tech-detect <URL>`.** Dedicated subcommand — the primary
  surface for the feature. Single GET per URL (curl_cffi when `--stealth`
  or `--session`, httpx otherwise), full HTML + response headers +
  cookies signal set, JSON envelope or compact table output, parallel
  batch via `-i FILE -w N`, streaming via `--ndjson`. Filters:
  `--min-confidence N`, `--only-categories CMS,Frameworks`,
  `--exclude-categories Analytics,Tag\ managers`. Accepts `--stdin` to
  read HTML from a pipe with no network. Discoverable via
  `flarecrawl guide tech-detect` (aliases: `tech`, `wappalyzer`,
  `fingerprint`, `stack`, `detect`).

- **`Client.detect_tech(html, headers, url, cookies, js_globals)`.**
  Local Python API returning a confidence-sorted list of detection dicts
  (`name`, `version`, `categories`, `groups`, …). All six Wappalyzer
  signal layers are reachable when the caller supplies them. Lazy-loaded
  process-wide singleton, thread-safe under concurrent crawl/scrape.

- **`--tech-detect` flag on `scrape`, `crawl`, `fetch`.** In-line
  detection during the existing workflows. Attaches `technologies:
  [...]` at the top of the result record; emits a compact `[tech]`
  summary line to stderr on non-JSON output. Signal coverage per path:

  | Path | HTML | Headers | Cookies | JS globals |
  |---|---|---|---|---|
  | `tech-detect` subcommand | yes | yes | yes | no |
  | `fetch --tech-detect` (HTML branch) | yes | yes (same transport) | yes (cookie jar) | no |
  | `scrape --tech-detect` (CDP path) | yes | yes (`Network.responseReceived`) | yes (`CDP.getCookies`) | **yes** (probe injected via `Runtime.evaluate`) |
  | `scrape --tech-detect` (REST path) | yes | yes (streaming GET side-fetch, 32 KB cap) | yes (side-fetch) | no |
  | `scrape --stdin --tech-detect` | yes | no | no | no |
  | `crawl --tech-detect` | yes (per record) | no | no | no |

- **`MainDocumentHeaders` CDP collector.** Subscribes to
  `Network.responseReceived`, filters to the navigation document only
  (subresources skipped), surfaces headers for the tech-detect pipeline.

- **`WappalyzerClient.build_js_probe()`.** Generates the CDP-injectable
  JS that probes every `js`-keyed property path in the fingerprint DB
  (~5,500 paths). Result is parsed from a hidden `#wap-probe` element
  and fed back as `js_globals=` for higher-confidence matches on
  JS-heavy SPAs.

- **Vendored fingerprint database.** ~7,500 upstream technologies from
  `enthec/webappanalyzer` (GPL-3.0 data — see
  `src/flarecrawl/wappalyzer_data/LICENSE.wappalyzer_data`) plus a
  60-entry custom overlay covering CMS platforms (Craft CMS), CSS
  frameworks (Tailwind CSS), hospitality/tourism booking engines
  (SevenRooms, ResDiary, OpenTable, Mr Yum, me&u, Rezdy, FareHarbor,
  SiteMinder, …), accommodation PMS, channel managers, and POS systems.

### Notes

- The bundled fingerprint data is GPL-3.0; the rest of flarecrawl stays
  MIT. Strip `wappalyzer_data/` from the wheel if you need to ship a
  pure-MIT artefact, or pass `data_dir=` to `WappalyzerClient` to use a
  custom fingerprint set.

- REST scrape's side-fetch uses a streaming GET (not HEAD) with a 32 KB
  body cap — HEAD is unreliable (servers often omit `Set-Cookie`,
  `X-Powered-By`, or `Server` on HEAD). Honours `--proxy` and
  `--stealth`. Transport errors are caught narrowly (`httpx.HTTPError`,
  `ConnectionError`, `OSError`, `TimeoutError`) and never fail the
  parent command.

- `_attach_tech` is idempotent — a second call against a record that
  already has a `technologies` key is a no-op.

- Test coverage: 35 dedicated tests including a local HTTP fixture
  server that validates header-only fingerprints (Cloudflare via
  `Server: cloudflare`), cookie-only fingerprints (Java via
  `JSESSIONID`), filter flags, transport resilience (unreachable host,
  500 response), and an end-to-end CLI integration check.

- `tests/test_cli.py::TestHelp::test_version` now reads
  `flarecrawl.__version__` instead of asserting a hardcoded string, so
  it no longer lags behind release bumps.

## [0.29.0] - 2026-05-17

Agent-discoverability layer. v0.28.0 landed a large feature surface; this
makes it findable on first contact — `--help` is per-command reference and
never tells an agent *which* command to reach for or how they compose.

### Added

- **`flarecrawl guide [topic]`.** Emits the packaged AGENTS.md (hatch
  `force-include`, so it works after a bare `pip install` with no repo on
  disk; loader falls back to repo root for editable installs). `guide` =
  preamble + Quick Reference + topic index; `guide <topic>` = one section
  via exact→prefix→substring + alias resolution (`hard-targets`, `json`,
  `errors`, `rules`, `auth`, …); `guide --list` = every section. Single
  source of truth — the same AGENTS.md repo readers see. New pure/testable
  `guide.py` (parser/slug/alias resolver).

- **Root `--help` mental-model epilog.** Bare `flarecrawl` and `--help`
  now carry a 3-line orientation: the routing escalation ladder
  (fetch→scrape→stealth→local→recipe→p6) + `meta.blocked` note + a pointer
  to `flarecrawl guide`. The pointer is a command, not "read AGENTS.md",
  so it works in any install layout.

### Changed

- **Teaching-error → guide pointers.** The highest-confusion failures
  (auth-required, recipe error, p6 failure) now route a stuck agent to
  the relevant `flarecrawl guide <topic>`. Targeted, not blanket.

- `test_guide.py` (20 tests, incl. real-AGENTS.md packaging assertions).
  Wheel build verified to contain `flarecrawl/AGENTS.md`. Full non-live
  suite green (821 tests).

## [0.28.0] - 2026-05-16

Closes the OTDB hard-target field-report backlog (a 9-connector AU
EV-charging harvest against Akamai / Cloudflare / Imperva / CloudFront).
Nine bug/DX fixes plus five features, including the P6 mint→replay
primitive that carried the entire workstream.

### Added

- **`flarecrawl p6` — mint→replay anti-bot primitive.** New `p6.py`
  orchestrator: a local Chromium navigates a mint URL so the bot wall
  deposits its cookie shells (`_abck`, `bm_*`, `__cf_bm`, ...), then
  `curl_cffi --impersonate` replays the real targets carrying the jar
  plus a genuine Chrome JA3/JA4 handshake. Built-in **proactive re-mint**
  when the jar goes stale, **cumulative exponential cool-down** (the
  Akamai egress-escalation trap — backoff is keyed on total re-mints,
  not per-target, so sustained pressure backs off globally), and
  **terminal fast-fail** on Cloudflare 1020 (keyed on the egress, not
  the session — minting can't help). Resume journal. All
  browser/network is dependency-injected; the control loop is fully
  unit-tested without a browser or socket.

- **Machine-readable block detection (`meta.blocked`).** New
  `blockdetect.py`: a pure `detect_block(status, headers, body)`
  classifier with an ordered signature table — Cloudflare 1020
  (terminal), CF JS-challenge, Akamai interstitial (HTTP 200!) /
  edge-deny, Imperva, DataDome, PerimeterX, CloudFront 403, rate-limit.
  Surfaced as `meta.blocked` in `scrape` (CDP), `fetch` (`--json`), and
  the `recipe` summary, so connectors stop reinventing fragile
  per-vendor heuristics. Tesla-style SPA-404 is intentionally excluded
  (a generic detector would false-positive on every SPA).

- **`flarecrawl session inspect` — offline jar freshness.** New
  `jarhealth.py`: classifies anti-bot shell cookies, computes TTLs, and
  returns a verdict (`fresh` | `stale` | `expired` | `empty`) with no
  network call. Exit code is non-zero unless `fresh`, so connectors can
  re-mint *proactively* instead of after burning a blocked request. Also
  the freshness oracle P6 uses between replay batches.

- **`fetch --json` on `application/octet-stream` that is valid JSON.**
  When the caller explicitly requests JSON and the URL filename doesn't
  look binary, the body is JSON-sniffed before falling back to a binary
  download (no more files literally named `download`).

### Fixed

- **Windows cp1252 crash on output (highest recurrence — 2 independent
  hits).** `_output_json` and all `fetch` file writes now go through the
  UTF-8 / `errors="replace"` path. Valid content with emoji or
  box-drawing glyphs no longer hard-crashes or writes a 0-byte file.

- **`--json` no longer overrides backend / output / content-type.**
  `scrape --output PATH --json` now honours `--output` (was silently
  discarded). `fetch --session`/`--impersonate` now implies the
  curl_cffi TLS path and the JSON/raw-text branches use it too — a
  session jar is no longer defeated by adding `--json`.

- **HAR / capture flushed on failure.** `_scrape_single_cdp` writes the
  HAR and captured bodies in a `finally` block, so a
  `--wait-for-selector` timeout no longer discards the expensive
  bot-wall-clearing navigation. The selector timeout now surfaces a
  clean `selector 'X' not found after Ns` instead of a raw traceback.

- **`recipe` capture ordering & eval results.** `capture` steps are
  pre-armed *before* navigation (a `capture` after a `wait` no longer
  silently captures 0). `eval` and `get_content` step return values are
  surfaced in `steps[].result`. `browser: cf` + `capture` fails fast
  with a clear message (CF-hosted browser can't intercept response
  bodies) instead of silently yielding nothing.

### Changed

- **Recipe result schema frozen (`schema_version: 1`).** The `run()`
  summary contract is documented and versioned in `recipe.py`; `captured`
  is the canonical key (not `captures`). Connectors can key off
  `schema_version`.

- **`search` API-key disclosure.** The Jina-key requirement is stated
  up front in `--help` and the missing-key case exits as `AUTH_REQUIRED`
  with an actionable hint, instead of a generic failure.

- New unit suites: `test_blockdetect.py`, `test_jarhealth.py`,
  `test_p6.py` (60 tests across the three). Full non-live suite green
  (801 tests).

## [0.27.0] - 2026-05-16

### Fixed

- **`fetch` routing: non-HTML content types no longer traceback.** URLs returning
  `text/xml`, `application/xml`, `text/csv`, `text/plain`, `application/yaml`, and
  any other non-HTML type previously fell into the HTML → CF Browser Rendering branch,
  which called `_scrape_single()` without credentials and raised an unhandled exception.
  New `_is_html_content_type()` helper gates the CF branch to `text/html` and
  `application/xhtml+xml` only. A new `elif not info.is_html:` branch fetches the raw
  body and returns it verbatim — no CF auth required, no browser time consumed.
  Reproducer: `flarecrawl fetch "https://www.google.com/maps/d/kml?mid=abc&forcekml=1"`.

- **`--only-main-content` nav soup leaking.** When a `<main>` or `<article>` selector
  matched an element that was itself a nav-heavy container (link-density ≥ 60%), the
  full navigation was included in the output. `extract_main_content()` now measures link
  density on each candidate element; high-density hits fall through to the next selector
  or body fallback. A supplementary `_BODY_STRIP_EXTRA` tuple strips `site-header`,
  `site-nav`, `primary-nav`, `main-nav`, and `navbar-*` patterns from the body fallback.

### Added

- **RFC 6839 structured syntax suffix routing.** `application/*+json` (JSON-LD,
  GeoJSON, `problem+json`, `vnd.api+json`) and `application/*+xml` (RSS, Atom,
  KML/vnd, SOAP) were all classified as binary and sent to binary download. One suffix
  check (`endswith("+json")` / `endswith("+xml")`) in `_is_binary_content_type()` fixes
  the entire class — they now route to the JSON parse or raw-text branch as appropriate.
  Also added `application/x-ndjson`, `application/x-jsonlines`, and
  `application/jsonlines` to `_TEXT_TYPES`.

- **E2E routing test corpus.** `tests/fixtures/routing/` — 14 realistic fixture files
  (`.xml`, `.csv`, `.tsv`, `.rss`, `.atom`, `.kml`, `.yaml`, `.toml`, `.ics`, `.vcf`,
  `.ttl`, `.md`, `.geojson`, `.jsonld`, `.ndjson`). `routing_server` session fixture
  (stdlib-only `ThreadingHTTPServer` with explicit mimetype overrides for all extensions
  Python's `mimetypes` doesn't handle) serves them on a random port. 25 e2e tests in
  `test_fetch_routing_e2e.py` hit a real TCP socket with no monkeypatching; 35
  parametrised unit tests in `test_fetch_routing.py` cover 18 content types. 1404 tests
  total.

## [0.26.0] - 2026-05-09

### Added — Headless evasion (partial)

Continuing the v0.25.0 dogfood: headless `--browser local` still got
caught by behavioural-fingerprint engines. v0.26.0 adds two layers:

- **Synthetic interaction (`humanize`)** — new `flarecrawl.humanize`
  module emits believable mouse moves (cubic-Bezier paths between
  random viewport points), small wheel scrolls, and idle gaps before
  any meaningful page action. Defeats *post-navigation* behavioural
  detectors that gate sessions on JS-observable interaction history.

  Three profiles via `--humanize-profile`: `fast` (~700ms, 1 move +
  1 scroll), `natural` (~1500ms, 2+2), `thorough` (~3000ms, 4+3).

  Auto-on with headless `--browser local`; opt-out with
  `--no-humanize`. Headed mode skips it (real cursor history comes
  from the OS).

  The bezier path generator uses two perturbed control points (15-35%
  off the straight line) so traces don't look mechanical.

- **Extended stealth_init.js patches (8 new evasions)**:
  - `chrome.runtime.id` defined (real Chrome has it)
  - AudioContext fingerprint noise injection
  - `speechSynthesis.getVoices()` returns a plausible non-empty list
  - `navigator.getBattery()` resolves a real BatteryManager-shape object
  - WebGL2 vendor/renderer masking (parity with WebGL1)
  - `MediaDevices.enumerateDevices()` returns generic mic+camera+speaker
  - `outerWidth`/`outerHeight` patched when 0 (headless tell)

- `SyncCDPPage.send(method, params)` — public sync wrapper for raw CDP
  commands. Required by `humanize_page()`, `recipe.capture_download`,
  and any user code that needs `Input.dispatchMouseEvent` etc.
- 12 new unit tests in `test_humanize.py`

### Empirical findings

`UPGRADE-PLAN-v0.26.0.md` updated with dogfood results: humanize +
extended stealth init still doesn't defeat **Akamai BMP on war.gov-tier
targets**. That engine fingerprints at the TLS/transport layer (HTTP 403
on the initial GET, before any page JS runs), so JS-level evasions can't
help. `--headed` remains the recommended setting for those sites until
v0.27.0+ adds TLS-handshake-level mitigations. Humanize **does** help on
sites that gate on post-load JS — most cf-non-Akamai bot detection.

### Fixed

- **`RuntimeWarning: coroutine '_AsyncCDPClient.close' was never awaited`**
  no longer fires on cleanup. The CDPClient now tracks a `_closed`
  flag (idempotent close), and `_run()` cleanly cancels coroutines
  scheduled against an already-stopped event loop instead of letting
  Python's GC complain. Closes a long-standing cosmetic warning that
  surfaced any time a scrape hit an exception or when wrappers double-
  closed the client

## [0.25.1] - 2026-05-09

### Added

- **Recipe step `for_each`** — iterate sub-steps over a CSS-selector list,
  with `@current` placeholder substitution targeting the current iteration's
  element (selector-based, position-stable). Bounded by `max:` to keep
  runs bounded:

      - for_each:
          selector: "[data-record-trigger]"
          max: 200
          steps:
            - click: "@current"
            - wait_for: ".modal"
            - click: ".modal-download"
            - press: Escape
            - wait: 500ms

- **Recipe step `capture_download`** — sets `Page.setDownloadBehavior` to
  allow downloads to land in the configured directory. Click the trigger
  button in a subsequent step:

      - capture_download:
          to: ./pdfs/
      - click: "button.download"

- **`scrape --then-fetch-organize-by`** flag — sub-categorise downloads
  into directories. Modes: `flat` (default), `extension`
  (`pdfs/`, `images/`, `videos/`, `docs/`, `other/`), `content-type`
  (`image/`, `video/`, `application/`, `audio/`, `other/`), or
  `thumbnail` (war.gov-style: pulls URLs containing `/thumbnail/` into
  a separate `thumbnails/` subdir, falls through to extension classes
  for everything else).
- New `_classify_url_for_organize()` helper exposed in the CLI module
- Working recipe in `examples/recipes/war-gov-uap.yml` now demos
  `for_each`-style flows
- AGENTS.md: comprehensive update for all v0.23.0–v0.25.0 features
  (hard-target stack, recipe section, optional extras list)
- `docs/UPGRADE-PLAN-v0.26.0.md` — planning doc for headless evasion
  (synthetic interaction + extended stealth patches)

### Fixed

- **Windows console crash on Unicode output.** `_output_text()` previously
  called `print()` which crashes on cp1252 consoles when scraped content
  contains characters like primes (`′`), em dashes, smart quotes, or fancy
  bullets. Now writes via `sys.stdout` with `errors="replace"` fallback —
  printable approximation lands on the console, full content goes to
  `--output` files unaltered

## [0.25.0] - 2026-05-09

### Added — Productivity layer

Stops users from writing 40-line wrappers around `flarecrawl scrape`.

- **YAML interaction recipes** (`flarecrawl recipe <path.yml>`).
  Declarative multi-step browser flows. Step kinds: `click`, `fill`,
  `press`, `wait`, `wait_for`, `eval`, `capture`, `screenshot`,
  `get_content`. Resume support via journal file
  (`.recipe-state-<hash>.ndjson`); `--resume` skips already-completed
  steps. `--dry-run` validates and prints the step plan.

  Recipe format (v1):

      version: 1
      goto: https://example.com
      browser: local
      headed: true
      steps:
        - wait_for: ".loaded"
        - capture:
            pattern: "*.csv,*.json"
            to: ./out/
        - click: "[data-action]"
        - wait: 500ms

  Optional dep: `pip install flarecrawl[recipes]`
- **yt-dlp passthrough on `videos`** (`--yt-dlp`). After DOM-based
  discovery, run candidate URLs (YouTube, Vimeo, DVIDS, TikTok, Twitch,
  Wistia, Loom, etc. — 16 host families) through yt-dlp's extractor
  registry. Resolves provider-specific embeds that DOM scraping can't
  unwrap (e.g. DVIDS `iframe[src=...]` → direct mp4 URL with auth).
  `resolve_via_yt_dlp()` exposed at the Python level. Optional dep:
  `pip install flarecrawl[videos]`
- **Auto data-discovery** (`--auto-data` / `--no-auto-data`, on by
  default). When CDP is in use, passively detects structured-data
  responses (CSV, JSON, XLSX, YAML, XML) the page fetched on init —
  without downloading their bodies. Emits `meta.data_sources` array
  on every scrape result. Same-origin filter on by default.
  `DataSourceProbe` exposed at the Python level
- 23 new unit tests across `test_recipe.py`,
  `test_videos_yt_dlp.py`, and `test_data_source_probe.py`
- New optional extras: `recipes` (PyYAML), `videos` (yt-dlp)

### Changed

- `_AsyncCDPPage.enable_network()` accepts a `data_probe: DataSourceProbe`
  arg in addition to `body_capture`. Both can be passed simultaneously
- `extract_videos(html, base_url, *, use_yt_dlp=False)` gains the
  `use_yt_dlp` keyword arg

## [0.24.0] - 2026-05-09

### Added — Capabilities for hard targets

This release closes the gap between flarecrawl and bespoke
Playwright + curl_cffi + yt-dlp wrappers for heavily-defended SPAs.
Dogfood: scraping the war.gov UAP disclosure page (162 records hidden
behind a JS-rendered modal flow) goes from "must drop into Python" to a
single CLI invocation.

- **Response body interception** (`--capture-pattern`, `--capture-dir`).
  Save subresources fetched by JS during page load (CSV, JSON, XHR
  payloads). Pattern is comma-separated fnmatch globs; optional
  `--capture-content-type` for MIME filtering. Auto-promotes to `--cdp`.
  The dogfood case (war.gov's 185 KB `uap-csv.csv`) drops to:
  `flarecrawl scrape https://www.war.gov/UFO/ --js --browser local
  --headed --capture-pattern uap-csv.csv --capture-dir ./out/`
- **Stealth init script auto-applied via CDP** (`Page.addScriptToEvaluateOnNewDocument`).
  Patches `navigator.webdriver`, `window.chrome`, plugins, languages,
  WebGL vendor/renderer, hardware concurrency, etc. — the fingerprints
  Cloudflare Bot Management / DataDome / Akamai BMP / PerimeterX
  commonly check. Idempotent. Fails open if asset is missing
- **Local Chromium backend** (`--browser local`, `--headed`). Spawns a
  Playwright-managed Chromium with `--remote-debugging-port`, exposes
  its CDP WebSocket via the existing `FLARECRAWL_CDP_ENDPOINT`
  override, and tears down on scrape exit. Bypasses the 293-byte stub
  CF Browser Rendering returns on hard targets. Optional dep:
  `pip install flarecrawl[local-browser]`. Headed mode (`--headed`) is
  the recommended setting for war.gov-class targets where headless is
  detected
- **`--then-fetch` flow** for cookie-handed-off mass downloads. Pulls
  cookies from the live CDP session and parallel-downloads a list of
  URLs via `curl_cffi` (Chrome 131 TLS impersonation):
  - `--then-fetch URL1,URL2,...` — inline list
  - `--then-fetch-from FILE` — one URL per line
  - `--then-fetch-from FILE --then-fetch-column "Col Name"` — CSV column
    extraction (handles spaces and special chars in column names)
  - `--then-fetch-output DIR` — destination
  - `--then-fetch-workers N` — parallel workers (default 4)
  Resume-safe: existing files with non-zero size are skipped
- `BodyCapture` and `LocalBrowser` exposed at the Python API level for
  programmatic use
- `stealth` and `local-browser` optional extras in `pyproject.toml`
- 18 new unit tests across `test_body_capture.py` and `test_then_fetch.py`

### Changed

- `_AsyncCDPPage.enable_network()` now accepts an optional
  `body_capture: BodyCapture` arg. The synchronous wrapper
  `SyncCDPPage.enable_network(body_capture=...)` is the public path
- `apply_stealth()` is invoked automatically before navigation in CDP
  scrape mode. Best-effort: never fails the scrape if stealth init
  encounters issues

### Known limitations

- Headless local Chromium (`--browser local` without `--headed`) still
  gets blocked by Akamai BMP on war.gov-tier targets. Use `--headed`
  for those. Improving headless evasion is on the v0.25.0+ roadmap

## [0.23.0] - 2026-05-09

### Fixed

- **CDP keep_alive rejected with HTTP 400.** Cloudflare's Browser Rendering CDP changed its `keep_alive` query parameter to require milliseconds (was seconds), with a 10-second minimum. flarecrawl was sending raw seconds, so any `--keep-alive`, `--interactive`, `--live-view`, `--record`, `--save-cookies`, or `--load-cookies` invocation rejected at the WebSocket handshake. CDP now converts seconds → milliseconds internally and clamps below 10 s up to the minimum
- `flarecrawl fetch` previously caught `httpx.HTTPError` even though `httpx` wasn't imported in `cli.py`, raising a `NameError` from inside the exception handler when any HTTP error occurred. Now imports lazily

### Added

- `CDPAuthError` (exit code 2) and `CDPTierError` (exit code 5) distinguish 401/403/404 WebSocket failures from generic connection errors. Surface a one-line actionable message instead of a stack trace
- `flarecrawl fetch --stealth` now actually works for binary downloads. Routes through `curl_cffi` with Chrome 131 TLS impersonation, defeating JA3/JA4 bot detection on sites like war.gov that previously returned 403 to direct fetches. The flag was previously declared but ignored on the binary path
- `flarecrawl fetch --paywall` flag — implies `--stealth` for binaries (the paywall cascade is HTML-oriented; the stealth tier within it is what binaries need)
- `flarecrawl fetch --impersonate <profile>` — choose curl_cffi browser profile (`chrome131`, `chrome120`, `safari17`, etc.). Default `chrome131`
- `download_binary_stealth()` in `flarecrawl.fetch` for programmatic use
- `cacheable_response()` predicate in `flarecrawl.cache` — skips persisting empty bodies, non-200 responses, and HTML/markdown stubs under 1 KB. Prevents the "cached error masquerading as real content for an hour" footgun
- `cache.put(..., allow_empty=True)` opt-in flag preserves the legacy behaviour for callers that intentionally want to cache empty responses

### Changed

- `--js-eval` now auto-promotes `scrape` to `--cdp` mode. Without CDP the REST `/scrape` endpoint silently dropped the eval return value; the new behaviour matches `--interactive`, `--live-view`, `--record`, etc. A dim notice prints in non-`--json` mode
- `detect_content_type()` accepts `stealth=True` to route the HEAD probe through curl_cffi too — needed for sites that fingerprint TLS even on HEAD requests
- `cache.put()` now returns `bool` (was `None`) — `False` indicates the response was rejected by the gating predicate. Callers that ignore the return value see no behavioural change

### Security

- API tokens with insufficient permissions for CDP (Browser Rendering Edit) now surface a clear "permission required" message instead of leaking the WebSocket failure trace

## [0.22.2] - 2026-04-21

### Fixed
- Type-safety holes in `credentials.py` flagged by Pyright: `keyring` symbol now correctly bound to `None` in the `ImportError` fallback (was undefined), and `_json` local was unused/inconsistent — replaced with module-level `import json`. Runtime behaviour unchanged

## [0.22.1] - 2026-04-21

### Fixed
- `auth status` now eagerly migrates **both** credentials from legacy config.json on first call. Previously only `account_id` migrated (the one fetched for the masked display) while `api_token` stayed in plaintext until some other command happened to need it. Status would misleadingly report `source: keyring` while half the credentials were still on disk

## [0.22.0] - 2026-04-21

### Added
- `src/flarecrawl/credentials.py` with `CredentialStore` (env -> keyring -> .env -> legacy config.json priority)
- `secure` optional extra: `pip install flarecrawl[secure]` enables OS keyring storage (Forma protocol §07)
- `keyring_available` field in `auth status` JSON output

### Changed
- **BREAKING (minor)**: `auth status --json` `source` field now returns `keyring | environment | dotenv | config-legacy | none` (was: `environment | config | none`). Scripts checking for the literal `"config"` need to handle `"config-legacy"` instead
- Legacy plaintext credentials in `~/.config/flarecrawl/config.json` are auto-migrated to keyring on first read after upgrade. Usage tracking and session data in the same file are preserved

### Security
- API tokens no longer stored in plaintext when `keyring` is installed
- Migration is one-shot and idempotent — re-running has no effect

## [0.21.0] - 2026-04-20

### Added

- `--browser-cookies chrome|firefox` flag on `scrape`, `interact`, `design extract` (parity with `videos`)
- `--session` flag on `crawl` for authenticated crawls (was missing)
- Live test suite for design extraction (`tests/live/test_design_live.py`)
- `frontier` listed in `[tool.forma].resources`; `status = "experimental"` field

### Fixed

- `--ignore-robots` on `crawl` no longer silently fails — prints warning pointing at `spider`/`authcrawl` (CF `/crawl` API has no robots bypass parameter)
- Design extract file writes now use UTF-8 encoding (fixes UnicodeEncodeError on Windows from block chars in coherence bars)

## [0.20.0] - 2026-04-20

### Added

- `_enrich_cdp_error()` helper that detects known CDP failure patterns (bot detection, timeouts, redirects, network errors, WebSocket issues, auth failures) and appends actionable `Suggestions:` block with CLI flags to try
- Applied CDP error enrichment at all CDP call sites: scrape, interact, design extract, design coherence, design diff, and videos commands
- 18 unit tests for CDP error enrichment

### Changed

- CHANGELOG.md restored as source of truth — README Recent Updates trimmed to last 5 releases

## [0.19.0] - 2026-04-19

### Added

- `flarecrawl videos` command — discover video URLs on web pages (mp4, webm, m3u8, YouTube, Vimeo embeds, OpenGraph `og:video`, JSON-LD `VideoObject`)
- Video discovery across 21 platforms including YouTube, Vimeo, Dailymotion, Twitch, Wistia, Brightcove, Vidyard, Loom, and more
- `--export-cookies` flag for yt-dlp Netscape cookie format export
- `--browser-cookies chrome|firefox` flag for local browser cookie extraction via rookiepy
- `--download` and `--download-dir` flags for direct yt-dlp integration
- `--depth N` for multi-page video discovery
- `spider` command alias for `authcrawl`

### Fixed

- 7 broken `console.print(err=True)` calls corrected to `console.print(..., stderr=True)`

## [0.18.0] - 2026-04-18

### Fixed

- Path-traversal vulnerability on `--resume JOB_ID` — job IDs are now sanitised
- Non-http(s) URLs (`file:`, `javascript:`, `data:`) blocked from entering the crawler
- Robots.txt and sitemap downloads capped to prevent hostile server OOM

### Changed

- Crawl loop refactored to ~half its previous size
- PEP 561 `py.typed` marker added for downstream type hint support
- 1027 tests

## [0.17.0] - 2026-04-18

### Added

- `flarecrawl authcrawl` command — industrial-scale authenticated BFS crawler for millions of URLs across tens of thousands of domains
- `--resume JOB_ID` to resume interrupted crawls, picking up exactly where you left off
- `--refresh-days N` weekly-refresh mode — only re-fetch pages changed since last run (ETag/Last-Modified)
- Fair round-robin scheduling across domains
- `--adaptive-delay` automatic politeness tuning based on server response times
- Auto-retry with exponential backoff and dead-letter inspector (`flarecrawl frontier dead-letter JOB_ID`)
- Circuit breaker — pauses domains after 10 consecutive failures
- Robots.txt compliance via protego
- `--tracing console|json|otlp` OpenTelemetry tracing for production observability
- URL canonicalisation with tracking-param stripping (`?utm_source=...` deduplication)

### Changed

- selectolax parser (10-30x faster than BeautifulSoup on hot paths), orjson, uvloop, tuned httpx pool — typical crawls 2-5x faster end-to-end
- 967 tests

## [0.16.0] - 2026-04-17

### Added

- `flarecrawl design extract` — generates DESIGN.md from any website with colors, typography, spacing, shadows, radii, layout, CSS variables, media queries, z-index
- 9-category Design Coherence scoring (A-F grades)
- `flarecrawl design coherence` for standalone scoring
- `flarecrawl design diff` for side-by-side design comparison
- HTML preview with visual swatches (`--preview`)
- `--session` support for authenticated design extraction
- CDP-backed live computed style extraction

## [0.15.0] - 2026-04-17

### Added

- `flarecrawl webmcp discover` and `flarecrawl webmcp call` for structured tool discovery on WebMCP-enabled sites
- `flarecrawl interact` command with `--fill`, `--click`, `--select` and human-like timing (Bezier mouse curves, variable keystroke delays)
- `flarecrawl cdp connect` — prints WebSocket URL for Playwright/Puppeteer integration
- `FLARECRAWL_CDP_ENDPOINT` env var for custom CDP backends (Oxylabs, Bright Data, local Chrome)
- `--tabs` flag for multi-URL session reuse
- `--stagehand` stub for future AI element finding
- Free tier warnings when approaching daily limits

### Fixed

- Live View URLs corrected to use `live.browser.run` hosted UI
- Session listing/close now uses real CF REST API
- Recording retrieval via `/recording/{session_id}`
- `keep_alive` capped at 600s (CF maximum)
- 740 tests

## [0.14.1] - 2026-04-16

### Added

- CDP WebSocket integration — `--cdp` flag for persistent browser sessions via Chrome DevTools Protocol
- `--interactive` human-in-the-loop auth flow (login in DevTools, cookies auto-saved)
- `--live-view` real-time browser debugging via Chrome DevTools
- Proper `--js-eval` via `Runtime.evaluate` (replaces addScriptTag hack)
- Real `--har` network capture via `Network.enable`
- `--record` session recordings (rrweb format)
- `--keep-alive N` persistent sessions with cross-invocation reuse
- `--save-cookies`/`--load-cookies` for authenticated scraping
- `--ignore-robots` on crawl
- `flarecrawl cdp sessions` and `flarecrawl cdp close` session management commands

### Changed

- Workers max increased from 10 to 50 (CF now supports 120 concurrent browsers)
- Rebranded to Cloudflare Browser Run
- 723 tests

---

## v0.14.0 — 2026-04-16

### New Modules

- **`cookies.py`** — Cookie file loading with auto-detection of Puppeteer JSON, Chrome DevTools export, and Netscape text format. Includes `cookies_to_httpx()`, `cookies_to_header()` (domain-filtered), and `validate_cookies()` (HEAD request test).
- **`fetch.py`** — Content-type aware downloading. `detect_content_type()` does a HEAD probe, `download_binary()` streams large files with chunk-based progress, `build_session()` constructs an authenticated `httpx.Client`. `ContentInfo` and `DownloadResult` dataclasses.
- **`openapi.py`** — OpenAPI/Swagger spec discovery. `discover_specs()` finds specs from `<a>` links, SwaggerUI `<script>` configs, and `<link>` tags. `probe_common_paths()` HEAD-checks `/swagger.json`, `/openapi.json`, `/openapi.yaml`, and 7 other common paths. `validate_spec()` checks for `openapi`/`swagger` top-level keys and counts endpoints. `download_spec()` fetches and validates.
- **`authcrawl.py`** — Authenticated BFS crawler. `AuthenticatedCrawler` carries cookies through every request, respects `max_depth`/`max_pages`, supports `include_patterns`/`exclude_patterns` (regex or substring), yields `CrawlResult` async iterator. Uses asyncio semaphore (same pattern as `batch.py`).

### New CLI Commands

- **`flarecrawl fetch URL`** — Content-type aware fetch. HTML → markdown (via CF), binary → stream download, JSON → pretty-print. Supports `--session FILE`, `--session @NAME`, `--auth`, `--headers`, `--output`, `--stealth`, `--proxy`, `--overwrite`, `--json`. Rich progress bar for files > 1 MB.
- **`flarecrawl openapi URL`** — Discover and optionally download OpenAPI/Swagger specs. Flags: `--download/-d`, `--output/-o`, `--probe`, `--session`, `--json`.
- **`flarecrawl session save NAME --file FILE`** — Save cookies to a named session.
- **`flarecrawl session list`** — List saved sessions.
- **`flarecrawl session show NAME`** — Show cookies in a saved session.
- **`flarecrawl session delete NAME`** — Delete a saved session.
- **`flarecrawl session validate NAME URL`** — HEAD-test cookies against a URL.

### Enhancements

- **`discover --openapi`** — New flag that additionally probes for OpenAPI specs and includes them in the JSON output under `meta.api_specs`.
- **`scrape --session`** — Refactored to use `cookies.py` instead of inline JSON parsing; now supports Netscape format and Chrome DevTools exports in addition to Puppeteer arrays.
- **`config.py`** — `get_sessions_dir()`, `save_session()`, `load_session()`, `list_sessions()`, `delete_session()` session persistence functions.

### Tests

- `tests/test_cookies.py` — Format loading, conversion, domain filtering (26 tests)
- `tests/test_fetch.py` — Content-type detection, filename derivation, build_session (20 tests)
- `tests/test_openapi.py` — Spec discovery from HTML, validation logic (18 tests)
- `tests/test_authcrawl.py` — Config defaults, URL filtering, depth limits (17 tests)

---

## v0.13.0 — 2026-04-14

Performance: optimize sanitise pipeline — 51% faster via keyword pre-checks. Extend `--agent-safe` with 7 new attack vector sanitisers (v0.12.1).

## v0.12.1 — 2026-04-06

Extended `--agent-safe` attack vector coverage: hidden iframes, hidden form inputs, CSS class hiding, meta tag injection, homoglyph evasion (Cyrillic/Greek), markdown exfiltration detection, HTML entity evasion. 13 sanitisers total, 61-file corpus, 564 tests.

## v0.12.0 — 2026-04-06

`--agent-safe` flag for adversarial content sanitisation.

## v0.11.0 — 2026-04-03

`search` command (Jina Search), `--proxy` flag, `--clean`, per-site YAML rulesets, 378 tests.

## v0.10.0 — 2026-04-02

Enhanced content extraction (`--paywall`), stealth mode (`--stealth`), 343 tests.

## v0.9.0 — 2026-03-26

Markdown content negotiation, domain capability cache, 278 tests.

## v0.8.0 — 2026-03-20

`--scroll`, `--query`, `--precision`/`--recall`, `--deduplicate`, `--session`, `flarecrawl batch`, 215 tests.
