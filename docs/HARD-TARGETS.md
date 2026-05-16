# Scraping hard targets

A "hard target" in flarecrawl shorthand is a site that:

- Returns a 293-byte HTML stub to the default `--js` scrape (Cloudflare
  Browser Rendering's Chromium gets fingerprinted)
- Blocks direct `httpx` / `curl` fetches with HTTP 403 (TLS handshake
  fingerprinted via JA3/JA4)
- Hides its data layer in JS app state — no `<a href>` links, no
  XHRs you can extract from the rendered HTML

The motivating example is [war.gov/UFO/](https://www.war.gov/UFO/) — the
US DoW UAP disclosure page, where 162 records live behind a JS-driven
modal and the master data file is fetched on init. None of flarecrawl's
default REST-mode commands could see the data; it took a hand-rolled
Playwright + curl_cffi + yt-dlp wrapper to download. v0.23.0–v0.25.0
collapses that wrapper to a single CLI invocation.

## The four-step formula

```bash
flarecrawl scrape https://www.war.gov/UFO/ \
  --js --browser local --headed \
  --capture-pattern "uap-csv.csv" \
  --capture-dir ./out/csv/ \
  --then-fetch-from ./out/csv/uap-csv.csv \
  --then-fetch-column "PDF | Image Link" \
  --then-fetch-output ./out/files/ \
  --then-fetch-workers 8
```

What each flag is doing:

| Flag | Layer | Why |
|------|-------|-----|
| `--js` | Render | Drive a real Chromium so JS app state populates |
| `--browser local` | Bypass CF stub | Run Chromium on this machine, not Cloudflare's |
| `--headed` | Bypass headless detection | Akamai BMP / DataDome flag headless |
| `--capture-pattern` | Data discovery | Save the CSV the page fetches on init |
| `--capture-dir` | Output | Where the captured body lands |
| `--then-fetch-from --then-fetch-column` | Mass download | Read URLs from the captured CSV |
| `--then-fetch-output` | Output | Where files land |
| `--then-fetch-workers` | Parallelism | curl_cffi thread pool |

The downloads inherit the cookies the headed browser established during
the initial scrape (anti-bot challenge solved once, reused N times) and
TLS-impersonate Chrome 131 (defeats JA3/JA4 fingerprinting on the
download path).

## When the four-step formula isn't enough: P6 (mint → replay)

The formula above works when one headed navigation clears the wall and
the data is reachable from that same browser session. It breaks down on
targets where:

- the shells expire mid-harvest (you need to **re-mint** partway through)
- the real data is a non-WAF'd API you want to hit directly with the
  minted jar (not re-drive a browser per request)
- sustained replay pressure escalates the **whole egress IP** (Akamai),
  so a tight re-mint-on-every-block loop just keeps you flagged
- the wall is a terminal Cloudflare 1020 (keyed on the egress, not the
  session — minting can never help; you must fail fast)

`flarecrawl p6` is the primitive for this. It mints cookie shells with a
local Chromium, then replays targets with `curl_cffi --impersonate`
carrying the jar plus a real Chrome JA3/JA4 handshake:

```bash
flarecrawl p6 https://site.com/ --jar ./jar.json \
  --target https://site.com/api/stations \
  --target https://site.com/api/prices \
  --output-dir ./out
# or feed a list:
flarecrawl p6 https://site.com/ --jar ./jar.json \
  --targets-from urls.txt --json
```

What it does that a shell loop won't:

| Behaviour | Why it matters |
|-----------|----------------|
| Proactive re-mint when `jarhealth` says the jar is stale | Re-mint *before* a block, not after burning a request |
| **Cumulative** exponential cool-down (keyed on *total* re-mints, not per-target) | A per-target re-mint loop keeps the Akamai egress escalated; global backoff is the documented escape |
| Terminal fast-fail on Cloudflare 1020 | `blockdetect` marks it `terminal` — abort instead of wasting the re-mint budget |
| Resume journal next to the jar | `--resume` skips targets already completed |

Minting does **not** require solving the JS sensor — depositing the
`bm_*`/`_abck`/`__cf_bm` shells plus a real-Chrome TLS fingerprint is
enough to clear the edge for non-locale paths.

### Inspecting jar freshness offline

Before a replay batch, check whether the jar still has live shells —
no network call, no burned request:

```bash
flarecrawl session inspect @ampol          # or a path: ./jar.json
flarecrawl session inspect ./jar.json --json
```

Verdict is `fresh` | `stale` | `expired` | `empty`; exit code is
non-zero unless `fresh`, so a connector can branch on it and re-mint
proactively. This is the same oracle `p6` uses internally between
targets.

### Machine-readable block detection

Every `scrape` (CDP), `fetch --json`, and `recipe` result carries a
`meta.blocked` object so connectors stop string-matching their own
heuristics:

```json
{ "blocked": true, "vendor": "akamai", "kind": "interstitial",
  "terminal": false, "signal": "akamai interstitial" }
```

`vendor` ∈ akamai / cloudflare / imperva / cloudfront / datadome /
perimeterx. `kind` ∈ interstitial / edge_deny / js_challenge / captcha /
cf_1020_hard / rate_limited. `terminal: true` (Cloudflare 1020) means
non-bypassable — do not waste a mint. Note: HTTP status lies (the Akamai
interstitial is a 200), which is exactly why this exists. SPA-404 is
deliberately *not* detected — a generic detector would false-positive on
every single-page app, so assert your own content presence for that.

## When you don't need the full stack

| Target shape | Minimum flags |
|--------------|---------------|
| Static HTML, no JS | (default) |
| JS-rendered, no bot detection | `--js` |
| TLS fingerprint blocks direct downloads | `--js --stealth` (or use `flarecrawl fetch --stealth` for known URLs) |
| Cloudflare bot mode B | `--js --paywall` |
| Hard target with CF Chromium fingerprint blocked | `--js --browser local --headed` |
| Data lives in an XHR you want to keep | add `--capture-pattern --capture-dir` |
| You need to mass-download from a captured manifest | add `--then-fetch-from --then-fetch-column --then-fetch-output` |
| Shells expire mid-harvest / API replay / egress escalation | `flarecrawl p6 MINT_URL --jar jar.json --target ...` |
| Need to know if a minted jar is still good | `flarecrawl session inspect @name` (exit ≠0 unless fresh) |

## Headless vs headed local browser

`--browser local` defaults to headless. On war.gov-tier targets (Akamai
BMP), headless still gets blocked by behavioural fingerprinting. Use
`--headed` for those sites. For most local-Chromium targets, headless
works fine.

If headless is required (CI environment, no display), the typical
escalation order is:

1. Try headless first
2. If 403 stub, switch to a paid CDP backend with proper stealth
3. If still blocked, add a `Xvfb`-style virtual display + `--headed`
4. Last resort: drive the headed local browser via VNC/RDP from CI

## When yt-dlp earns its install

Some hard targets embed video via providers that yt-dlp's extractor
registry handles much better than DOM scraping:

- DVIDS Hub (`dvidshub.net`) — military media
- Vimeo with auth
- TikTok / Instagram embeds
- Twitch clips

Add `--yt-dlp` to the `videos` command:

```bash
flarecrawl videos https://www.war.gov/some-page --yt-dlp --json
```

This runs DOM-discovered iframe URLs through `yt_dlp.extract_info()`
to resolve to direct media URLs.

## Recipe form for repeatable flows

If you'll run the same flow more than once (or pin it for teammates),
write a recipe instead of a shell wrapper:

```yaml
# uap.yml
version: 1
goto: https://www.war.gov/UFO/
browser: local
headed: true
steps:
  - capture:
      pattern: "uap-csv.csv"
      to: ./out/csv/
  - wait: 3s
```

```bash
flarecrawl recipe uap.yml
flarecrawl recipe uap.yml --resume   # pick up after partial failure
```

See [`examples/recipes/`](../examples/recipes/) for more.
