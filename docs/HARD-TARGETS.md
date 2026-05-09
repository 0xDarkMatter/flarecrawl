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
