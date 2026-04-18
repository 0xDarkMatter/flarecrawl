"""Flarecrawl - Cloudflare Browser Run CLI."""

__version__ = "0.16.0"

# Polite-crawling default: bot identifies itself and points at a
# contactable homepage. Override per-call via --user-agent.
DEFAULT_USER_AGENT = (
    f"FlarecrawlBot/{__version__} (+https://github.com/forma-tools/flarecrawl)"
)
