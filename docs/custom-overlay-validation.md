# Custom Overlay Validation — 2026-06-02

End-to-end audit of every fingerprint in
`src/flarecrawl/wappalyzer_data/custom_fingerprints.json`. Two validation
passes were run:

1. **Synthetic** — for each pattern, build a minimal HTML/header/cookie
   fixture that should match, then assert
   `WappalyzerClient.analyze()` fires the tech. Script:
   `tests/validate_custom_overlay.py`.
2. **Live** — `flarecrawl tech-detect <url>` (HTTP path) and
   `flarecrawl scrape <url> --tech-detect --js` (CDP/Browser Run path,
   with `--wait-until domcontentloaded`) against the vendor's own
   marketing domain. Scripts: `tests/_probe_vendor_sites.py` (HTTP) and
   `tests/_probe_vendor_sites_cdp.py` (CDP).

The CDP path uses Cloudflare Browser Run via CDP — the same code path
that production users hit — rather than local Playwright. That's
intentional: the upstream `tech-detect --render` flag is a
local-Playwright shortcut for development; live validation should
exercise the production rendering path.

## Headline numbers

| Cohort                                | Count |
| ------------------------------------- | ----: |
| Total custom-overlay tech entries     |   104 |
| Synthetic — all signals fire          |   102 |
| Synthetic — partial signals fire      |     2 |
| Synthetic — no signals fire           |     0 |
| Live (HTTP) — vendor brand self-embeds|    35 |
| Live (CDP)  — vendor brand self-embeds|     0 of 19 NO-HTTP retries |
| Live — vendor doesn't self-embed      |    18 |
| Live — CDP errored (timeout/conn)     |     2 |

The 18 + 2 = 20 fingerprints where the vendor's brand site does not
self-embed have been annotated in `custom_fingerprints.json` with an
`_overlay_note` field documenting the negative result; the patterns
themselves are unchanged because there is no evidence they are wrong —
only that a brand domain isn't the right surface to verify them. Each
note explicitly TODOs adding a known customer site to the bench corpus
to lock the pattern in.

## Structural bugs found and fixed

### Bug 1 — overlay-merge type-mismatch silently drops list-form `dom`

`WappalyzerClient._load()` previously only handled list+list and
dict+dict merges; an overlay declaring `"dom": ["sel"]` against an
upstream tech with `"dom": {"sel": {...}}` fell through to the
`key not in existing` branch and was **silently dropped**.

This affected **SevenRooms**, one of the four "verified" overlay
fingerprints called out in the v0.5 release notes. The overlay's
`a[href*='sevenrooms.com/reservations']` and
`iframe[src*='sevenrooms.com']` selectors never reached the engine; the
"verification" was actually firing only through the html/scriptSrc
patterns. A customer site that links to /reservations without the
`.sevenrooms.com/` iframe was being missed.

Fix: in `wappalyzer.py:117-148` the merge now promotes overlay
list-form selectors to `{selector: {}}` before dict-merging. Regression
test: `tests/test_wappalyzer.py::test_custom_overlay_list_dom_merges_into_upstream_dict_dom`.

### Bug 2 — duplicate top-level JSON keys silently collapse

JSON doesn't forbid duplicate object keys but Python's `json.load`
keeps only the last occurrence. The overlay file declared
`"Bokun": {...}` twice (lines 255 and 693) and `"ATDW": {...}` twice
(lines 196 and 1024). The earlier entry's patterns were silently lost.

For Bokun this meant losing the `widget.bokun.io`, `bokun.io`, and
`bokuncdn.com` scriptSrc patterns. For ATDW it meant losing the
`atdw-online.com.au/` scriptSrc and `data-atdw-` html marker, in
exchange for keeping only the pixel/redirect html patterns from the
second entry.

Fix: merged each pair into a single entry holding the union of
patterns. Regression test:
`tests/test_wappalyzer.py::test_custom_overlay_no_duplicate_top_level_keys`
checks the file structurally using `json.loads(..., object_pairs_hook=...)`
so the same hazard can't reappear.

## Bench corpus extension

`tests/bench_tech_detect.py` gained 12 new entries — one per vendor
whose brand domain demonstrably self-embeds its own widget at the
HTTP layer with a clean detection profile (≤12 unrelated techs). The
12 act as regression guards for the corresponding overlay
fingerprints; any future pattern tightening that drops them will fail
the bench.

Picked: Beds24, Bopple, Channex, Peek Pro, RedBalloon, Resy,
Simpleview CMS, Windcave, Quandoo, Triptease, FareHarbor, Rezdy. The
remaining ~23 OK sites are good follow-up candidates if/when the
corpus is sized up further.

## Vendor brand sites that do NOT self-embed

Annotated with `_overlay_note` for visibility in the JSON file. None
of these are pattern bugs in the testable sense; the vendor simply
runs a marketing/sales site that doesn't itself use the widget.

Adrenaline, ATDW, Bento, Bokun, Cobber, Eventbrite, Experience Oz,
Localis, Mews, Mr Yum, Nabooki, NewBook, OpenTable, SevenRooms,
SiteMinder, Square Online, STAAH, TheFork, Tock, Ventrata.

For each of these, the right next step is to find a known customer
site that uses the widget and add it to the bench corpus.

## Synthetic-validator caveats

`tests/validate_custom_overlay.py` builds fixtures by inverting each
regex. The inverter handles the patterns actually used in the overlay
but is not a general inverse — two of 104 entries (Tailwind CSS, two
of its four nested `\b...\b[^"]*\b...\b` html patterns; ResDiary, one
dom selector after the upstream merge) come back as "partial fire"
because the synthesised string doesn't quite satisfy the original
regex. Both have other patterns that do fire synthetically and both
have evidence of firing on real sites, so the partial reports are
inverter limitations, not real pattern bugs.

## Reproducing this audit

```bash
# Synthetic — runs against the loaded overlay, no network
uv run python tests/validate_custom_overlay.py

# Live HTTP probe — ~3 min, no CF cost
uv run python tests/_probe_vendor_sites.py > tests/vendor_probe.json

# Live CDP probe (only re-probes sites the HTTP probe missed) — ~5-10 min
# Each call consumes one CF Browser Run session; ~20 sessions total.
uv run python tests/_probe_vendor_sites_cdp.py > tests/vendor_probe_cdp.json

# Bench — regression guard for the curated corpus
uv run python tests/bench_tech_detect.py
```

The three probe scripts (`validate_custom_overlay.py`,
`_probe_vendor_sites.py`, `_probe_vendor_sites_cdp.py`) are not part
of the pytest run; they are operator tools for the next audit pass.
