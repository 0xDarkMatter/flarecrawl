"""HTML content extraction utilities for Flarecrawl.

Provides main-content extraction, tag filtering, image extraction,
structured data parsing (LD+JSON, OpenGraph, Twitter Cards), and
minimal HTML-to-markdown conversion.

Uses selectolax (lexbor) for HTML parsing — ~20x faster than
BeautifulSoup4 + lxml on the hot path. BS4 is still used in
sanitise.py and paywall.py where deep mutation APIs are needed.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

# ------------------------------------------------------------------
# Main content extraction
# ------------------------------------------------------------------

# Elements to remove when extracting main content
_STRIP_TAGS = ("nav", "footer", "header", "aside", "script", "style", "noscript", "iframe")

# Selectors to try for main content (in priority order)
_MAIN_SELECTORS = ["main", "article", "[role=main]", "#content", ".content", "#main"]


def _strip_tags(node: Node, tags: tuple[str, ...] | set[str]) -> None:
    """Remove all descendants of ``node`` whose tag is in ``tags``."""
    selector = ", ".join(tags)
    for el in node.css(selector):
        el.decompose()


def _node_html(node: Node) -> str:
    """Return outer HTML of a node (selectolax .html is outer HTML)."""
    return node.html or ""


def extract_main_content(html: str) -> str:
    """Extract main content from HTML, stripping nav/footer/sidebar.

    Tries known main-content selectors. Falls back to <body> with
    nav/footer/header/aside stripped.

    Returns cleaned HTML string.
    """
    tree = HTMLParser(html)

    # Try each selector in priority order
    for selector in _MAIN_SELECTORS:
        el = tree.css_first(selector)
        if el and len(el.text(strip=True)) > 50:
            _strip_tags(el, _STRIP_TAGS)
            return _node_html(el)

    # Fallback: use body with unwanted tags stripped
    body = tree.css_first("body")
    if not body:
        return html
    _strip_tags(body, _STRIP_TAGS)
    return _node_html(body)


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
    tree = HTMLParser(html)

    if include:
        parts_html: list[str] = []
        for selector in include:
            for node in tree.css(selector):
                parts_html.append(_node_html(node))
        return "<div>" + "".join(parts_html) + "</div>"

    if exclude:
        for selector in exclude:
            for el in tree.css(selector):
                el.decompose()

    body = tree.css_first("body")
    return _node_html(body) if body else (tree.html or html)


# ------------------------------------------------------------------
# Image extraction
# ------------------------------------------------------------------


def extract_images(html: str, base_url: str) -> list[dict]:
    """Extract image URLs from HTML.

    Finds <img>, <picture><source>, and <meta property="og:image"> tags.
    Returns list of dicts with url, alt, width, height keys.
    """
    tree = HTMLParser(html)
    images: list[dict] = []
    seen: set[str] = set()

    # <img> tags
    for img in tree.css("img"):
        attrs = img.attributes
        src = attrs.get("src") or attrs.get("data-src")
        if not src:
            continue
        url = urljoin(base_url, src)
        if url in seen:
            continue
        seen.add(url)
        images.append({
            "url": url,
            "alt": attrs.get("alt", "") or "",
            "width": attrs.get("width"),
            "height": attrs.get("height"),
        })

    # <picture><source> tags
    for source in tree.css("source"):
        srcset = source.attributes.get("srcset")
        if not srcset:
            continue
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
    for meta in tree.css('meta[property="og:image"]'):
        content = meta.attributes.get("content")
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
    tree = HTMLParser(html)

    # LD+JSON
    ld_json: list[dict] = []
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(strip=True)
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
    for meta in tree.css("meta"):
        prop = meta.attributes.get("property") or ""
        if not prop.startswith("og:"):
            continue
        content = meta.attributes.get("content") or ""
        if prop and content:
            key = prop[3:]
            opengraph[key] = content

    # Twitter Cards
    twitter_card: dict[str, str] = {}
    for meta in tree.css("meta"):
        name = meta.attributes.get("name") or ""
        if not name.startswith("twitter:"):
            continue
        content = meta.attributes.get("content") or ""
        if name and content:
            key = name[8:]
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
    """
    tree = HTMLParser(html)

    # Remove scripts and styles
    for tag in tree.css("script, style, noscript"):
        tag.decompose()

    lines: list[str] = []
    # Match BS4 behaviour: walk from the document root (including <html>/<head>
    # leak-through for title text). Callers who want body-only content first
    # pipe through extract_main_content().
    root = tree.root
    if root is not None:
        _walk(root, lines)

    # Clean up excessive blank lines
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _walk(element: Node, lines: list[str]) -> None:
    """Recursively walk DOM and build markdown lines."""
    # Text node
    if element.tag == "-text":
        text = (element.text() or "").strip()
        if text:
            lines.append(text)
        return

    tag = element.tag

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = element.text(strip=True)
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
        # Direct <li> children only (selectolax css has no recursive=False)
        i = 0
        for child in element.iter(include_text=False):
            if child.tag != "li":
                continue
            prefix = f"{i + 1}. " if tag == "ol" else "- "
            text = _inline_text(child)
            if text:
                lines.append(f"{prefix}{text}")
            i += 1
        lines.append("")
        return

    if tag == "pre":
        code = element.text() or ""
        lines.append(f"\n```\n{code}\n```\n")
        return

    if tag == "blockquote":
        text = element.text(strip=True)
        if text:
            lines.append(f"\n> {text}\n")
        return

    if tag == "hr":
        lines.append("\n---\n")
        return

    if tag == "a":
        text = element.text(strip=True)
        href = element.attributes.get("href", "") or ""
        if text and href:
            lines.append(f"[{text}]({href})")
        elif text:
            lines.append(text)
        return

    if tag == "img":
        alt = element.attributes.get("alt", "") or ""
        src = element.attributes.get("src", "") or ""
        if src:
            lines.append(f"![{alt}]({src})")
        return

    # Recurse for other tags
    for child in element.iter(include_text=True):
        _walk(child, lines)


def _inline_text(element: Node) -> str:
    """Convert inline elements to markdown text."""
    parts: list[str] = []
    for child in element.iter(include_text=True):
        if child.tag == "-text":
            raw = child.text() or ""
            parts.append(raw.strip())
        else:
            text = child.text(strip=True)
            if not text:
                continue
            name = child.tag
            if name in ("strong", "b"):
                parts.append(f"**{text}**")
            elif name in ("em", "i"):
                parts.append(f"*{text}*")
            elif name == "code":
                parts.append(f"`{text}`")
            elif name == "a":
                href = child.attributes.get("href", "") or ""
                parts.append(f"[{text}]({href})" if href else text)
            elif name == "br":
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

    scored: list[tuple[int, float, str]] = []
    for i, para in enumerate(paragraphs):
        words = para.lower().split()
        if len(words) < 5:
            continue
        word_counts = Counter(words)
        score = sum(
            (word_counts.get(term, 0) / len(words))
            * math.log(len(paragraphs) / (1 + sum(1 for p in paragraphs if term in p.lower())))
            for term in query_terms
        )
        if para.startswith("#") and any(t in para.lower() for t in query_terms):
            score *= 2.0
        link_ratio = para.count("[") / max(len(words), 1)
        if link_ratio > 0.3:
            score *= 0.3
        scored.append((i, score, para))

    relevant = sorted([s for s in scored if s[1] > 0], key=lambda x: x[1], reverse=True)[:top_k]
    relevant.sort(key=lambda x: x[0])

    if not relevant:
        return text

    return "\n\n".join(para for _, _, para in relevant)


# ------------------------------------------------------------------
# Precision / Recall extraction modes
# ------------------------------------------------------------------

_PRECISION_SELECTORS = ["article", "main", "[role=main]"]
_PRECISION_STRIP = ("nav", "footer", "header", "aside", "script", "style",
                    "noscript", "iframe", "form", "figure", "figcaption",
                    "table", "ul.nav", ".sidebar", ".menu", ".social",
                    ".share", ".related", ".comments", ".ad", ".ads")

_RECALL_SELECTORS = ["main", "article", "[role=main]", "#content", ".content",
                     "#main", ".main", "#article", ".article", ".post",
                     ".entry", ".page-content", "#page-content"]
_RECALL_STRIP = ("script", "style", "noscript", "iframe")


def extract_main_content_precision(html: str) -> str:
    """Extract main content with aggressive filtering (precision mode)."""
    tree = HTMLParser(html)
    for selector in _PRECISION_SELECTORS:
        el = tree.css_first(selector)
        if el and len(el.text(strip=True)) > 100:
            _strip_tags(el, _PRECISION_STRIP)
            return _node_html(el)
    body = tree.css_first("body")
    if body:
        _strip_tags(body, _PRECISION_STRIP)
        return _node_html(body)
    return html


def extract_main_content_recall(html: str) -> str:
    """Extract main content with conservative filtering (recall mode)."""
    tree = HTMLParser(html)
    for selector in _RECALL_SELECTORS:
        el = tree.css_first(selector)
        if el and len(el.text(strip=True)) > 30:
            _strip_tags(el, _RECALL_STRIP)
            return _node_html(el)
    body = tree.css_first("body")
    if body:
        _strip_tags(body, _RECALL_STRIP)
        return _node_html(body)
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
    tree = HTMLParser(html)

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

    def _walk_tree(element: Node, depth: int = 0) -> None:
        tag = element.tag
        if tag == "-text" or tag is None:
            return
        attrs = element.attributes
        role = attrs.get("role") or role_map.get(tag)

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            nodes.append({
                "role": "heading",
                "name": element.text(strip=True),
                "level": int(tag[1]),
                "depth": depth,
            })
            return

        if role:
            node: dict = {"role": role, "depth": depth}
            name = (
                attrs.get("aria-label")
                or attrs.get("alt")
                or attrs.get("title")
                or attrs.get("placeholder")
            )
            if not name and tag in ("a", "button", "li"):
                name = element.text(strip=True)[:100]
            if name:
                node["name"] = name

            if tag == "a":
                node["href"] = attrs.get("href", "") or ""
            if tag == "img":
                node["src"] = attrs.get("src", "") or ""
            if tag == "input":
                node["type"] = attrs.get("type", "text") or "text"

            nodes.append(node)

        # Iterate direct children (text nodes filtered in recursion)
        for child in element.iter(include_text=False):
            _walk_tree(child, depth + (1 if role else 0))

    body = tree.css_first("body") or tree.root
    if body is not None:
        _walk_tree(body)
    return nodes


# ------------------------------------------------------------------
# Content cleanup (ad/nav cruft removal)
# ------------------------------------------------------------------

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
    tree = HTMLParser(html)
    for selector in _AD_SELECTORS:
        for el in tree.css(selector):
            el.decompose()
    body = tree.css_first("body")
    return _node_html(body) if body else (tree.html or html)


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

        if not stripped:
            cleaned.append(line)
            continue

        if lower in _CRUFT_EXACT:
            continue

        if len(stripped) < 80 and _CRUFT_PATTERNS.match(lower):
            continue

        if lower in ("advertisement", "advertisements"):
            continue

        cleaned.append(line)

    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
