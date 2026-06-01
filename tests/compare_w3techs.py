"""Side-by-side comparison: flarecrawl tech-detect vs w3techs.

Not a pytest target - run directly:

    uv run python tests/compare_w3techs.py
    uv run python tests/compare_w3techs.py --markdown > docs/tech-detect-vs-w3techs.md

For each site, runs flarecrawl tech-detect (cleaned output) and fetches
the w3techs profile via jina (text-only proxy that bypasses w3techs's
JS), then surfaces:
- both:        techs both tools agree on
- flarecrawl-only: techs only flarecrawl found
- w3techs-only:    techs only w3techs found

Disagreements aren't necessarily errors - w3techs and Wappalyzer have
overlapping but non-identical fingerprint libraries, and w3techs lists
many protocol/format features that aren't "stack" techs (HTTP/2,
JSON-LD, GIF, language codes, etc.). The W3TECHS_NOISE_PATTERNS list
strips those before comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# Sites to compare. Diverse mix: LAMP CMS, JS frameworks, ecommerce,
# DIY builders, news, enterprise SaaS, hospitality verticals.
SITES = [
    "drupal.org",
    "wordpress.org",
    "vercel.com",
    "github.com",
    "shopify.com",
    "stripe.com",
    "bbc.com",
    "nytimes.com",
    "reddit.com",
    "news.ycombinator.com",
    "about.gitlab.com",
    "basecamp.com",
    "laravel.com",
    "www.djangoproject.com",
    "ghost.org",
    "astro.build",
    "webflow.com",
    "discord.com",
    "slack.com",
    "www.opentable.com",
    "sevenrooms.com",
    "www.squarespace.com",
    "airbnb.com",
    "www.cloudflare.com",
    "developer.mozilla.org",
]

# Same technology, different label. Normalise so the side-by-side
# doesn't surface false differences just because two tools spell the
# same vendor differently.
NAME_NORMALISE = {
    # w3techs label                  -> flarecrawl-canonical label
    "Apache": "Apache HTTP Server",
    "Cloudflare Server": "Cloudflare",
    "Twitter/X Cards": "Open Graph",  # both meta-tag flavours, close enough
    "CDNJS": "cdnjs",
    "jQuery CDN": "jsDelivr",  # near-equivalent CDN buckets; treat as same signal
}


# w3techs surfaces low-level features alongside actual techs. Strip
# those so the comparison focuses on the stack picture, not protocol
# trivia.
W3TECHS_NOISE_PATTERNS = [
    r"^\.",  # TLDs
    r"^Cookies expiring",
    r"^Default ",
    r"^(Embedded|External|Inline) CSS$",
    r"^Generic RDFa$",
    r"^HTML5$",
    r"^IPv6$",
    r"^UTF-8$",
    r"^(GIF|JPEG|PNG|SVG|WebP)$",
    r"^JSON-LD$",
    r"^Microdata$",
    r"^Open Graph$",
    r"^JavaScript$",  # tautological - we already know
    r"^HTTP/[0-9]",
    r"^(Gzip|Brotli|Deflate) Compression$",
    r"^Non-?HttpOnly Cookies$",
    r"^Non-Secure Cookies$",
    r"^HttpOnly Cookies$",
    r"^HTTP Strict Transport Security$",  # we call this HSTS, but it's in noise floor anyway
    r"^(GlobalSign|DigiCert|Sectigo|Let.s Encrypt|Amazon|Comodo|Entrust|IdenTrust)$",
    r"^Gmail$",  # 'has MX records pointing to Gmail' isn't a stack tech
    r"^(English|French|German|Italian|Spanish|Japanese|Chinese|Portuguese|"
    r"Russian|Dutch|Polish|Turkish|Czech|Swedish|Danish|Finnish|Norwegian|"
    r"Hungarian|Romanian|Slovak|Greek|Arabic|Hebrew|Korean|Catalan|Slovenian|"
    r"Bulgarian|Croatian|Estonian|Latvian|Lithuanian|Ukrainian|Vietnamese|"
    r"Thai|Indonesian|Malay|Filipino|Tagalog|Welsh|Irish|Scottish Gaelic|"
    r"Basque|Galician|Esperanto|Latin|Maltese|Icelandic|Faroese)$",
    r"^(Germany|United States|United Kingdom|France|Italy|Spain|Netherlands|"
    r"Ireland|Australia|Canada|Brazil|Japan|China|Singapore|India|Sweden|"
    r"Switzerland|Austria|Czech Republic|Norway|Russia|Hong Kong|"
    r"Belgium|Denmark|Finland|Poland|Portugal|South Korea|Mexico|Argentina|"
    r"Chile|Colombia|Peru|New Zealand|South Africa|Israel|Turkey|Greece|"
    r"Romania|Bulgaria|Hungary|Slovakia|Ukraine|Estonia|Latvia|Lithuania|"
    r"Iceland|Luxembourg)$",
    r"^Facebook$",  # share buttons aren't a stack
    r"^Twitter$",
    r"^X\.com$",
    r"^Pinterest$",
    r"^Instagram$",
    r"^LinkedIn$",
    r"^Microformats$",  # markup standard, not a tech
    r"^(Weak|Strong)? ?ETag$",
    r"^(Secure|Session) Cookies$",
    r"^Linux$",  # OS detection from headers - widespread but uninformative
    r"^Windows Server$",
    r"^Unix$",
    r"^(NS1|DNS Made Easy|Route 53|Cloudflare DNS|Google Cloud DNS|Azure DNS)$",
    r"^HorizonIQ$",  # hosting provider
    r"^Akamai Bot Manager$",  # already implied by Akamai
]


def normalise(name: str) -> str:
    """Apply equivalence map so identical techs spelled differently match up."""
    return NAME_NORMALISE.get(name, name)


def is_w3_noise(name: str) -> bool:
    return any(re.search(p, name) for p in W3TECHS_NOISE_PATTERNS)


def fetch_w3techs(domain: str, timeout: float = 30.0) -> list[str]:
    """Pull the technology list w3techs reports for a domain."""
    url = f"https://r.jina.ai/https://www.w3techs.com/sites/info/{domain}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "flarecrawl-bench/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [f"<fetch error: {e}>"]
    # Every detected tech is rendered as a markdown link to /technologies/details/<slug>
    techs = set(re.findall(
        r"\[([^\]]+)\]\(https://w3techs\.com/technologies/details/", body))
    return sorted(normalise(t) for t in techs if not is_w3_noise(t))


def _flarecrawl_cmd() -> str:
    venv_bin = Path(sys.executable).parent
    for c in (
        venv_bin / "flarecrawl.exe",
        venv_bin / "flarecrawl",
        venv_bin / "Scripts" / "flarecrawl.exe",
    ):
        if c.exists():
            return str(c)
    import shutil
    found = shutil.which("flarecrawl")
    if found:
        return found
    raise RuntimeError("flarecrawl CLI not on PATH")


def fetch_flarecrawl(domain: str, timeout: float = 20.0) -> tuple[list[str], str | None]:
    """Run flarecrawl tech-detect with the cleaning recipe."""
    url = f"https://{domain}"
    try:
        out = subprocess.run(
            [_flarecrawl_cmd(), "tech-detect", url, "--json",
             "--timeout", str(int(timeout)),
             "--exclude-categories",
             "Miscellaneous,Security,Tag managers,RUM"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        return [], "subprocess-timeout"
    if out.returncode != 0:
        return [], f"exit-{out.returncode}"
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError:
        return [], "bad-json"
    rec = payload["data"][0]
    if rec.get("error"):
        return [], rec["error"]
    return sorted(normalise(t["name"]) for t in rec.get("technologies", [])), None


def compare_one(domain: str) -> dict:
    fc, fc_err = fetch_flarecrawl(domain)
    w3 = fetch_w3techs(domain)
    w3_err = next((t for t in w3 if t.startswith("<")), None)
    if w3_err:
        w3 = []
    fc_set = set(fc)
    w3_set = set(w3)
    return {
        "domain": domain,
        "flarecrawl_error": fc_err,
        "w3techs_error": w3_err,
        "flarecrawl_count": len(fc),
        "w3techs_count": len(w3),
        "both": sorted(fc_set & w3_set),
        "flarecrawl_only": sorted(fc_set - w3_set),
        "w3techs_only": sorted(w3_set - fc_set),
    }


def render_markdown(rows: list[dict]) -> str:
    out: list[str] = []
    out.append("# flarecrawl tech-detect vs w3techs — side-by-side")
    out.append("")
    out.append("Local Wappalyzer-based detection (flarecrawl `tech-detect`, with")
    out.append("the documented noise filter applied) compared against w3techs's")
    out.append("public site profiles (fetched via jina to bypass their JS gating).")
    out.append("")
    out.append("Both tools are fingerprint-based but use overlapping non-identical")
    out.append("fingerprint libraries. Disagreements aren't necessarily errors -")
    out.append("they often reflect different fingerprint sources, different")
    out.append("category granularity, or one tool catching a signal the other")
    out.append("misses.")
    out.append("")
    out.append("**Run yourself:** `uv run python tests/compare_w3techs.py`")
    out.append("")
    out.append("## How to read the gaps")
    out.append("")
    out.append("w3techs-only entries fall into five buckets — only one is a")
    out.append("real flarecrawl miss:")
    out.append("")
    out.append("1. **w3techs is stale or wrong about this URL.** Verified empirically")
    out.append("   on ghost.org: w3techs reports Apache + Cloudflare + Nginx + Ruby +")
    out.append("   Vercel, but the live `Server:` header is literally `Netlify` with")
    out.append("   no other infrastructure signals present. w3techs caches snapshots")
    out.append("   and aggregates across subdomains.")
    out.append("2. **w3techs aggregates across subdomains.** Discourse + Mintlify on")
    out.append("   ghost.org are on `community.ghost.org` / `docs.ghost.org`. We")
    out.append("   scrape only the URL we're given.")
    out.append("3. **We filter by design.** Google Ads/Analytics/Tag Manager,")
    out.append("   Twitter/X, language detection, and OS probes (Ubuntu, Linux) are")
    out.append("   dropped by the documented `--exclude-categories` cleaning recipe.")
    out.append("   Drop those flags to surface them.")
    out.append("4. **Naming differences.** `CDNJS` vs `cdnjs`, `Apache` vs")
    out.append("   `Apache HTTP Server`. The script normalises the obvious ones.")
    out.append("5. **Real upstream fingerprint gaps.** We've patched the worst:")
    out.append("   GSAP (upstream only matched the dead `TweenMax.min.js` 2.x file)")
    out.append("   and Amazon CloudFront (upstream entry had zero detection")
    out.append("   patterns — w3techs detects it via DNS CNAME which we can't see")
    out.append("   from HTTP). See `wappalyzer_data/custom_fingerprints.json`.")
    out.append("")
    out.append("## Headline numbers")
    out.append("")
    valid = [r for r in rows if not r["flarecrawl_error"] and not r["w3techs_error"]]
    if valid:
        avg_both = sum(len(r["both"]) for r in valid) / len(valid)
        avg_fc_only = sum(len(r["flarecrawl_only"]) for r in valid) / len(valid)
        avg_w3_only = sum(len(r["w3techs_only"]) for r in valid) / len(valid)
        out.append(f"- Sites compared: **{len(valid)}** / {len(rows)}")
        out.append(f"- Mean overlap (both agree): **{avg_both:.1f}** techs/site")
        out.append(f"- Mean flarecrawl-only: {avg_fc_only:.1f} techs/site")
        out.append(f"- Mean w3techs-only:    {avg_w3_only:.1f} techs/site")
        out.append("")
    out.append("## Summary table")
    out.append("")
    out.append("| Site | both | flarecrawl-only | w3techs-only |")
    out.append("|---|---:|---:|---:|")
    for r in rows:
        if r["flarecrawl_error"] or r["w3techs_error"]:
            err = r["flarecrawl_error"] or r["w3techs_error"]
            out.append(f"| {r['domain']} | (error: {err}) | | |")
            continue
        out.append(f"| {r['domain']} | {len(r['both'])} | "
                   f"{len(r['flarecrawl_only'])} | {len(r['w3techs_only'])} |")
    out.append("")
    out.append("## Per-site detail")
    out.append("")
    for r in rows:
        out.append(f"### {r['domain']}")
        if r["flarecrawl_error"] or r["w3techs_error"]:
            err = r["flarecrawl_error"] or r["w3techs_error"]
            out.append(f"  (error: {err})")
            out.append("")
            continue
        out.append("")
        if r["both"]:
            out.append(f"**Both agree** ({len(r['both'])}): {', '.join(r['both'])}")
        else:
            out.append("**Both agree:** *(no overlap)*")
        out.append("")
        if r["flarecrawl_only"]:
            out.append(f"**flarecrawl only** ({len(r['flarecrawl_only'])}): "
                       f"{', '.join(r['flarecrawl_only'])}")
        out.append("")
        if r["w3techs_only"]:
            out.append(f"**w3techs only** ({len(r['w3techs_only'])}): "
                       f"{', '.join(r['w3techs_only'])}")
        out.append("")
    return "\n".join(out)


def render_text(rows: list[dict]) -> str:
    out: list[str] = []
    out.append("=" * 88)
    out.append(f"{'site':<28} {'both':>6} {'fc-only':>10} {'w3-only':>10}")
    out.append("=" * 88)
    for r in rows:
        if r["flarecrawl_error"] or r["w3techs_error"]:
            err = r["flarecrawl_error"] or r["w3techs_error"]
            out.append(f"{r['domain']:<28}  (error: {err})")
            continue
        out.append(f"{r['domain']:<28} {len(r['both']):>6} "
                   f"{len(r['flarecrawl_only']):>10} {len(r['w3techs_only']):>10}")
    out.append("")
    for r in rows:
        if r["flarecrawl_error"] or r["w3techs_error"]:
            continue
        out.append(f"--- {r['domain']} ---")
        out.append(f"  both:        {', '.join(r['both'])}")
        out.append(f"  fc-only:     {', '.join(r['flarecrawl_only'])}")
        out.append(f"  w3-only:     {', '.join(r['w3techs_only'])}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--markdown", action="store_true",
                   help="Emit Markdown report instead of plain text")
    p.add_argument("--json", action="store_true", help="Raw JSON output")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    rows = [compare_one(d) for d in SITES]

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
    elif args.markdown:
        sys.stdout.write(render_markdown(rows))
    else:
        sys.stdout.write(render_text(rows))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
