"""HTML content extraction utilities for Flarecrawl.

Provides main-content extraction, tag filtering, image extraction,
structured data parsing (LD+JSON, OpenGraph, Twitter Cards), and
minimal HTML-to-markdown conversion.

Uses BeautifulSoup4 + lxml for robust HTML parsing.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

# ------------------------------------------------------------------
# Main content extraction
# ------------------------------------------------------------------

# Elements to remove when extracting main content
_STRIP_TAGS = {"nav", "footer", "header", "aside", "script", "style", "noscript", "iframe"}

# Selectors to try for main content (in priority order)
_MAIN_SELECTORS = ["main", "article", "[role=main]", "#content", ".content", "#main"]


def extract_main_content(html: str) -> str:
    """Extract main content from HTML, stripping nav/footer/sidebar.

    Tries known main-content selectors. Falls back to <body> with
    nav/footer/header/aside stripped.

    Returns cleaned HTML string.
    """
    soup = BeautifulSoup(html, "lxml")

    # Try each selector in priority order
    for selector in _MAIN_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 50:
            # Remove unwanted nested elements
            for tag in el.find_all(_STRIP_TAGS):
                tag.decompose()
            return str(el)

    # Fallback: use body with unwanted tags stripped
    body = soup.find("body")
    if not body:
        return html

    for tag in body.find_all(_STRIP_TAGS):
        tag.decompose()
    return str(body)


# ------------------------------------------------------------------
# Tag filtering
# ------------------------------------------------------------------


def filter_tags(html: str, include: list[str] | None = None,
                exclude: list[str] | None = None) -> str:
    """Filter HTML by CSS selectors.

    include: keep only content matching these selectors.
    exclude: remove content matching these selectors.
    Only one of include/exclude should be set.

    Returns filtered HTML string.
    """
    soup = BeautifulSoup(html, "lxml")

    if include:
        parts = []
        for selector in include:
            parts.extend(soup.select(selector))
        # Build new document from matched elements
        result = BeautifulSoup("<div></div>", "lxml")
        container = result.find("div")
        for part in parts:
            container.append(part.extract())  # type: ignore[union-attr]
        return str(container)

    if exclude:
        for selector in exclude:
            for el in soup.select(selector):
                el.decompose()

    body = soup.find("body")
    return str(body) if body else str(soup)


# ------------------------------------------------------------------
# Image extraction
# ------------------------------------------------------------------


def extract_images(html: str, base_url: str) -> list[dict]:
    """Extract image URLs from HTML.

    Finds <img>, <picture><source>, and <meta property="og:image"> tags.
    Returns list of dicts with url, alt, width, height keys.
    """
    soup = BeautifulSoup(html, "lxml")
    images: list[dict] = []
    seen: set[str] = set()

    # <img> tags
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        url = urljoin(base_url, src)
        if url in seen:
            continue
        seen.add(url)
        images.append({
            "url": url,
            "alt": img.get("alt", ""),
            "width": img.get("width"),
            "height": img.get("height"),
        })

    # <picture><source> tags
    for source in soup.find_all("source"):
        srcset = source.get("srcset")
        if not srcset:
            continue
        # Take first URL from srcset
        first_src = srcset.split(",")[0].strip().split()[0]
        url = urljoin(base_url, first_src)
        if url in seen:
            continue
        seen.add(url)
        images.append({
            "url": url,
            "alt": "",
            "width": None,
            "height": None,
        })

    # <meta property="og:image">
    for meta in soup.find_all("meta", attrs={"property": "og:image"}):
        content = meta.get("content")
        if not content:
            continue
        url = urljoin(base_url, content)
        if url in seen:
            continue
        seen.add(url)
        images.append({
            "url": url,
            "alt": "",
            "width": None,
            "height": None,
        })

    return images


# ------------------------------------------------------------------
# Structured data extraction (LD+JSON, OpenGraph, Twitter Cards)
# ------------------------------------------------------------------


def extract_structured_data(html: str) -> dict:
    """Extract structured data from HTML.

    Parses:
    - <script type="application/ld+json"> blocks
    - <meta property="og:*"> tags (OpenGraph)
    - <meta name="twitter:*"> tags (Twitter Cards)

    Returns dict with ld_json, opengraph, twitter_card keys.
    """
    soup = BeautifulSoup(html, "lxml")

    # LD+JSON
    ld_json: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.get_text(strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
            if isinstance(data, list):
                ld_json.extend(data)
            else:
                ld_json.append(data)
        except json.JSONDecodeError:
            continue  # Skip malformed JSON

    # OpenGraph
    opengraph: dict[str, str] = {}
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if prop and content:
            # Strip "og:" prefix for cleaner keys
            key = prop[3:]
            opengraph[key] = content

    # Twitter Cards
    twitter_card: dict[str, str] = {}
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        name = meta.get("name", "")
        content = meta.get("content", "")
        if name and content:
            key = name[8:]  # Strip "twitter:" prefix
            twitter_card[key] = content

    return {
        "ld_json": ld_json,
        "opengraph": opengraph,
        "twitter_card": twitter_card,
    }


# ------------------------------------------------------------------
# Minimal HTML to Markdown converter
# ------------------------------------------------------------------


def html_to_markdown(html: str) -> str:
    """Convert HTML to simple markdown.

    Handles headings, paragraphs, links, lists, bold, italic, code.
    No external dependencies — uses BeautifulSoup for parsing.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove scripts and styles
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    lines: list[str] = []
    _walk(soup, lines)

    # Clean up excessive blank lines
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _walk(element, lines: list[str]) -> None:
    """Recursively walk DOM and build markdown lines."""
    if isinstance(element, str):
        # NavigableString
        text = element.strip()
        if text:
            lines.append(text)
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = element.get_text(strip=True)
        if text:
            lines.append(f"\n{'#' * level} {text}\n")
        return

    if tag == "p":
        text = _inline_text(element)
        if text:
            lines.append(f"\n{text}\n")
        return

    if tag == "br":
        lines.append("")
        return

    if tag in ("ul", "ol"):
        lines.append("")
        for i, li in enumerate(element.find_all("li", recursive=False)):
            prefix = f"{i + 1}. " if tag == "ol" else "- "
            text = _inline_text(li)
            if text:
                lines.append(f"{prefix}{text}")
        lines.append("")
        return

    if tag == "pre":
        code = element.get_text()
        lines.append(f"\n```\n{code}\n```\n")
        return

    if tag == "blockquote":
        text = element.get_text(strip=True)
        if text:
            lines.append(f"\n> {text}\n")
        return

    if tag == "hr":
        lines.append("\n---\n")
        return

    if tag == "a":
        text = element.get_text(strip=True)
        href = element.get("href", "")
        if text and href:
            lines.append(f"[{text}]({href})")
        elif text:
            lines.append(text)
        return

    if tag == "img":
        alt = element.get("alt", "")
        src = element.get("src", "")
        if src:
            lines.append(f"![{alt}]({src})")
        return

    # Recurse for other tags
    for child in element.children:
        _walk(child, lines)


def _inline_text(element: Tag) -> str:
    """Convert inline elements to markdown text."""
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, str):
            parts.append(child.strip())
        elif isinstance(child, Tag):
            text = child.get_text(strip=True)
            if not text:
                continue
            if child.name in ("strong", "b"):
                parts.append(f"**{text}**")
            elif child.name in ("em", "i"):
                parts.append(f"*{text}*")
            elif child.name == "code":
                parts.append(f"`{text}`")
            elif child.name == "a":
                href = child.get("href", "")
                parts.append(f"[{text}]({href})" if href else text)
            elif child.name == "br":
                parts.append("\n")
            else:
                parts.append(text)
    return " ".join(p for p in parts if p)


# ------------------------------------------------------------------
# Relevance filtering (BM25-style)
# ------------------------------------------------------------------


def filter_by_query(text: str, query: str, top_k: int = 10) -> str:
    """Filter text to keep only paragraphs relevant to a query.

    Uses simple TF-IDF-like scoring (no external deps).
    Splits text into paragraphs, scores each against query terms,
    returns top_k most relevant paragraphs in original order.
    """
    import math
    from collections import Counter

    if not query or not text:
        return text

    query_terms = set(query.lower().split())
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    if not paragraphs:
        return text

    # Score each paragraph by term frequency of query terms
    scored: list[tuple[int, float, str]] = []
    for i, para in enumerate(paragraphs):
        words = para.lower().split()
        # Skip very short paragraphs (likely nav items, list markers)
        if len(words) < 5:
            continue
        word_counts = Counter(words)
        # TF-IDF-like: sum of (term_freq / doc_length) for query terms
        score = sum(
            (word_counts.get(term, 0) / len(words))
            * math.log(len(paragraphs) / (1 + sum(1 for p in paragraphs if term in p.lower())))
            for term in query_terms
        )
        # Boost headings that match
        if para.startswith("#") and any(t in para.lower() for t in query_terms):
            score *= 2.0
        # Penalize list-heavy paragraphs (mostly links/bullets)
        link_ratio = para.count("[") / max(len(words), 1)
        if link_ratio > 0.3:
            score *= 0.3
        scored.append((i, score, para))

    # Keep paragraphs with score > 0, up to top_k, in original order
    relevant = sorted([s for s in scored if s[1] > 0], key=lambda x: x[1], reverse=True)[:top_k]
    relevant.sort(key=lambda x: x[0])  # Restore original order

    if not relevant:
        return text  # No matches, return everything

    return "\n\n".join(para for _, _, para in relevant)


# ------------------------------------------------------------------
# Precision / Recall extraction modes
# ------------------------------------------------------------------

# Tighter selectors for precision mode
_PRECISION_SELECTORS = ["article", "main", "[role=main]"]
_PRECISION_STRIP = {"nav", "footer", "header", "aside", "script", "style",
                    "noscript", "iframe", "form", "figure", "figcaption",
                    "table", "ul.nav", ".sidebar", ".menu", ".social",
                    ".share", ".related", ".comments", ".ad", ".ads"}

# Looser selectors for recall mode (keep more)
_RECALL_SELECTORS = ["main", "article", "[role=main]", "#content", ".content",
                     "#main", ".main", "#article", ".article", ".post",
                     ".entry", ".page-content", "#page-content"]
_RECALL_STRIP = {"script", "style", "noscript", "iframe"}


def extract_main_content_precision(html: str) -> str:
    """Extract main content with aggressive filtering (precision mode)."""
    soup = BeautifulSoup(html, "lxml")
    for selector in _PRECISION_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 100:
            for tag in el.find_all(_PRECISION_STRIP):
                tag.decompose()
            return str(el)
    # Fallback
    body = soup.find("body")
    if body:
        for tag in body.find_all(_PRECISION_STRIP):
            tag.decompose()
        return str(body)
    return html


def extract_main_content_recall(html: str) -> str:
    """Extract main content with conservative filtering (recall mode)."""
    soup = BeautifulSoup(html, "lxml")
    for selector in _RECALL_SELECTORS:
        el = soup.select_one(selector)
        if el and len(el.get_text(strip=True)) > 30:
            for tag in el.find_all(_RECALL_STRIP):
                tag.decompose()
            return str(el)
    body = soup.find("body")
    if body:
        for tag in body.find_all(_RECALL_STRIP):
            tag.decompose()
        return str(body)
    return html


# ------------------------------------------------------------------
# Accessibility tree
# ------------------------------------------------------------------


def extract_accessibility_tree(html: str) -> list[dict]:
    """Extract a simplified accessibility tree from HTML.

    Returns a list of nodes with role, name, level, and children info.
    Focuses on semantic elements: headings, landmarks, links, buttons,
    form controls, images, lists, tables.
    """
    soup = BeautifulSoup(html, "lxml")

    role_map = {
        "nav": "navigation",
        "main": "main",
        "header": "banner",
        "footer": "contentinfo",
        "aside": "complementary",
        "section": "region",
        "article": "article",
        "form": "form",
        "table": "table",
        "ul": "list",
        "ol": "list",
        "li": "listitem",
        "a": "link",
        "button": "button",
        "input": "textbox",
        "textarea": "textbox",
        "select": "combobox",
        "img": "image",
    }

    nodes: list[dict] = []

    def _walk_tree(element, depth: int = 0):
        if not isinstance(element, Tag):
            return

        tag = element.name
        role = element.get("role") or role_map.get(tag)

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            nodes.append({
                "role": "heading",
                "name": element.get_text(strip=True),
                "level": int(tag[1]),
                "depth": depth,
            })
            return

        if role:
            node: dict = {"role": role, "depth": depth}
            # Name from text, aria-label, alt, title, or placeholder
            name = (
                element.get("aria-label")
                or element.get("alt")
                or element.get("title")
                or element.get("placeholder")
            )
            if not name and tag in ("a", "button", "li"):
                name = element.get_text(strip=True)[:100]
            if name:
                node["name"] = name

            # Extra attributes
            if tag == "a":
                node["href"] = element.get("href", "")
            if tag == "img":
                node["src"] = element.get("src", "")
            if tag == "input":
                node["type"] = element.get("type", "text")

            nodes.append(node)

        for child in element.children:
            _walk_tree(child, depth + (1 if role else 0))

    body = soup.find("body") or soup
    _walk_tree(body)
    return nodes


# ------------------------------------------------------------------
# Content cleanup (ad/nav cruft removal)
# ------------------------------------------------------------------

# Ad/promo CSS selectors to strip from HTML DOM
_AD_SELECTORS = [
    "[class*='ad-']", "[class*='ad_']", "[id*='ad-']", "[id*='ad_']",
    "[class*='advertisement']", "[class*='sponsored']", "[class*='promo']",
    "[class*='banner-ad']", "[class*='dfp']", "[class*='gpt-ad']",
    "[data-ad]", "[data-advertisement]", "[data-ad-slot]",
    "[class*='social-share']", "[class*='share-buttons']", "[class*='share-bar']",
    "[class*='newsletter-signup']", "[class*='subscribe-box']", "[class*='email-signup']",
    "[class*='related-articles']", "[class*='recommended']", "[class*='recirculation']",
    "[class*='cookie-banner']", "[class*='consent']", "[class*='onetrust']",
]


def clean_html(html: str) -> str:
    """Strip ad/promo/social DOM elements from HTML.

    Lighter than extract_main_content -- keeps page structure (nav,
    footer, header) but removes ad containers, social share widgets,
    newsletter signups, cookie banners, and recommendation blocks.
    """
    soup = BeautifulSoup(html, "lxml")
    for selector in _AD_SELECTORS:
        for el in soup.select(selector):
            el.decompose()
    body = soup.find("body")
    return str(body) if body else str(soup)


# Lines that are pure ad/UI cruft (exact match after stripping)
_CRUFT_EXACT = {
    "advertisement", "ad", "sponsored", "promoted",
    "share this article", "share this", "share",
    "sign up", "sign in", "log in", "subscribe",
    "newsletter", "get the app", "open in app",
    "read more", "continue reading", "see more",
    "skip to content", "skip to main content",
    "skip advertisement", "skip ad",
    "recommended for you", "more from",
    "follow us", "follow", "like", "comment",
    "bookmark", "save", "print", "email",
    "copy link", "copied", "link copied",
}

# Patterns for lines that are ad/UI cruft (substring match)
_CRUFT_PATTERNS = re.compile(
    r"^(advertisement|sponsored content|promoted|"
    r"sign up for|subscribe to|get our|join our|"
    r"download the app|open in app|"
    r"share on (twitter|facebook|linkedin|email)|"
    r"follow us on|connect with us|"
    r"related articles?|trending now|"
    r"most (read|popular|viewed)|"
    r"you (may|might) (also )?like|"
    r"more (stories|articles) from|"
    r"this (article|story) (is|was)|"
    r"©\s*\d{4}.*|all rights reserved|"
    r"terms (of|and) (use|service)|privacy policy|cookie policy)$",
    re.IGNORECASE,
)


def clean_content(text: str) -> str:
    """Remove common ad placeholders, share buttons, and UI cruft from text.

    Operates on markdown/plain text (not HTML). Removes lines that are
    pure advertising or navigation chrome while preserving article content.
    """
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # Skip empty lines (preserve them for spacing)
        if not stripped:
            cleaned.append(line)
            continue

        # Skip exact cruft matches
        if lower in _CRUFT_EXACT:
            continue

        # Skip pattern matches (short lines only - don't filter article text)
        if len(stripped) < 80 and _CRUFT_PATTERNS.match(lower):
            continue

        # Skip standalone "Advertisement" with any capitalisation
        if lower in ("advertisement", "advertisements"):
            continue

        cleaned.append(line)

    # Clean up excessive blank lines from removals
    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
