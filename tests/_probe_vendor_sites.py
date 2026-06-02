"""Probe vendor brand sites for self-embedded tech signals.

Runs flarecrawl tech-detect (no --render) against ~30 vendor brand
domains in parallel and reports which fingerprints fire on each.

Output: JSON to stdout summarising:
- per-site detections
- per-fingerprint coverage (which sites fire it)
- candidates for adding to bench corpus
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys

# Vendor brand domains + which custom-overlay tech we expect to fire.
# Each entry: (url, expected_tech_name_in_overlay)
TARGETS: list[tuple[str, str]] = [
    ("https://sevenrooms.com",            "SevenRooms"),
    ("https://www.opentable.com",         "OpenTable"),
    ("https://www.resdiary.com",          "ResDiary"),
    ("https://www.resy.com",              "Resy"),
    ("https://www.exploretock.com",       "Tock"),
    ("https://www.thefork.com",           "TheFork"),
    ("https://www.quandoo.com",           "Quandoo"),
    ("https://www.eventbrite.com",        "Eventbrite"),
    ("https://www.mryum.com",             "Mr Yum"),
    ("https://www.meandu.com",            "me&u"),
    ("https://getbento.com",              "Bento"),
    ("https://nowbookit.com",             "Now Book It"),
    ("https://www.bookeasy.com.au",       "Bookeasy"),
    ("https://www.bookingboss.com",       "Booking Boss"),
    ("https://cobber.online",             "Cobber"),
    ("https://www.nabooki.com",           "Nabooki"),
    ("https://fareharbor.com",            "FareHarbor"),
    ("https://www.rezdy.com",             "Rezdy"),
    ("https://www.siteminder.com",        "SiteMinder"),
    ("https://atdw-online.com.au",        "ATDW"),
    ("https://www.localis.com.au",        "Localis"),
    ("https://www.simpleviewinc.com",     "Simpleview CMS"),
    ("https://www.mews.com",              "Mews"),
    ("https://www.cloudbeds.com",         "Cloudbeds"),
    ("https://bokun.io",                  "Bokun"),
    ("https://www.lightspeedhq.com",      "Lightspeed Restaurant"),
    ("https://squareup.com",              "Square Online"),
    ("https://triptease.com",             "Triptease"),
    ("https://craftcms.com",              "Craft CMS"),
    ("https://www.littlehotelier.com",    "Little Hotelier"),
    ("https://www.checkfront.com",        "Checkfront"),
    ("https://ventrata.com",              "Ventrata"),
    ("https://www.xola.com",              "Xola"),
    ("https://www.trekksoft.com",         "TrekkSoft"),
    ("https://www.rmscloud.com",          "RMS Cloud"),
    ("https://www.newbook.cloud",         "NewBook"),
    ("https://www.hirum.com.au",          "HiRUM"),
    ("https://www.seekom.com",            "Seekom"),
    ("https://www.staah.com",             "STAAH"),
    ("https://www.update247.com.au",      "Update247"),
    ("https://www.bopple.com",            "Bopple"),
    ("https://www.hungryhungry.com",      "HungryHungry"),
    ("https://www.peek.com",              "Peek Pro"),
    ("https://www.regiondo.com",          "Regiondo"),
    ("https://www.palisis.com",           "Palisis"),
    ("https://www.prioticket.com",        "Prioticket"),
    ("https://www.beds24.com",            "Beds24"),
    ("https://channex.io",                "Channex"),
    ("https://www.resly.com.au",          "Resly"),
    ("https://www.windcave.com",          "Windcave"),
    ("https://www.eway.com.au",           "eWAY"),
    ("https://www.securepay.com.au",      "SecurePay"),
    ("https://www.txa.com.au",            "Tourism Exchange Australia"),
    ("https://www.redballoon.com.au",     "RedBalloon"),
    ("https://www.adrenaline.com.au",     "Adrenaline"),
    ("https://www.experienceoz.com.au",   "Experience Oz"),
]


def probe(url: str, expected: str, timeout: int = 25) -> dict:
    try:
        proc = subprocess.run(
            ["uv", "run", "flarecrawl", "tech-detect", url,
             "--json", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        return {"url": url, "expected": expected, "error": "timeout"}
    except Exception as e:
        return {"url": url, "expected": expected, "error": str(e)}

    if proc.returncode != 0:
        return {"url": url, "expected": expected,
                "error": f"exit {proc.returncode}",
                "stderr": (proc.stderr or "")[-400:]}

    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"url": url, "expected": expected,
                "error": f"json: {e}",
                "raw": (proc.stdout or "")[:400]}

    detected: list[str] = []
    if isinstance(out, dict):
        for site in out.get("data", []):
            for t in site.get("technologies", []):
                detected.append(t.get("name", ""))
    return {
        "url": url,
        "expected": expected,
        "fired_expected": expected in detected,
        "detections": sorted(set(detected)),
    }


def main() -> int:
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(probe, url, exp): (url, exp)
                   for url, exp in TARGETS}
        for fut in concurrent.futures.as_completed(futures):
            url, exp = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"url": url, "expected": exp, "error": str(e)}
            results.append(r)
            sys.stderr.write(
                f"  {'OK ' if r.get('fired_expected') else 'NO '}"
                f"{exp:<28}{url}\n"
            )

    print(json.dumps(results, indent=2))
    fired = sum(1 for r in results if r.get("fired_expected"))
    errored = sum(1 for r in results if r.get("error"))
    sys.stderr.write(
        f"\n{fired}/{len(results)} brand sites self-embed; "
        f"{errored} errors\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
