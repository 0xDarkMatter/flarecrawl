"""OpenAPI/Swagger spec discovery and download for Flarecrawl."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass(slots=True)
class SpecDiscovery:
    """A discovered OpenAPI/Swagger spec location."""
    url: str
    source: str  # "link" | "swagger-ui" | "common-path" | "text-match"
    format: str  # "json" | "yaml" | "unknown"
    confidence: float  # 0.0 - 1.0


@dataclass(slots=True)
class SpecValidation:
    """Quick validation of an OpenAPI/Swagger spec."""
    valid: bool
    version: str | None
    title: str | None
    endpoint_count: int | None


@dataclass(slots=True)
class SpecResult:
    """Result of downloading a spec."""
    url: str
    validation: SpecValidation
    path: Path | None
    size: int


_SPEC_LINK_PATTERNS = re.compile(
    r"(swagger\.json|openapi\.json|openapi\.ya?ml|api-docs|"
    r"swagger/.*\.json|openapi/.*\.(json|ya?ml))",
    re.IGNORECASE,
)

COMMON_SPEC_PATHS = [
    "/swagger/v1/swagger.json",
    "/swagger.json",
    "/openapi.json",
    "/openapi.yaml",
    "/api-docs",
    "/v2/api-docs",
    "/v3/api-docs",
    "/api/swagger.json",
    "/api/openapi.json",
    "/_api/swagger.json",
]


def discover_specs(html: str, base_url: str) -> list[SpecDiscovery]:
    """Parse HTML for API spec links from multiple sources."""
    soup = BeautifulSoup(html, "lxml")
    specs: list[SpecDiscovery] = []
    seen: set[str] = set()

    def _add(url: str, source: str, confidence: float):
        if url in seen:
            return
        seen.add(url)
        fmt = "json" if url.endswith(".json") else "yaml" if re.search(r"\.ya?ml$", url) else "unknown"
        specs.append(SpecDiscovery(url=url, source=source, format=fmt, confidence=confidence))

    # 1. <a href> links matching spec patterns
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if _SPEC_LINK_PATTERNS.search(href):
            _add(urljoin(base_url, href), "link", 0.9)

    # 2. <script> tags with SwaggerUI config
    for script in soup.find_all("script"):
        text = script.get_text()
        if not text:
            continue
        url_match = re.search(r'url\s*:\s*["\']([^"\']+\.(?:json|ya?ml))["\']', text)
        if url_match:
            _add(urljoin(base_url, url_match.group(1)), "swagger-ui", 0.95)
        urls_match = re.search(r'urls?\s*:\s*\[([^\]]+)\]', text)
        if urls_match:
            for m in re.finditer(r'url\s*:\s*["\']([^"\']+)["\']', urls_match.group(1)):
                _add(urljoin(base_url, m.group(1)), "swagger-ui", 0.9)

    # 3. <link> or <meta> tags with API documentation rels
    for link in soup.find_all("link", rel=True):
        rel_list = link.get("rel", [])
        rel = " ".join(str(r) for r in rel_list) if isinstance(rel_list, list) else str(rel_list)
        if "api" in rel.lower() or "openapi" in rel.lower():
            href = link.get("href")
            if href:
                _add(urljoin(base_url, str(href)), "link", 0.85)

    # 4. Text content near links matching spec keywords
    _spec_keywords = re.compile(r"download\s+openapi|api\s+specification|swagger|openapi\s+spec", re.IGNORECASE)
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if _spec_keywords.search(text):
            href = str(a["href"])
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                _add(urljoin(base_url, href), "text-match", 0.7)

    return specs


def probe_common_paths(base_url: str, session: httpx.Client | None = None) -> list[SpecDiscovery]:
    """HEAD-check common spec paths to find API specs."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    client = session or httpx.Client(timeout=10, follow_redirects=True)
    close = session is None
    found: list[SpecDiscovery] = []

    try:
        for path in COMMON_SPEC_PATHS:
            url = f"{origin}{path}"
            try:
                resp = client.head(url)
                if resp.status_code < 400:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct or "yaml" in ct or "xml" in ct or "text" in ct:
                        fmt = "json" if "json" in ct else "yaml" if "yaml" in ct else "unknown"
                        found.append(SpecDiscovery(url=url, source="common-path", format=fmt, confidence=0.8))
            except httpx.HTTPError:
                continue
    finally:
        if close:
            client.close()

    return found


def validate_spec(content: str | dict) -> SpecValidation:
    """Quick check for openapi/swagger top-level keys."""
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            try:
                import yaml
                data = yaml.safe_load(content)
            except Exception:
                return SpecValidation(valid=False, version=None, title=None, endpoint_count=None)
    else:
        data = content

    if not isinstance(data, dict):
        return SpecValidation(valid=False, version=None, title=None, endpoint_count=None)

    version = data.get("openapi") or data.get("swagger")
    title = None
    info = data.get("info")
    if isinstance(info, dict):
        title = info.get("title")

    endpoint_count = None
    paths = data.get("paths")
    if isinstance(paths, dict):
        endpoint_count = sum(
            sum(1 for k in v if k in {"get", "post", "put", "patch", "delete", "options", "head"})
            for v in paths.values() if isinstance(v, dict)
        )

    return SpecValidation(
        valid=bool(version),
        version=str(version) if version else None,
        title=title,
        endpoint_count=endpoint_count,
    )


def download_spec(url: str, session: httpx.Client | None = None,
                  output_path: Path | None = None) -> SpecResult:
    """Download, validate, and optionally save an API spec."""
    client = session or httpx.Client(timeout=30, follow_redirects=True)
    close = session is None

    try:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.text
        size = len(content.encode("utf-8"))
        validation = validate_spec(content)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")

        return SpecResult(url=url, validation=validation, path=output_path, size=size)
    finally:
        if close:
            client.close()
