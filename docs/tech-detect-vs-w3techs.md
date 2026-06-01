# flarecrawl tech-detect vs w3techs — side-by-side

Local Wappalyzer-based detection (flarecrawl `tech-detect`, with
the documented noise filter applied) compared against w3techs's
public site profiles (fetched via jina to bypass their JS gating).

Both tools are fingerprint-based but use overlapping non-identical
fingerprint libraries. Disagreements aren't necessarily errors -
they often reflect different fingerprint sources, different
category granularity, or one tool catching a signal the other
misses.

**Run yourself:** `uv run python tests/compare_w3techs.py`

## Headline numbers

- Sites compared: **24** / 25
- Mean overlap (both agree): **1.5** techs/site
- Mean flarecrawl-only: 3.8 techs/site
- Mean w3techs-only:    7.0 techs/site

## Summary table

| Site | both | flarecrawl-only | w3techs-only |
|---|---:|---:|---:|
| drupal.org | 3 | 1 | 6 |
| wordpress.org | 3 | 5 | 15 |
| vercel.com | 2 | 5 | 7 |
| github.com | 1 | 4 | 6 |
| shopify.com | 2 | 1 | 4 |
| stripe.com | 1 | 2 | 5 |
| bbc.com | 3 | 4 | 21 |
| nytimes.com | (error: subprocess-timeout) | | |
| reddit.com | 0 | 3 | 5 |
| news.ycombinator.com | 0 | 1 | 0 |
| about.gitlab.com | 0 | 8 | 0 |
| basecamp.com | 1 | 1 | 1 |
| laravel.com | 1 | 8 | 11 |
| www.djangoproject.com | 0 | 3 | 0 |
| ghost.org | 2 | 4 | 24 |
| astro.build | 2 | 3 | 3 |
| webflow.com | 5 | 7 | 20 |
| discord.com | 3 | 3 | 17 |
| slack.com | 2 | 3 | 6 |
| www.opentable.com | 0 | 3 | 0 |
| sevenrooms.com | 3 | 3 | 16 |
| www.squarespace.com | 0 | 3 | 0 |
| airbnb.com | 2 | 4 | 2 |
| www.cloudflare.com | 0 | 7 | 0 |
| developer.mozilla.org | 0 | 4 | 0 |

## Per-site detail

### drupal.org

**Both agree** (3): Apache HTTP Server, Drupal, PHP

**flarecrawl only** (1): Varnish

**w3techs only** (6): Fastly, Google Ads, Google Analytics, Google Tag Manager, Hotjar, Nginx

### wordpress.org

**Both agree** (3): Nginx, PHP, WordPress

**flarecrawl only** (5): Google Font API, Gutenberg, MySQL, WordPress Block Editor, WordPress Site Editor

**w3techs only** (15): Automattic, Backbone, Google Ads, Google Analytics, Google Tag Manager, Lodash, Moment.js, Open Graph, Popper, React, Underscore, WooCommerce, WordPress Jetpack, bbPress, jQuery

### vercel.com

**Both agree** (2): Next.js, Vercel

**flarecrawl only** (5): Amazon S3, Amazon Web Services, Linkedin Insight Tag, React, Tailwind CSS

**w3techs only** (7): Discourse, Mintlify, Node.js, Open Graph, Payload, Ruby, Vue.js

### github.com

**Both agree** (1): GitHub Pages

**flarecrawl only** (4): Amazon S3, Amazon Web Services, React, Tailwind CSS

**w3techs only** (6): Fastly, GitHub, Microsoft, Next.js, Node.js, Open Graph

### shopify.com

**Both agree** (2): Cloudflare, Shopify

**flarecrawl only** (1): Tailwind CSS

**w3techs only** (4): Discourse, Open Graph, Ruby, Twitter/X

### stripe.com

**Both agree** (1): Nginx

**flarecrawl only** (2): Amazon S3, Amazon Web Services

**w3techs only** (5): Amazon CloudFront, Next.js, Node.js, Open Graph, Starfield

### bbc.com

**Both agree** (3): Bootstrap, Optimizely, RequireJS

**flarecrawl only** (4): Cxense, Piano, Varnish, dc.js

**w3techs only** (21): Ahrefs Web Analytics, Broadcom, CDNJS, Chartbeat, Cloudflare, CrazyEgg, Envoy, Fastly, Full Circle Studies, Google Ads, Google Hosted Libraries, Next.js, Nielsen, Node.js, Open Graph, Ruby, Salesforce, Shopify, Zendesk, jQuery, jQuery CDN

### nytimes.com
  (error: subprocess-timeout)

### reddit.com

**Both agree:** *(no overlap)*

**flarecrawl only** (3): Python, Reddit, Varnish

**w3techs only** (5): Fastly, Google Ads, Google Analytics, Google Tag Manager, Open Graph

### news.ycombinator.com

**Both agree:** *(no overlap)*

**flarecrawl only** (1): Nginx


### about.gitlab.com

**Both agree:** *(no overlap)*

**flarecrawl only** (8): Cloudflare, GitLab, Google Cloud, Nuxt.js, OneTrust, Ruby, Ruby on Rails, Vue.js


### basecamp.com

**Both agree** (1): Cloudflare

**flarecrawl only** (1): Stimulus

**w3techs only** (1): Open Graph

### laravel.com

**Both agree** (1): Cloudflare

**flarecrawl only** (8): Algolia, Algolia DocSearch, Bunny, Bunny Fonts, Fathom, HubSpot, Inertia.js, Tailwind CSS

**w3techs only** (11): AVIF, Bootstrap, Caddy, DigitalOcean, Google Ads, Google Analytics, Google Tag Manager, Open Graph, PHP, Starfield, unpkg

### www.djangoproject.com

**Both agree:** *(no overlap)*

**flarecrawl only** (3): Nginx, RequireJS, Varnish


### ghost.org

**Both agree** (2): Hugo, Netlify

**flarecrawl only** (4): Algolia, Alpine.js, FirstPromoter, Tailwind CSS

**w3techs only** (24): Ahrefs Web Analytics, Apache HTTP Server, Bengali, CDNJS, Cloudflare, Discourse, Ghost, Google Ads, Google Analytics, Google Tag Manager, Mintlify, Next.js, Nginx, Node.js, Open Graph, Persian, Ruby, Twitter/X, Ubuntu, Vercel, jQuery, jQuery CDN, jsDelivr, static files

### astro.build

**Both agree** (2): Astro, Netlify

**flarecrawl only** (3): Fathom, React, Tailwind CSS

**w3techs only** (3): Google Ads, Open Graph, Ubuntu

### webflow.com

**Both agree** (5): Cloudflare, Google Hosted Libraries, Webflow, jQuery, jsDelivr

**flarecrawl only** (7): Amazon S3, Amazon Web Services, Embedly, Google Font API, Swiper, Three.js, cdnjs

**w3techs only** (20): AVIF, Amazon CloudFront, Atlassian, Atlassian Statuspage, CDNJS, Dreamdata, Envoy, GSAP, Google Ads, Google Analytics, Google Tag Manager, Microsoft UET, Next.js, Nginx, Node.js, Open Graph, Ruby, Starfield, Twitter/X, Zendesk

### discord.com

**Both agree** (3): Cloudflare, Google Hosted Libraries, jQuery

**flarecrawl only** (3): Embedly, Google Font API, OneTrust

**w3techs only** (17): CDNJS, Cloudflare Web Analytics, Envoy, Google Ads, Google Analytics, Google Tag Manager, Handlebars, Mintlify, Next.js, Node.js, Open Graph, Ruby, Vercel, Webflow, Zendesk, jQuery CDN, jsDelivr

### slack.com

**Both agree** (2): Apache HTTP Server, Envoy

**flarecrawl only** (3): Clearbit Reveal, OneTrust, Swiper

**w3techs only** (6): Bootstrap, Google Analytics, Google Tag Manager, Open Graph, QUIC, Twitter/X

### www.opentable.com

**Both agree:** *(no overlap)*

**flarecrawl only** (3): Akamai, Envoy, OneTrust


### sevenrooms.com

**Both agree** (3): Next.js, React, Vercel

**flarecrawl only** (3): Builder.io, PHP, Swiper

**w3techs only** (16): Cloudflare, Google, Google Ads, Google Analytics, Google Servers, Google Tag Manager, HubSpot, Index Exchange, LinkedIn Insight Tag, Node.js, Open Graph, Pendo, Visual Website Optimizer, XHTML Strict, jQuery, jsDelivr

### www.squarespace.com

**Both agree:** *(no overlap)*

**flarecrawl only** (3): Ahrefs, Squarespace, Squarespace Commerce


### airbnb.com

**Both agree** (2): Envoy, Nginx

**flarecrawl only** (4): Klarna Checkout, PayPal, React, RequireJS

**w3techs only** (2): Akamai, Open Graph

### www.cloudflare.com

**Both agree:** *(no overlap)*

**flarecrawl only** (7): Astro, Cloudflare, Google Analytics, Linkedin Insight Tag, Lucide, OneTrust, Tailwind CSS


### developer.mozilla.org

**Both agree:** *(no overlap)*

**flarecrawl only** (4): Google Cloud, Google Cloud Load Balancing, Google Cloud Trace, Varnish


