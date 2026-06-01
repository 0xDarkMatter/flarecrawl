"""Audit the vendored Wappalyzer fingerprint DB for quality issues.

Not a pytest target - run directly:

    uv run python tests/audit_fingerprints.py
    uv run python tests/audit_fingerprints.py --csv > docs/fingerprint-audit.csv

Surfaces:
- Techs with ZERO detection patterns (scriptSrc/headers/cookies/meta/
  html/scripts/css/url/dom/js all empty or missing). These can't be
  detected from anything we can see - the upstream entry is a
  placeholder.
- Techs with JS-globals-only patterns (require a browser-injected
  probe via `--browser-cookies` / `scrape --tech-detect --browser local`
  to surface).
- The chronic w3techs-only set from tests/compare_w3techs.py
  cross-referenced with the empty-pattern set: those are the
  candidates worth patching in custom_fingerprints.json.

Findings drive the overlay patches in wappalyzer_data/
custom_fingerprints.json. 2026-06-01 audit identified 173 empty-
pattern and 882 JS-globals-only techs out of 7552 total (2.3% and
11.7% respectively).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


DETECT_KEYS = (
    "scriptSrc", "headers", "cookies", "meta", "html",
    "scripts", "css", "url", "dom", "js",
)


# Techs that w3techs commonly reports for popular sites where our DB
# either has zero patterns or only JS-globals patterns. Drives the
# triage of which empties are worth patching.
CHRONIC_W3TECHS_ONLY = {
    "Ruby", "Starfield", "Envoy", "Discourse", "Bootstrap", "Mintlify",
    "Zendesk", "Index Exchange", "HubSpot", "Pendo",
    "Visual Website Optimizer", "Loom", "DocuSign", "Dropbox",
    "Sitecore Experience Platform", "Sitecore", "Atlassian Statuspage",
    "Microsoft UET", "Optimizely", "Piano", "Cxense", "Chartbeat",
    "Ahrefs Web Analytics", "Triple Whale", "Dreamdata",
}


def classify(fp: dict) -> str:
    """Return 'empty' | 'js-only' | 'ok'."""
    keys_set = [k for k in DETECT_KEYS if fp.get(k)]
    if not keys_set:
        return "empty"
    if keys_set == ["js"]:
        return "js-only"
    return "ok"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", action="store_true",
                   help="Emit CSV instead of human report")
    args = p.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    from flarecrawl.wappalyzer import get_wappalyzer
    w = get_wappalyzer()
    w._load()
    assert w._techs is not None

    empties: list[str] = []
    js_only: list[str] = []
    ok: list[str] = []
    for name, fp in sorted(w._techs.items()):
        if name.startswith("_"):
            continue
        c = classify(fp)
        if c == "empty":
            empties.append(name)
        elif c == "js-only":
            js_only.append(name)
        else:
            ok.append(name)

    total = len(empties) + len(js_only) + len(ok)

    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(["name", "status", "in_chronic_w3techs_only"])
        for name in empties:
            writer.writerow([name, "empty", name in CHRONIC_W3TECHS_ONLY])
        for name in js_only:
            writer.writerow([name, "js-only", name in CHRONIC_W3TECHS_ONLY])
        return 0

    print(f"Fingerprint DB audit (loaded {total} techs after overlay merge)")
    print(f"  - {len(ok):>5}  techs with HTTP-detectable patterns")
    print(f"  - {len(js_only):>5}  techs that require a JS-globals probe (CDP only)")
    print(f"  - {len(empties):>5}  techs with ZERO detection patterns")
    print()
    print("=" * 78)
    print("Empty-pattern techs cross-referenced with chronic w3techs-only set:")
    print("(these are the best candidates for overlay patches)")
    print("=" * 78)
    patchable = [n for n in empties if n in CHRONIC_W3TECHS_ONLY]
    for n in patchable:
        print(f"  {n}")
    print(f"  ({len(patchable)} candidates)")
    print()
    print("=" * 78)
    print("JS-globals-only techs in chronic set (need CDP probe; can't fix from HTTP):")
    print("=" * 78)
    cdp_only = [n for n in js_only if n in CHRONIC_W3TECHS_ONLY]
    for n in cdp_only:
        print(f"  {n}")
    print(f"  ({len(cdp_only)} entries)")
    print()
    print("=" * 78)
    print("Full empty list (alphabetical):")
    print("=" * 78)
    for n in empties:
        in_chronic = " *" if n in CHRONIC_W3TECHS_ONLY else ""
        print(f"  {n}{in_chronic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
