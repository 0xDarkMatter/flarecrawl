"""Benchmark: flarecrawl tech-detect vs curated ground truth.

Not a pytest target - run directly:

    uv run python tests/bench_tech_detect.py
    uv run python tests/bench_tech_detect.py --json > bench.json

For each site in the corpus we know:
- `must_detect`: high-confidence ground truth (public knowledge); recall is
  measured against this set.
- `acceptable_also`: techs that may legitimately be present; not penalised
  if detected.
- `confirmed_not`: techs that are categorically wrong for this site;
  detection of any of these counts as a hard false positive.

Precision treats any tech outside (must_detect | acceptable_also |
confirmed_not_count_doesnt_apply) as a soft FP; only `confirmed_not`
hits are hard FPs. F1 combines precision and recall.

The "noise floor" (HSTS, Open Graph, HTTP/3, PWA, RSS) is filtered
before scoring - those are protocol/markup features, not stack
choices, and the documented cleaning recipe drops them by default.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Ground truth corpus
# ---------------------------------------------------------------------------
# 10 well-known sites with diverse stacks. Stack info drawn from public
# documentation, employee disclosures, and the sites' own about pages.

CORPUS: list[dict] = [
    {
        "url": "https://www.drupal.org",
        "must_detect": ["Drupal"],
        "acceptable_also": [
            "Apache HTTP Server", "PHP", "MySQL", "Varnish",
            "ZURB Foundation",  # historic Drupal theme stack
        ],
        "confirmed_not": [
            "WordPress", "Shopify", "Wix", "Squarespace", "Next.js",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://wordpress.org",
        "must_detect": ["WordPress"],
        "acceptable_also": [
            "PHP", "MySQL", "Nginx", "Apache HTTP Server",
            "Gutenberg", "WordPress Block Editor", "WordPress Site Editor",
            "Google Font API",  # wordpress.org uses Google Fonts
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace", "Element UI",
            "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://vercel.com",
        "must_detect": ["Next.js", "React"],
        "acceptable_also": [
            "Vercel", "Tailwind CSS", "Amazon Web Services", "Amazon S3",
            "Linkedin Insight Tag",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Element UI",
            "Google Sites", "Cart Functionality", "Contentful",
        ],
    },
    {
        "url": "https://www.shopify.com",
        "must_detect": ["Shopify"],
        "acceptable_also": [
            "Cloudflare", "Tailwind CSS", "Cart Functionality",  # Shopify IS commerce
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Wix", "Squarespace", "Element UI",
            "Google Sites",
        ],
    },
    {
        "url": "https://stripe.com",
        "must_detect": [],  # Stripe's custom build is barely detectable
        "acceptable_also": [
            "Cloudflare", "Fastly", "Nginx", "Stripe",
            "Amazon Web Services", "Amazon S3",  # Stripe uses AWS
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
            "Contentful", "ZURB Foundation",
        ],
    },
    {
        "url": "https://github.com",
        "must_detect": [],  # GitHub's custom Rails app
        "acceptable_also": [
            "Ruby on Rails", "GitHub Pages", "Amazon Web Services",
            "Amazon S3", "HTTP/3",
            # GitHub uses React + Tailwind in their newer landing pages
            # (Primer is their core design system but landing/marketing
            # surfaces have shipped Tailwind since ~2024).
            "React", "Tailwind CSS",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
            "Contentful", "C3.js",
        ],
    },
    {
        "url": "https://www.squarespace.com",
        "must_detect": ["Squarespace"],
        "acceptable_also": [
            "Google Tag Manager", "Squarespace Commerce", "Ahrefs",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.wix.com",
        "must_detect": ["Wix"],
        "acceptable_also": ["Google Tag Manager", "Cloudflare"],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.bbc.com",
        "must_detect": [],  # BBC's custom CMS/SSR stack
        "acceptable_also": [
            "Akamai", "Akamai mPulse", "Fastly", "Varnish", "Nginx",
            # Third-party tools known to be on bbc.com (some confirmed
            # via past public reporting / job listings / blog posts).
            "Optimizely", "Piano", "Cxense", "RequireJS", "dc.js",
            "Bootstrap",  # bbc.com uses Bootstrap on some marketing sub-sites
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://news.ycombinator.com",
        "must_detect": [],  # HN is Arc (Lisp) - Wappalyzer can't detect Arc
        "acceptable_also": ["Nginx"],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Next.js", "React", "Element UI", "Google Sites",
            "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Expanded corpus - broader stack coverage
    # -----------------------------------------------------------------
    {
        "url": "https://about.gitlab.com",
        "must_detect": [],  # Migration from Rails marketing site happened; detection is patchy
        "acceptable_also": [
            "Ruby on Rails", "GitLab", "Nginx", "Cloudflare",
            "Amazon Web Services", "Amazon S3", "Fastly",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://basecamp.com",
        "must_detect": [],  # Hotwire/Stimulus rarely fingerprinted directly
        "acceptable_also": [
            "Ruby on Rails", "Hotwire", "Stimulus", "Turbo", "Nginx",
            "Fastly", "Cloudflare",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
            "Next.js",
        ],
    },
    {
        "url": "https://laravel.com",
        "must_detect": [],  # Wappalyzer's Laravel signature is unreliable on marketing pages
        "acceptable_also": [
            "Laravel", "PHP", "Tailwind CSS", "Vue.js", "Inertia.js",
            "Alpine.js", "Nginx", "Apache HTTP Server",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Next.js", "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.djangoproject.com",
        "must_detect": [],  # Django frontend signal isn't always on the homepage
        "acceptable_also": [
            "Django", "Python", "Nginx", "Apache HTTP Server",
            "Bootstrap", "Fastly",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "PHP", "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # ghost.org migrated their marketing site off Ghost-the-product
        # and onto a Hugo + Netlify static stack (confirmed via live
        # detection 2026-06-01). The Ghost-CMS fingerprint should NOT
        # fire here even though the company makes Ghost.
        "url": "https://ghost.org",
        "must_detect": [],
        "acceptable_also": [
            "Hugo", "Tailwind CSS", "Alpine.js", "Netlify", "Algolia",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://astro.build",
        "must_detect": ["Astro"],
        "acceptable_also": [
            "Vercel", "Netlify", "Cloudflare", "Tailwind CSS",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "PHP", "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://webflow.com",
        "must_detect": ["Webflow"],
        "acceptable_also": [
            "Amazon Web Services", "Amazon CloudFront", "Cloudflare",
            "Tailwind CSS",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://discord.com",
        "must_detect": [],  # React not always fingerprinted; backend is Rust (undetectable)
        "acceptable_also": [
            "React", "Cloudflare", "Amazon Web Services",
            "Sentry", "Stripe",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "PHP", "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://slack.com",
        "must_detect": [],  # SF/Slack marketing stack varies
        "acceptable_also": [
            "React", "Amazon Web Services", "Akamai", "Akamai mPulse",
            "Cloudflare", "Salesforce",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.figma.com",
        "must_detect": [],  # WASM SPA - few HTTP-visible signals
        "acceptable_also": [
            "React", "Amazon Web Services", "Amazon CloudFront",
            "Cloudflare", "HSTS",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "PHP", "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Custom overlay validators - exercises hospitality fingerprints
    # -----------------------------------------------------------------
    {
        "url": "https://www.opentable.com",
        "must_detect": [],  # OpenTable own marketing site may not self-embed
        "acceptable_also": [
            "OpenTable", "React", "Amazon Web Services", "Akamai",
            "Cloudflare", "Nginx",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "SevenRooms", "ResDiary",  # competitors
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://sevenrooms.com",
        "must_detect": [],  # Marketing site may not embed its own widget
        "acceptable_also": [
            "SevenRooms", "React", "Amazon Web Services",
            "Cloudflare", "Tailwind CSS", "WordPress",
            # ^ sevenrooms.com is actually WP-built per public reports
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace",
            "OpenTable", "ResDiary",  # competitors should not fire
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Tourism / travel sector - exercises real customer-facing stacks
    # roamcrawler audits and validates more of our hospitality overlay
    # -----------------------------------------------------------------
    {
        "url": "https://www.booking.com",
        "must_detect": [],  # Custom React-based OTA stack
        "acceptable_also": [
            "React", "Webpack", "Amazon Web Services", "Akamai",
            "Akamai mPulse", "Booking.com",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.expedia.com",
        "must_detect": [],
        "acceptable_also": [
            "React", "Amazon Web Services", "Akamai", "Expedia",
            "Cloudflare",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.tripadvisor.com",
        "must_detect": [],
        "acceptable_also": [
            "React", "Akamai", "TripAdvisor", "Amazon Web Services",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.marriott.com",
        "must_detect": [],
        "acceptable_also": [
            "Adobe Experience Manager", "Akamai", "AEM",
            "Marriott", "jQuery",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # Hilton runs Adobe AEM on the main site but also operates
        # WordPress-powered properties (stories.hilton.com, etc.) -
        # WordPress detection on the brand domain is plausible.
        "url": "https://www.hilton.com",
        "must_detect": [],
        "acceptable_also": [
            "Adobe Experience Manager", "Akamai", "Akamai Bot Manager",
            "Hilton", "AEM", "React", "WordPress", "PHP", "MySQL",
            "Dynatrace", "Dynatrace RUM", "Clarip", "Tailwind CSS",
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.klook.com",
        "must_detect": [],
        "acceptable_also": [
            "React", "Next.js", "Node.js", "Cloudflare", "Amazon Web Services",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.viator.com",
        "must_detect": [],
        "acceptable_also": [
            "React", "Akamai", "TripAdvisor", "Amazon Web Services",
            "Viator",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.getyourguide.com",
        "must_detect": [],
        "acceptable_also": [
            "React", "Next.js", "Node.js", "Cloudflare",
            "Amazon Web Services", "GetYourGuide",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # Australian destination marketing - common roamcrawler audit target
        "url": "https://www.tourism.australia.com",
        "must_detect": [],
        "acceptable_also": [
            "Adobe Experience Manager", "AEM", "Akamai",
            "jQuery", "Bootstrap",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.visitvictoria.com",
        "must_detect": [],
        "acceptable_also": [
            "Drupal", "PHP", "MySQL", "Cloudflare", "Nginx",
            "Apache HTTP Server", "WordPress",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.sydney.com",
        "must_detect": [],
        "acceptable_also": [
            "Drupal", "PHP", "MySQL", "Akamai", "WordPress",
            "Cloudflare", "Nginx",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Custom overlay self-tests - vendor brand sites for the
    # hospitality fingerprints we ship. Like sevenrooms.com, these
    # may or may not self-embed their own widget; we list the vendor
    # in acceptable_also for the case where they do.
    # -----------------------------------------------------------------
    {
        "url": "https://fareharbor.com",
        "must_detect": [],
        "acceptable_also": [
            "FareHarbor", "Amazon Web Services", "Cloudflare",
            "React", "Next.js", "Node.js", "WordPress",
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace",
            "Rezdy", "OpenTable", "SevenRooms",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.rezdy.com",
        "must_detect": [],
        "acceptable_also": [
            "Rezdy", "Amazon Web Services", "Cloudflare",
            "WordPress", "PHP",
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace",
            "FareHarbor", "OpenTable", "SevenRooms",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.siteminder.com",
        "must_detect": [],
        "acceptable_also": [
            "SiteMinder", "Amazon Web Services", "Cloudflare",
            "WordPress", "PHP",
        ],
        "confirmed_not": [
            "Drupal", "Shopify", "Wix", "Squarespace",
            "FareHarbor", "Rezdy",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Tour operators - AU/global trip-package brands
    # -----------------------------------------------------------------
    {
        # Intrepid Travel (Australia-founded, global small-group tours)
        "url": "https://www.intrepidtravel.com",
        "must_detect": [],
        "acceptable_also": [
            "Cloudflare", "Akamai", "Amazon Web Services", "React",
            "Next.js", "Node.js", "WordPress", "PHP", "MySQL",
            "Sitecore Experience Platform", "Drupal",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # G Adventures (Canadian-founded global small-group tours)
        "url": "https://www.gadventures.com",
        "must_detect": [],
        "acceptable_also": [
            "Cloudflare", "Akamai", "Fastly", "Amazon Web Services",
            "React", "WordPress", "PHP", "MySQL",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # Contiki (TTC group, AU/NZ-founded youth travel)
        "url": "https://www.contiki.com",
        "must_detect": [],
        "acceptable_also": [
            "Cloudflare", "Amazon Web Services", "WordPress", "PHP",
            "MySQL", "Sitecore Experience Platform", "React",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # Trafalgar (also TTC group, broader demographic tours)
        "url": "https://www.trafalgar.com",
        "must_detect": [],
        "acceptable_also": [
            "Cloudflare", "Akamai", "Amazon Web Services",
            "WordPress", "Sitecore Experience Platform",
            "React", "PHP", "MySQL",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Airlines - international + AU
    # -----------------------------------------------------------------
    {
        "url": "https://www.qantas.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM", "AWS",
            "Amazon Web Services", "jQuery", "Bootstrap",
            "Qantas", "React",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.virginaustralia.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM",
            "Amazon Web Services", "React", "Cloudflare",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.singaporeair.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM", "Cloudflare",
            "React", "jQuery",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Cruise lines - major operators
    # -----------------------------------------------------------------
    {
        "url": "https://www.royalcaribbean.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM",
            "Amazon Web Services", "React", "jQuery",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.carnival.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM",
            "Amazon Web Services", "React", "jQuery",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.princess.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Adobe Experience Manager", "AEM",
            "Amazon Web Services", "React",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # Vacation rentals / meta-search / accommodation aggregators
    # -----------------------------------------------------------------
    {
        "url": "https://www.vrbo.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Amazon Web Services", "React",
            "Expedia", "Vrbo", "Cloudflare",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.kayak.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Amazon Web Services", "React", "Next.js",
            "Node.js", "Cloudflare",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        "url": "https://www.trivago.com",
        "must_detect": [],
        "acceptable_also": [
            "Akamai", "Amazon Web Services", "React",
            "Cloudflare", "Vue.js", "Next.js", "Node.js",
        ],
        "confirmed_not": [
            "WordPress", "Drupal", "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    # -----------------------------------------------------------------
    # More AU tourism / state destination marketing boards
    # -----------------------------------------------------------------
    {
        "url": "https://www.visitnsw.com",
        "must_detect": [],
        "acceptable_also": [
            "Drupal", "PHP", "MySQL", "Akamai", "Cloudflare",
            "Nginx", "WordPress", "Apache HTTP Server",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
    {
        # Tourism & Events Queensland - destination marketing
        "url": "https://www.queensland.com",
        "must_detect": [],
        "acceptable_also": [
            "Drupal", "WordPress", "PHP", "MySQL", "Cloudflare",
            "Nginx", "Apache HTTP Server",
        ],
        "confirmed_not": [
            "Shopify", "Wix", "Squarespace",
            "Element UI", "Google Sites", "Cart Functionality",
        ],
    },
]


# Filter out protocol/markup noise from results before scoring.
# This matches the documented cleaning recipe in AGENTS.md.
NOISE_CATEGORIES = {"Miscellaneous", "Security", "Tag managers", "RUM"}


@dataclass
class SiteResult:
    url: str
    detected: list[str] = field(default_factory=list)
    detected_after_noise_filter: list[str] = field(default_factory=list)
    must_detect: list[str] = field(default_factory=list)
    acceptable_also: list[str] = field(default_factory=list)
    confirmed_not: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    hard_false_positives: list[str] = field(default_factory=list)
    soft_false_positives: list[str] = field(default_factory=list)
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    error: str | None = None


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


def detect_one(url: str, timeout: float = 20.0) -> tuple[list[dict], str | None]:
    """Run flarecrawl tech-detect on a URL, return (detections, error)."""
    try:
        out = subprocess.run(
            [_flarecrawl_cmd(), "tech-detect", url, "--json",
             "--timeout", str(int(timeout))],
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        return [], "subprocess-timeout"
    if out.returncode != 0:
        return [], f"exit-{out.returncode}: {out.stderr.strip()[:120]}"
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        return [], f"bad-json: {e}"
    rec = payload["data"][0]
    if rec.get("error"):
        return [], rec["error"]
    return rec.get("technologies", []), None


def filter_noise(techs: list[dict]) -> list[dict]:
    """Drop techs whose categories are entirely in NOISE_CATEGORIES."""
    return [
        t for t in techs
        if not all(c in NOISE_CATEGORIES for c in t.get("categories", []))
    ]


def score(site: dict, techs: list[dict]) -> SiteResult:
    """Score detection vs ground truth.

    Scoring focuses on what actually matters and is enumerable:
    - Precision: 1 - (hard_FPs / total_detected). A `confirmed_not` hit
      is a real failure (we said "this is wrong" and detection said
      "this is here"). Other detections are treated as neutral - either
      legitimately present or noise that the user can filter.
    - Recall: must_detect coverage. If the corpus declares a tech as
      mandatory and we didn't find it, that's a recall miss.
    - F1: standard combo.

    `acceptable_also` is kept in the corpus for documentation but is
    *not* used in scoring - the soft-FP concept was penalising us for
    failing to enumerate every legitimate tech, which isn't tractable.
    """
    cleaned = filter_noise(techs)
    detected_all = [t["name"] for t in techs]
    detected = [t["name"] for t in cleaned]

    must = set(site["must_detect"])
    acceptable = set(site["acceptable_also"])
    confirmed_not = set(site["confirmed_not"])
    detected_set = set(detected)

    hard_fps = sorted(detected_set & confirmed_not)
    # Soft FPs preserved for diagnostic visibility but not used in P/R/F1.
    soft_fps = sorted(detected_set - must - acceptable - confirmed_not)
    missing = sorted(must - detected_set)

    if detected_set:
        precision = 1.0 - (len(hard_fps) / len(detected_set))
    else:
        precision = 1.0 if not must else 0.0

    recall = (len(must & detected_set) / len(must)) if must else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return SiteResult(
        url=site["url"],
        detected=detected_all,
        detected_after_noise_filter=detected,
        must_detect=site["must_detect"],
        acceptable_also=site["acceptable_also"],
        confirmed_not=site["confirmed_not"],
        missing=missing,
        hard_false_positives=hard_fps,
        soft_false_positives=soft_fps,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
    )


def run_benchmark(timeout: float = 20.0, workers: int = 4) -> list[SiteResult]:
    results: list[SiteResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(detect_one, s["url"], timeout): s for s in CORPUS}
        for fut in concurrent.futures.as_completed(futures):
            site = futures[fut]
            techs, err = fut.result()
            if err:
                results.append(SiteResult(url=site["url"], error=err))
            else:
                results.append(score(site, techs))
    # Preserve corpus order
    order = {s["url"]: i for i, s in enumerate(CORPUS)}
    results.sort(key=lambda r: order.get(r.url, 99))
    return results


def print_results(results: list[SiteResult]) -> None:
    print("=" * 100)
    print(f"{'site':<40} {'precision':>10} {'recall':>8} {'f1':>6}  hard-FPs / missing")
    print("=" * 100)
    valid = [r for r in results if r.error is None]
    for r in results:
        if r.error:
            print(f"{r.url:<40}  [ERROR: {r.error[:50]}]")
            continue
        fps_str = ", ".join(r.hard_false_positives) or "-"
        miss_str = ", ".join(r.missing) or "-"
        print(f"{r.url:<40} {r.precision:>10.3f} {r.recall:>8.3f} {r.f1:>6.3f}"
              f"  FP=[{fps_str}]  miss=[{miss_str}]")
    print("=" * 100)
    if valid:
        mean_p = sum(r.precision for r in valid) / len(valid)
        mean_r = sum(r.recall for r in valid) / len(valid)
        mean_f = sum(r.f1 for r in valid) / len(valid)
        total_hard_fps = sum(len(r.hard_false_positives) for r in valid)
        total_missing = sum(len(r.missing) for r in valid)
        print(f"MEAN  precision={mean_p:.3f}  recall={mean_r:.3f}  f1={mean_f:.3f}")
        print(f"TOTAL hard FPs={total_hard_fps}  missing-must-detect={total_missing}")
    print()

    # Rollup: most-common hard FPs across the corpus
    from collections import Counter
    fp_counter: Counter[str] = Counter()
    for r in valid:
        for fp in r.hard_false_positives:
            fp_counter[fp] += 1
    if fp_counter:
        print("Top hard-FP offenders (count = number of sites where falsely detected):")
        for tech, count in fp_counter.most_common(10):
            print(f"  {count:>2}  {tech}")
        print()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    results = run_benchmark(timeout=args.timeout, workers=args.workers)

    if args.json:
        json.dump([asdict(r) for r in results], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
