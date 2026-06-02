"""CDP probe — re-run the 19 NO sites through CF Browser Run.

Uses `flarecrawl scrape --tech-detect --js --json` so the rendered
HTML + response headers + cookies + injected JS-globals all reach
the Wappalyzer engine. Necessary for techs that only surface via
window globals (Resy widget, Tock loader, etc).
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys

NO_SITES: list[tuple[str, str]] = [
    ("https://www.exploretock.com",       "Tock"),
    ("https://www.thefork.com",           "TheFork"),
    ("https://www.eventbrite.com",        "Eventbrite"),
    ("https://www.mryum.com",             "Mr Yum"),
    ("https://cobber.online",             "Cobber"),
    ("https://getbento.com",              "Bento"),
    ("https://www.nabooki.com",           "Nabooki"),
    ("https://www.opentable.com",         "OpenTable"),
    ("https://sevenrooms.com",            "SevenRooms"),
    ("https://www.localis.com.au",        "Localis"),
    ("https://atdw-online.com.au",        "ATDW"),
    ("https://www.siteminder.com",        "SiteMinder"),
    ("https://www.mews.com",              "Mews"),
    ("https://bokun.io",                  "Bokun"),
    ("https://www.newbook.cloud",         "NewBook"),
    ("https://www.staah.com",             "STAAH"),
    ("https://ventrata.com",              "Ventrata"),
    ("https://www.adrenaline.com.au",     "Adrenaline"),
    ("https://www.experienceoz.com.au",   "Experience Oz"),
    ("https://squareup.com",              "Square Online"),
]


def probe(url: str, expected: str, timeout_ms: int = 60_000) -> dict:
    # --wait-until domcontentloaded is required: the default "load" event
    # never fires on heavy SPAs (Akamai-walled OpenTable, Cloudflare-walled
    # Tock, etc) and the CF API throws a generic timeout. domcontentloaded
    # is enough to populate window.* globals for the JS probe.
    subproc_timeout = (timeout_ms // 1000) + 30
    try:
        proc = subprocess.run(
            ["uv", "run", "flarecrawl", "scrape", url,
             "--tech-detect", "--js", "--json",
             "--wait-until", "domcontentloaded",
             "--timeout", str(timeout_ms)],
            capture_output=True, text=True, timeout=subproc_timeout,
        )
    except subprocess.TimeoutExpired:
        return {"url": url, "expected": expected, "error": "subprocess timeout"}
    except Exception as e:
        return {"url": url, "expected": expected, "error": str(e)}

    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"url": url, "expected": expected,
                "error": f"json: {e}",
                "raw": (proc.stdout or "")[:400],
                "stderr": (proc.stderr or "")[-200:]}

    if isinstance(out, dict) and "error" in out:
        return {"url": url, "expected": expected,
                "error": out["error"].get("message", str(out["error"]))[:200]}

    detected: list[str] = []
    if isinstance(out, dict):
        # Single-URL scrape returns {data: {technologies: [...]}}
        # Multi-URL would return {data: [{technologies: [...]}, ...]}
        data = out.get("data", {})
        if isinstance(data, dict):
            for t in data.get("technologies", []) or []:
                detected.append(t.get("name", ""))
        elif isinstance(data, list):
            for site in data:
                if isinstance(site, dict):
                    for t in site.get("technologies", []) or []:
                        detected.append(t.get("name", ""))
        for t in out.get("technologies", []) or []:
            detected.append(t.get("name", ""))
    return {
        "url": url,
        "expected": expected,
        "fired_expected": expected in detected,
        "detections": sorted(set(detected)),
    }


def main() -> int:
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(probe, url, exp): (url, exp)
                   for url, exp in NO_SITES}
        for fut in concurrent.futures.as_completed(futures):
            url, exp = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"url": url, "expected": exp, "error": str(e)}
            results.append(r)
            tag = ("OK " if r.get("fired_expected") else
                   "ERR" if r.get("error") else "NO ")
            extra = r.get("error", "")[:60] if r.get("error") else ""
            sys.stderr.write(f"  {tag} {exp:<28}{url:<40}{extra}\n")

    print(json.dumps(results, indent=2))
    fired = sum(1 for r in results if r.get("fired_expected"))
    errored = sum(1 for r in results if r.get("error"))
    sys.stderr.write(
        f"\nCDP probe: {fired}/{len(results)} fired; "
        f"{errored} errors\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
