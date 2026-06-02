# Flarecrawl v0.25.x → v0.26.0 — Headless evasion

> Status: **partially shipped in v0.26.0** (humanize landed). H1 confirmed
> useful for behavioural-fingerprint engines that gate on *post-navigation*
> JS signals. H1 does **not** defeat Akamai BMP on war.gov: that engine
> rejects at the TLS/transport layer (HTTP 403 on the initial GET, before
> any page JS runs). For Akamai-tier targets, `--headed` remains the only
> reliable escape until v0.27.0+ adds TLS-handshake-level mitigations.

## Problem

`--browser local` (v0.24.0 P2.2b) bypasses Cloudflare's hosted-Chromium
fingerprint, but headless Chrome still gets caught by Akamai BMP / DataDome
/ PerimeterX behavioural checks. The current escape hatch — `--headed` —
works but blocks CI use, hogs a desktop, and hits ~50% slower throughput.

The dogfood data point: war.gov serves a 403 Akamai page to headless
Chromium with our v0.25.0 stealth_init.js applied. The same browser with
`--headed` succeeds.

## Goals

1. Make the *default* behaviour for `--browser local` succeed on
   war.gov-tier targets
2. Keep CI compatibility (no real display required)
3. No new external services / proxies — local-only

## Non-goals

- Becoming undetectable on every site forever (cat-and-mouse problem)
- Replacing the hosted CF Browser Rendering — keep `--browser cf` as the
  fast path for friendly sites

## Hypothesis tree (validate in order)

### H1 — Behavioural fingerprint, not static fingerprint

The page passes basic JS feature checks (we already patch `webdriver`,
plugins, WebGL, etc. via `stealth_init.js`) but fails on **interaction
signals**: no mouse movement, instant click after navigate, no touch
events, no scroll, no idle time. Headed mode "passes" because the user
moves the mouse / scrolls naturally.

**Test:** before clicking, run a synthetic-interaction routine — random
mouse moves over a few hundred ms, a couple of small scrolls, a brief
idle. If headless then succeeds on war.gov, H1 is the root cause.

**Plan-of-attack if true:**

- New step `pre_navigation_humanize` injected automatically when
  `--browser local` is set without `--headed`. Internally:
  - Mouse move with bezier-curve interpolation (200-400ms each)
  - Two small scrolls
  - Random 500-1500ms idle gaps
- Total cost: ~2-4s added to every scrape. Worth it for the headless ROI.

### H2 — Specific Chromium-headless tells we missed

Even with `stealth_init.js`, Chromium-headless leaks differ from real
Chrome in ~30 places not yet patched. Worth a sweep:

- `chrome.runtime.id` undefined in headless
- `chrome.app` properties differ
- `Notification.maxActions` differs
- `outerHeight - innerHeight` ratio is off in headless
- AudioContext fingerprint
- Battery API (deprecated but checked)
- Speech synthesis voices count
- WebRTC IP enumeration leaks LAN IPs
- Fonts list (headless has a smaller default set)
- ScreenY / availTop tells

**Test:** run [creepjs](https://abrahamjuliot.github.io/creepjs) /
[bot.sannysoft.com](https://bot.sannysoft.com) in our local-headless
browser and diff the output against the same browser headed. Patch
each delta in `stealth_init.js`.

**Plan-of-attack:**

- Adopt the upstream `playwright-stealth` patch set (MIT) wholesale —
  or vendor `puppeteer-extra-plugin-stealth`'s 17 evasions. Tradeoff:
  ~30KB JS payload added to every CDP scrape, but exhaustive coverage.
- A/B test: ours vs upstream against creepjs to validate.

### H3 — TLS fingerprint mismatch (ja3/ja4)

The Chromium-headless TLS fingerprint differs from desktop Chrome in
subtle ways (cipher order, extension order). Less likely than H1/H2
because Chromium uses BoringSSL identically headless or not, but worth
ruling out.

**Test:** capture TLS handshake from headless and headed local browser
to <https://tlsfingerprint.io>. Compare ja3/ja4 hashes.

**Plan-of-attack if differs:** no clean fix in Chromium itself; would
have to route requests through a curl_cffi-impersonated proxy. Costly.

### H4 — `--headless=new` mode is detectable as a separate fingerprint

We currently launch with `--headless=new`. There's a reasonable chance
that flag itself is detectable (some sites check `navigator.userAgent`
for "HeadlessChrome", which `--headless=new` removes, but other tells
remain).

**Test:** launch with `--headless=old` and `--headless=chrome` (the
deprecated alias) and re-test. Sometimes older modes have been better
patched by stealth scripts because they're the historical target.

## Phasing

### v0.26.0 (the realistic ship)

- Phase 1: H1 implementation — synthetic interaction before any meaningful
  click, on by default with `--browser local`. Backable behind
  `--no-humanize`.
- Phase 2: H2 audit — vendor `playwright-stealth` JS, run creepjs
  comparison test as part of `tests/live/`.
- Phase 3: Document recommended environments in `docs/HARD-TARGETS.md`
  ("if `--no-humanize` is set, you'll likely need `--headed`; for CI use,
  pair `--browser local` with the default humanize").

### v0.27.0+ (out of scope unless v0.26.0 isn't enough)

- Xvfb integration helper for true-headed-in-CI: `--xvfb` flag spawns
  Xvfb, sets DISPLAY, runs the browser headed, tears down on exit.
  Linux-only, requires `xvfb` package on the host.
- Audio context spoofing
- WebRTC IP leak protection

## Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| H1 humanize adds visible latency | High | Make pause durations configurable; default to "fast humanize" (~1s total) |
| Vendoring playwright-stealth bloats install | Medium | Stays inside `[local-browser]` extra; non-extra users unaffected |
| Cat-and-mouse: new evasion → new detection | Cert. | Document that flarecrawl is a tool, not a guarantee. Encourage `--browser local --headed` as fallback |
| Synthetic-interaction triggers actual page actions (clicks the hero CTA, etc.) | Med | Restrict mouse moves to neutral coordinates (top-left margin, then center idle) |

## Tests

- `tests/test_humanize.py` — unit-test the bezier path generator
- `tests/live/test_war_gov_headless.py` — gated live test that
  asserts headless `--browser local` returns >50KB on war.gov.
  Run weekly in CI to detect regressions in either direction.

## Definition of done

- [x] `humanize` module + auto-on for headless local → shipped
- [x] H2 extended stealth_init.js patches → shipped
- [x] All v0.25.x tests still green (727 passing)
- [x] Unit tests for humanize bezier + dispatch + budget → 12/12 passing
- [ ] **Not achieved:** war.gov UAP capture without `--headed`. Empirical
      finding: Akamai BMP rejects at the TLS/transport layer before any
      page JS runs, so humanize cannot help. Tracked for v0.27.0+ as a
      separate "TLS handshake mitigations" workstream
- [x] No regression in scrape time for friendly sites — humanize is
      opt-in (auto-on only when `--browser local && !--headed`)

## Carry-over to v0.27.0+

- TLS handshake spoofing on the local Chromium (Akamai-tier evasion).
  Options:
  - Wrap the CDP socket through a curl_cffi-impersonated MITM proxy
  - Patch BoringSSL build flags in a vendored Chromium
  - Use a pre-vendored "anti-detect" Chromium fork (undetected-chromium-driver)
- Xvfb integration helper for true-headed-in-CI: `--xvfb` flag
- Audio context fingerprint randomisation per-context (currently per-buffer)
- Font list expansion in headless
