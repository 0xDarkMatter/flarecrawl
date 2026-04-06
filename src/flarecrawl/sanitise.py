"""Agent-safety sanitisation for Flarecrawl.

Defends against three trap categories from the AI Agent Traps taxonomy
(Franklin et al., Google DeepMind, 2026):

  1. Content Injection (Perception) - hidden text, suspicious attributes
  2. Prompt Injection (Action) - instruction patterns in web content
  3. Semantic Manipulation (Reasoning) - authority/urgency language (flagged only)

Usage:
    from flarecrawl.sanitise import sanitise_html, sanitise_text

    # Phase 1: HTML-level (before markdown conversion)
    result = sanitise_html(html_string)
    clean_html = result.content
    findings = result.findings

    # Phase 2: Text-level (after markdown conversion)
    result = sanitise_text(markdown_string)
    clean_text = result.content

Extensibility:
    Add new sanitisers with @register_html or @register_text decorators.
    Each sanitiser is a plain function - no class hierarchy needed.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, Tag

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single sanitisation finding."""

    category: str  # "content_injection" | "prompt_injection" | "semantic_manipulation"
    severity: str  # "high" | "medium" | "low"
    description: str
    action: str  # "removed" | "flagged"
    count: int = 1


@dataclass
class SanitiseResult:
    """Result of a sanitisation pass."""

    content: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def stats(self) -> dict:
        """Compute aggregate stats from findings."""
        removed = sum(f.count for f in self.findings if f.action == "removed")
        flagged = sum(f.count for f in self.findings if f.action == "flagged")
        by_cat: dict[str, int] = {}
        for f in self.findings:
            by_cat[f.category] = by_cat.get(f.category, 0) + f.count
        return {"removed": removed, "flagged": flagged, "byCategory": by_cat}

    def to_metadata(self) -> dict:
        """Format for inclusion in JSON result metadata."""
        return {
            "sanitised": True,
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "action": f.action,
                    "count": f.count,
                }
                for f in self.findings
            ],
            "stats": self.stats,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_html_sanitisers: list[Callable[[BeautifulSoup], list[Finding]]] = []
_text_sanitisers: list[Callable[[str], tuple[str, list[Finding]]]] = []


def register_html(fn: Callable[[BeautifulSoup], list[Finding]]) -> Callable:
    """Register an HTML-level sanitiser.

    Signature: (soup: BeautifulSoup) -> list[Finding]
    The function mutates soup in-place and returns findings.
    """
    _html_sanitisers.append(fn)
    return fn


def register_text(fn: Callable[[str], tuple[str, list[Finding]]]) -> Callable:
    """Register a text-level sanitiser.

    Signature: (text: str) -> tuple[str, list[Finding]]
    Returns (cleaned_text, findings).
    """
    _text_sanitisers.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum text length in a hidden element to consider it suspicious.
# Short hidden spans (icons, toggles, responsive helpers) are benign.
_HIDDEN_TEXT_MIN_CHARS = 20

# CSS properties/patterns that hide content from visual rendering.
_HIDING_PATTERNS: list[tuple[str, re.Pattern | None]] = [
    ("display", re.compile(r"display\s*:\s*none", re.IGNORECASE)),
    ("visibility", re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)),
    ("opacity", re.compile(r"opacity\s*:\s*0(?:\.0+)?(?:\s*;|\s*$)", re.IGNORECASE)),
    ("font-size", re.compile(r"font-size\s*:\s*0(?:px|em|rem|%)?\s*(?:;|$)", re.IGNORECASE)),
    ("height-zero", re.compile(r"(?:^|;)\s*height\s*:\s*0(?:px)?\s*(?:;|$)", re.IGNORECASE)),
    ("width-zero", re.compile(r"(?:^|;)\s*width\s*:\s*0(?:px)?\s*(?:;|$)", re.IGNORECASE)),
    ("overflow-clip", re.compile(
        r"overflow\s*:\s*hidden[^;]{0,200}(?:height\s*:\s*0|max-height\s*:\s*0)", re.IGNORECASE
    )),
    ("position-offscreen", re.compile(
        r"position\s*:\s*(?:absolute|fixed).{0,200}?(?:left|top)\s*:\s*-\d{4,}",
        re.IGNORECASE,
    )),
    ("text-indent", re.compile(r"text-indent\s*:\s*-\d{4,}", re.IGNORECASE)),
    ("clip-path", re.compile(r"clip-path\s*:\s*inset\s*\(\s*100\s*%", re.IGNORECASE)),
    ("color-transparent", re.compile(r"color\s*:\s*transparent", re.IGNORECASE)),
]

# Prompt injection patterns (compiled once).
_INJECTION_PATTERNS = re.compile(
    r"(?:"
    r"ignore\s+(?:(?:all|your|the|my)\s+)*(?:previous|prior|above|safety)\s+(?:instructions?|context|rules?|guidelines?|constraints?|restrictions?)"
    r"|you\s+are\s+now\s+(?:a\s+)?"
    r"|new\s+(?:system\s+)?instructions?\s*:"
    r"|forget\s+(?:all\s+|everything\s+)?(?:above|previous|prior|you\s+(?:know|were|have))"
    r"|disregard\s+(?:all\s+)?(?:previous|prior|above)"
    r"|override\s+(?:all\s+)?(?:previous|prior|your|safety)\s*(?:protocols?|restrictions?|rules?|guidelines?|instructions?)?"
    r"|(?:system|assistant)\s*(?:prompt|message)\s*:"
    r"|(?:ADMIN|SYSTEM|ROOT)\s*:\s*(?:override|ignore|forget|disregard|enter|enable|disable|reveal|execute|you\s)"
    r"|act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a\s+)?(?:different|new|another|unrestricted|evil|unfiltered|jailbroken)"
    r"|pretend\s+(?:you\s+are|to\s+be)\s+"
    r"|enter\s+(?:developer|debug|unrestricted|jailbreak)\s+mode"
    r"|(?:you\s+(?:must|should|will)\s+)?(?:always\s+)?obey\s+(?:the\s+following|these|my)"
    r"|do\s+(?:not|anything)\s+(?:i\s+say|regardless)"
    r"|(?:decode|execute)\s+(?:the\s+following|these|this)\s+(?:base64|instructions?|commands?)"
    r")",
    re.IGNORECASE,
)

# Delimiter patterns that might frame injected content.
_DELIMITER_PATTERN = re.compile(
    r"^(?:"
    r"```\s*$"  # isolated code fence
    r"|---+\s*$"  # horizontal rule used as separator
    r"|</?(?:system|instructions?|context|prompt|user|assistant|message|task)>"  # XML-like tags
    r"|<<<\s*$"
    r"|>>>\s*$"
    r")$",
    re.IGNORECASE,
)

# Suspicious attribute content patterns.
_ATTR_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore|forget|disregard|override|system|instructions?|prompt|"
    r"you\s+are|act\s+as|pretend|execute|reveal|output)",
    re.IGNORECASE,
)

# Urgency words for semantic manipulation detection.
_URGENCY_WORDS = {
    "immediately", "urgent", "urgently", "critical", "critically",
    "must act now", "time-sensitive", "expires", "deadline",
    "act now", "right now", "without delay", "asap",
}

# Authority claim patterns.
_AUTHORITY_PATTERNS = re.compile(
    r"(?:"
    r"(?:officially|authoritatively)\s+confirmed"
    r"|according\s+to\s+(?:internal|classified|confidential)"
    r"|classified\s+(?:information|document|data)"
    r"|confidential\s+(?:source|information|data)"
    r"|leaked\s+(?:document|information|data|memo)"
    r"|internal\s+(?:memo|document|source)s?\s+(?:reveal|show|confirm|indicate)"
    r"|(?:official|authorized)\s+notice\s+from"
    r")",
    re.IGNORECASE,
)

# Zero-width and bidirectional control characters.
_UNICODE_TRICKS = re.compile(
    "["
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\ufeff"  # byte order mark / zero-width no-break space
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u202a"  # left-to-right embedding
    "\u202b"  # right-to-left embedding
    "\u202c"  # pop directional formatting
    "\u202d"  # left-to-right override
    "\u202e"  # right-to-left override
    "\u2066"  # left-to-right isolate
    "\u2067"  # right-to-left isolate
    "\u2068"  # first strong isolate
    "\u2069"  # pop directional isolate
    "]"
)

# CSS class-based hiding patterns.
_HIDING_CLASSES = re.compile(
    r"(?:^|\s)(?:d-none|hidden|invisible|visually-hidden|sr-only)(?:\s|$)",
    re.IGNORECASE,
)

# Accessibility hiding classes that need injection-pattern check before removal.
_ACCESSIBILITY_CLASSES = re.compile(
    r"(?:^|\s)(?:sr-only|visually-hidden)(?:\s|$)",
    re.IGNORECASE,
)

# Standard meta tag names to skip (never sanitise these).
_STANDARD_META_NAMES = frozenset({
    "description", "keywords", "author", "robots", "viewport", "generator",
    "theme-color", "color-scheme", "format-detection",
})
_STANDARD_META_PROPERTY_PREFIXES = (
    "og:", "twitter:", "fb:", "article:", "music:", "video:", "book:", "profile:",
)

# Homoglyph mapping: Cyrillic/Greek characters visually similar to Latin.
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic -> Latin
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i",
    "\u0410": "A", "\u0415": "E", "\u041e": "O", "\u0420": "P",
    "\u0421": "C", "\u0422": "T", "\u041d": "H", "\u041c": "M",
    "\u0412": "B", "\u041a": "K",
    # Greek -> Latin
    "\u03b1": "a", "\u03b5": "e", "\u03bf": "o", "\u03c1": "p",
    "\u0391": "A", "\u0395": "E", "\u039f": "O", "\u0392": "B",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u03a4": "T",
}
_HOMOGLYPH_CHARS = re.compile(
    "[" + "".join(re.escape(c) for c in _HOMOGLYPH_MAP) + "]"
)

# Markdown image/link pattern for exfiltration detection.
_MD_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_SUSPICIOUS_URL = re.compile(
    r"(?:"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|[?&](?:secret|token|key|api[_-]?key|password|auth|session|data|exfil)="
    r"|\.(?:php|cgi)\?.*="
    r")",
    re.IGNORECASE,
)

# HTML entity pattern for evasion detection.
_HTML_ENTITY = re.compile(r"&(?:#(?:x[0-9a-fA-F]+|\d+)|[a-zA-Z]+);")


# ---------------------------------------------------------------------------
# HTML-level sanitisers (Phase 1)
# ---------------------------------------------------------------------------


@register_html
def sanitise_hidden_text(soup: BeautifulSoup) -> list[Finding]:
    """Remove elements hidden via CSS that contain substantial text.

    Targets: display:none, visibility:hidden, opacity:0, font-size:0,
    height:0/width:0, off-screen positioning, text-indent:-9999px,
    clip-path:inset(100%), color:transparent.

    Only removes elements with >= _HIDDEN_TEXT_MIN_CHARS of text content
    to avoid stripping responsive CSS toggles.
    """
    findings: list[Finding] = []
    removed_count = 0

    for el in soup.find_all(style=True):
        if not isinstance(el, Tag):
            continue
        # Skip already-decomposed children (parent destroyed by earlier iteration)
        if el.parent is None:
            continue
        style = el.get("style", "")
        if not style:
            continue

        text = el.get_text(strip=True)
        if len(text) < _HIDDEN_TEXT_MIN_CHARS:
            continue

        for name, pattern in _HIDING_PATTERNS:
            if pattern and pattern.search(style):
                el.decompose()
                removed_count += 1
                break

    if removed_count:
        findings.append(Finding(
            category="content_injection",
            severity="high",
            description=f"Hidden text via CSS ({removed_count} elements)",
            action="removed",
            count=removed_count,
        ))
    return findings


@register_html
def sanitise_html_comments(soup: BeautifulSoup) -> list[Finding]:
    """Remove HTML comments, especially those containing instructions.

    All comments are stripped from the DOM. Findings are only reported
    for comments that appear to contain adversarial instructions (>20 chars
    and matching instruction-like patterns).
    """
    findings: list[Finding] = []
    suspicious_count = 0
    comments = soup.find_all(string=lambda s: isinstance(s, Comment))

    for comment in comments:
        text = str(comment).strip()
        if len(text) > 20 and _INJECTION_PATTERNS.search(text):
            suspicious_count += 1
        comment.extract()

    if suspicious_count:
        findings.append(Finding(
            category="content_injection",
            severity="medium",
            description=f"Suspicious HTML comments ({suspicious_count} with instruction-like content)",
            action="removed",
            count=suspicious_count,
        ))
    return findings


@register_html
def sanitise_suspicious_attrs(soup: BeautifulSoup) -> list[Finding]:
    """Clear data-* attributes, aria-labels, and alt text containing instructions.

    - data-* attributes: cleared if >50 chars and contain action verbs
    - aria-label: cleared if >100 chars and contain instruction patterns
    - alt text: cleared if >200 chars or contain prompt-like patterns
    - title attributes: cleared if >100 chars and contain instruction patterns

    Clears attribute values rather than removing elements to preserve DOM structure.
    """
    findings: list[Finding] = []
    cleared_count = 0

    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        attrs = dict(el.attrs)
        for attr_name, attr_val in attrs.items():
            if not isinstance(attr_val, str):
                continue

            suspicious = False

            # data-* attributes
            if attr_name.startswith("data-") and len(attr_val) > 50:
                if _ATTR_INSTRUCTION_PATTERN.search(attr_val):
                    suspicious = True

            # aria-label
            elif attr_name == "aria-label" and len(attr_val) > 100:
                if _ATTR_INSTRUCTION_PATTERN.search(attr_val):
                    suspicious = True

            # alt text - only flag if it contains injection patterns
            elif attr_name == "alt":
                if _INJECTION_PATTERNS.search(attr_val) or (
                    len(attr_val) > 50 and _ATTR_INSTRUCTION_PATTERN.search(attr_val)
                ):
                    suspicious = True

            # title
            elif attr_name == "title" and len(attr_val) > 100:
                if _ATTR_INSTRUCTION_PATTERN.search(attr_val):
                    suspicious = True

            if suspicious:
                el[attr_name] = ""
                cleared_count += 1

    if cleared_count:
        findings.append(Finding(
            category="content_injection",
            severity="high",
            description=f"Suspicious attributes cleared ({cleared_count})",
            action="removed",
            count=cleared_count,
        ))
    return findings


@register_html
def sanitise_unicode_tricks(soup: BeautifulSoup) -> list[Finding]:
    """Strip zero-width characters and bidirectional overrides from text nodes.

    Targets: U+200B-200F (zero-width), U+202A-202E (bidi embedding),
    U+2066-2069 (bidi isolate), U+FEFF (BOM).
    """
    findings: list[Finding] = []
    total_removed = 0

    for text_node in soup.find_all(string=True):
        if isinstance(text_node, Comment):
            continue
        original = str(text_node)
        cleaned = _UNICODE_TRICKS.sub("", original)
        if cleaned != original:
            total_removed += len(original) - len(cleaned)
            text_node.replace_with(cleaned)

    if total_removed:
        findings.append(Finding(
            category="content_injection",
            severity="medium",
            description=f"Unicode tricks removed ({total_removed} characters)",
            action="removed",
            count=total_removed,
        ))
    return findings


@register_html
def sanitise_hidden_iframes(soup: BeautifulSoup) -> list[Finding]:
    """Remove iframe elements with external sources.

    Iframes load external content that could contain adversarial injections.
    The html_to_markdown converter doesn't handle iframes, and extract.py's
    _STRIP_TAGS already removes them. This provides defence-in-depth.
    """
    findings: list[Finding] = []
    removed_count = 0

    for el in soup.find_all("iframe"):
        if not isinstance(el, Tag):
            continue
        has_src = el.get("src")
        has_zero_dim = (
            el.get("width") in ("0", "0px")
            or el.get("height") in ("0", "0px")
        )
        has_hiding_style = False
        style = el.get("style", "")
        if style:
            for _, pattern in _HIDING_PATTERNS:
                if pattern and pattern.search(style):
                    has_hiding_style = True
                    break

        if has_src or has_zero_dim or has_hiding_style:
            el.decompose()
            removed_count += 1

    if removed_count:
        findings.append(Finding(
            category="content_injection",
            severity="medium",
            description=f"Hidden/external iframes removed ({removed_count})",
            action="removed",
            count=removed_count,
        ))
    return findings


@register_html
def sanitise_hidden_inputs(soup: BeautifulSoup) -> list[Finding]:
    """Clear value attributes on hidden form inputs containing instructions.

    Hidden inputs carry form state (CSRF tokens, session IDs) but can also
    smuggle adversarial payloads in their value attributes.
    """
    findings: list[Finding] = []
    cleared_count = 0

    for el in soup.find_all("input", attrs={"type": "hidden"}):
        if not isinstance(el, Tag):
            continue
        value = el.get("value", "")
        if not isinstance(value, str):
            continue
        if len(value) > 50 and _ATTR_INSTRUCTION_PATTERN.search(value):
            el["value"] = ""
            cleared_count += 1

    if cleared_count:
        findings.append(Finding(
            category="content_injection",
            severity="high",
            description=f"Hidden input values cleared ({cleared_count})",
            action="removed",
            count=cleared_count,
        ))
    return findings


@register_html
def sanitise_css_class_hiding(soup: BeautifulSoup) -> list[Finding]:
    """Remove elements hidden via CSS classes or the hidden HTML attribute.

    Targets: .d-none, .hidden, .invisible, .visually-hidden, .sr-only,
    and the [hidden] attribute.

    Accessibility classes (.sr-only, .visually-hidden) are only removed if
    their text content matches injection patterns - legitimate screen reader
    text is preserved.
    """
    findings: list[Finding] = []
    removed_count = 0

    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        # Skip already-decomposed elements
        if el.parent is None:
            continue

        cls = el.get("class", [])
        cls_str = " ".join(cls) if isinstance(cls, list) else str(cls)
        has_hiding_class = bool(_HIDING_CLASSES.search(cls_str))
        has_hidden_attr = el.has_attr("hidden")

        if not has_hiding_class and not has_hidden_attr:
            continue

        text = el.get_text(strip=True)
        if len(text) < _HIDDEN_TEXT_MIN_CHARS:
            continue

        # Accessibility classes: only remove if text matches injection patterns
        is_a11y = bool(_ACCESSIBILITY_CLASSES.search(cls_str))
        if is_a11y and not has_hidden_attr:
            if not (_INJECTION_PATTERNS.search(text) or _ATTR_INSTRUCTION_PATTERN.search(text)):
                continue

        el.decompose()
        removed_count += 1

    if removed_count:
        findings.append(Finding(
            category="content_injection",
            severity="high",
            description=f"CSS class-hidden elements removed ({removed_count})",
            action="removed",
            count=removed_count,
        ))
    return findings


@register_html
def sanitise_meta_injection(soup: BeautifulSoup) -> list[Finding]:
    """Clear custom meta tags containing instruction-like content.

    Standard meta tags (description, og:*, twitter:*, charset, http-equiv)
    are always preserved. Custom meta tags with long content matching
    instruction patterns are cleared.
    """
    findings: list[Finding] = []
    cleared_count = 0

    for el in soup.find_all("meta"):
        if not isinstance(el, Tag):
            continue

        # Skip standard meta tags
        name = el.get("name", "")
        if isinstance(name, str) and name.lower() in _STANDARD_META_NAMES:
            continue
        prop = el.get("property", "")
        if isinstance(prop, str) and any(
            prop.lower().startswith(p) for p in _STANDARD_META_PROPERTY_PREFIXES
        ):
            continue
        if el.has_attr("charset") or el.has_attr("http-equiv"):
            continue

        content = el.get("content", "")
        if not isinstance(content, str):
            continue
        if len(content) > 100 and _ATTR_INSTRUCTION_PATTERN.search(content):
            el["content"] = ""
            cleared_count += 1

    if cleared_count:
        findings.append(Finding(
            category="content_injection",
            severity="medium",
            description=f"Meta tag content cleared ({cleared_count})",
            action="removed",
            count=cleared_count,
        ))
    return findings


# ---------------------------------------------------------------------------
# Text-level sanitisers (Phase 2)
# ---------------------------------------------------------------------------


@register_text
def sanitise_prompt_injection(text: str) -> tuple[str, list[Finding]]:
    """Detect and remove lines containing prompt injection patterns.

    Operates line-by-line. Only removes lines shorter than 200 characters
    to avoid stripping legitimate article paragraphs that discuss prompt
    injection (the "short-line bias").

    Also detects delimiter injection: isolated code fences, XML-like tags,
    and separator lines that appear to frame injected content.
    """
    findings: list[Finding] = []
    lines = text.split("\n")
    clean_lines: list[str] = []
    removed_count = 0
    delimiter_count = 0

    for line in lines:
        stripped = line.strip()

        # Skip empty lines - always keep them
        if not stripped:
            clean_lines.append(line)
            continue

        # Check for prompt injection patterns (short-line bias)
        if len(stripped) < 200 and _INJECTION_PATTERNS.search(stripped):
            removed_count += 1
            continue

        # Check for delimiter injection
        if len(stripped) < 80 and _DELIMITER_PATTERN.fullmatch(stripped):
            # Only strip if surrounded by suspicious context.
            # Isolated delimiters in normal content are fine.
            # We'll strip XML-like system/instruction tags always.
            if re.match(
                r"</?(?:system|instructions?|context|prompt)>",
                stripped,
                re.IGNORECASE,
            ):
                delimiter_count += 1
                continue

        clean_lines.append(line)

    if removed_count:
        findings.append(Finding(
            category="prompt_injection",
            severity="high",
            description=f"Prompt injection patterns removed ({removed_count} lines)",
            action="removed",
            count=removed_count,
        ))
    if delimiter_count:
        findings.append(Finding(
            category="prompt_injection",
            severity="medium",
            description=f"Delimiter injection tags removed ({delimiter_count} lines)",
            action="removed",
            count=delimiter_count,
        ))

    return "\n".join(clean_lines), findings


@register_text
def sanitise_semantic_manipulation(text: str) -> tuple[str, list[Finding]]:
    """Flag (but do not remove) semantic manipulation patterns.

    Detects:
    - Urgency clusters: lines with 2+ urgency words
    - Authority claims: patterns asserting false authority or confidentiality

    Content is returned unchanged. Findings are reported with action="flagged".
    """
    findings: list[Finding] = []
    urgency_count = 0
    authority_count = 0

    for line in text.split("\n"):
        stripped = line.strip().lower()
        if not stripped:
            continue

        # Urgency cluster detection (2+ urgency words in one line)
        urgency_hits = sum(1 for word in _URGENCY_WORDS if word in stripped)
        if urgency_hits >= 2:
            urgency_count += 1

        # Authority claim detection
        if _AUTHORITY_PATTERNS.search(line):
            authority_count += 1

    if urgency_count:
        findings.append(Finding(
            category="semantic_manipulation",
            severity="medium",
            description=f"Urgency language clusters ({urgency_count} lines)",
            action="flagged",
            count=urgency_count,
        ))
    if authority_count:
        findings.append(Finding(
            category="semantic_manipulation",
            severity="medium",
            description=f"Authority claims ({authority_count} lines)",
            action="flagged",
            count=authority_count,
        ))

    # Content is never modified by this sanitiser
    return text, findings


@register_text
def sanitise_homoglyphs(text: str) -> tuple[str, list[Finding]]:
    """Detect prompt injection using homoglyph evasion (Cyrillic/Greek lookalikes).

    Maps visually similar characters to Latin equivalents, then re-checks
    for injection patterns. Only operates on mixed-script lines (containing
    both Latin characters and homoglyph characters) to avoid false positives
    on legitimate Cyrillic/Greek text.
    """
    findings: list[Finding] = []
    lines = text.split("\n")
    clean_lines: list[str] = []
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            clean_lines.append(line)
            continue

        # Fast bail-out: no homoglyphs in this line
        if not _HOMOGLYPH_CHARS.search(stripped):
            clean_lines.append(line)
            continue

        # Mixed-script check: line must contain Latin chars too
        has_latin = any("a" <= c.lower() <= "z" for c in stripped)
        if not has_latin:
            clean_lines.append(line)
            continue

        # Already caught by prompt_injection sanitiser? Skip
        if _INJECTION_PATTERNS.search(stripped):
            clean_lines.append(line)
            continue

        # Normalise homoglyphs and re-check
        normalised = stripped
        for char, replacement in _HOMOGLYPH_MAP.items():
            normalised = normalised.replace(char, replacement)

        if len(normalised) < 200 and _INJECTION_PATTERNS.search(normalised):
            removed_count += 1
            continue

        clean_lines.append(line)

    if removed_count:
        findings.append(Finding(
            category="prompt_injection",
            severity="high",
            description=f"Homoglyph evasion detected ({removed_count} lines)",
            action="removed",
            count=removed_count,
        ))

    return "\n".join(clean_lines), findings


@register_text
def sanitise_markdown_exfiltration(text: str) -> tuple[str, list[Finding]]:
    """Flag markdown image/link patterns that could exfiltrate data.

    Detects images pointing to IP addresses, URLs with exfiltration-associated
    query parameters, or dynamic endpoints. Flags only, never removes -
    legitimate images may have query parameters.
    """
    findings: list[Finding] = []
    flagged_count = 0

    for match in _MD_IMAGE_LINK.finditer(text):
        url = match.group(2)
        if _SUSPICIOUS_URL.search(url):
            flagged_count += 1

    if flagged_count:
        findings.append(Finding(
            category="content_injection",
            severity="medium",
            description=f"Suspicious image URLs ({flagged_count})",
            action="flagged",
            count=flagged_count,
        ))

    # Content is never modified
    return text, findings


@register_text
def sanitise_html_entity_evasion(text: str) -> tuple[str, list[Finding]]:
    """Detect prompt injection using HTML entity encoding to evade patterns.

    Decodes HTML entities and re-checks for injection patterns. Only removes
    lines where the decoded version matches but the original did not -
    indicating deliberate entity-based evasion.
    """
    findings: list[Finding] = []
    lines = text.split("\n")
    clean_lines: list[str] = []
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            clean_lines.append(line)
            continue

        # Fast bail-out: no HTML entities
        if not _HTML_ENTITY.search(stripped):
            clean_lines.append(line)
            continue

        # Already caught by upstream sanitisers? Skip
        if _INJECTION_PATTERNS.search(stripped):
            clean_lines.append(line)
            continue

        # Decode entities and re-check
        decoded = html.unescape(stripped)
        if decoded != stripped and len(decoded) < 200 and _INJECTION_PATTERNS.search(decoded):
            removed_count += 1
            continue

        clean_lines.append(line)

    if removed_count:
        findings.append(Finding(
            category="prompt_injection",
            severity="high",
            description=f"HTML entity evasion detected ({removed_count} lines)",
            action="removed",
            count=removed_count,
        ))

    return "\n".join(clean_lines), findings


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


def sanitise_html(html: str) -> SanitiseResult:
    """Phase 1: Run all HTML-level sanitisers.

    Parses HTML, runs each registered sanitiser on the soup, and returns
    the cleaned HTML string along with all findings.
    """
    soup = BeautifulSoup(html, "lxml")
    all_findings: list[Finding] = []

    for sanitiser in _html_sanitisers:
        findings = sanitiser(soup)
        all_findings.extend(findings)

    body = soup.find("body")
    cleaned = str(body) if body else str(soup)
    return SanitiseResult(content=cleaned, findings=all_findings)


def sanitise_text(text: str) -> SanitiseResult:
    """Phase 2: Run all text-level sanitisers.

    Runs each registered sanitiser sequentially on the text, accumulating
    findings from each pass.
    """
    all_findings: list[Finding] = []

    for sanitiser in _text_sanitisers:
        text, findings = sanitiser(text)
        all_findings.extend(findings)

    return SanitiseResult(content=text, findings=all_findings)
