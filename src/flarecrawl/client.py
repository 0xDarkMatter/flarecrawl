"""Cloudflare Browser Rendering API client."""

from __future__ import annotations

import time
from typing import Iterator

import httpx

from .config import get_account_id, get_api_token, track_usage


class FlareCrawlError(Exception):
    """API error with Fabric-compatible error code."""

    def __init__(self, message: str, code: str = "API_ERROR", status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class Client:
    """Cloudflare Browser Rendering REST API client."""

    BASE_URL = "https://api.cloudflare.com/client/v4/accounts"
    TIMEOUT = 120  # Browser rendering can be slow
    MAX_RETRIES = 3
    RETRY_CODES = {429, 503, 502}
    # Endpoints whose responses can be cached
    CACHEABLE_ENDPOINTS = {"markdown", "content", "links", "json"}

    def __init__(self, account_id: str | None = None, api_token: str | None = None,
                 cache_ttl: int = 3600):
        self.account_id = account_id or get_account_id()
        self.api_token = api_token or get_api_token()
        # Track cumulative browser time (ms) from X-Browser-Ms-Used headers
        self.browser_ms_used = 0
        # Cache TTL in seconds (0 = disabled)
        self.cache_ttl = cache_ttl

    @property
    def _base(self) -> str:
        return f"{self.BASE_URL}/{self.account_id}/browser-rendering"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _handle_error(self, response: httpx.Response) -> None:
        """Map HTTP status to Fabric error codes."""
        status = response.status_code

        # Try to extract CF error message
        try:
            data = response.json()
            errors = data.get("errors", [])
            if errors:
                message = errors[0].get("message", "Unknown error")
                raise FlareCrawlError(message, "API_ERROR", status)
        except (ValueError, KeyError):
            pass

        error_map = {
            400: ("Invalid request parameters", "VALIDATION_ERROR"),
            401: ("Invalid API token", "AUTH_REQUIRED"),
            403: ("Access forbidden — check token permissions (need 'Browser Rendering - Edit')", "FORBIDDEN"),
            404: ("Resource not found", "NOT_FOUND"),
            429: ("Rate limit exceeded", "RATE_LIMITED"),
            500: ("Cloudflare server error", "SERVER_ERROR"),
            503: ("Service unavailable", "SERVICE_UNAVAILABLE"),
        }

        if status in error_map:
            message, code = error_map[status]
            raise FlareCrawlError(message, code, status)

        raise FlareCrawlError(f"HTTP {status}: {response.text}", "API_ERROR", status)

    def _track_browser_time(self, response: httpx.Response) -> None:
        """Track browser time from X-Browser-Ms-Used header."""
        ms = response.headers.get("x-browser-ms-used")
        if ms:
            try:
                ms_int = int(float(ms))
                self.browser_ms_used += ms_int
                track_usage(ms_int)
            except (ValueError, TypeError):
                pass

    def _retry_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute request with retry on 429/503/502."""
        last_exc = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = getattr(httpx, method)(url, **kwargs)
                if response.status_code not in self.RETRY_CODES or attempt == self.MAX_RETRIES - 1:
                    return response
                # Retry after backoff
                retry_after = response.headers.get("retry-after")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
                time.sleep(min(wait, 30))
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt == self.MAX_RETRIES - 1:
                    raise FlareCrawlError(
                        f"Request timed out after {self.TIMEOUT}s ({attempt + 1} attempts)",
                        "TIMEOUT",
                    ) from e
                time.sleep(2 ** attempt)
        # Shouldn't reach here, but safety net
        raise FlareCrawlError("Max retries exceeded", "TIMEOUT") from last_exc

    def _post_json(self, endpoint: str, body: dict) -> dict:
        """POST returning JSON response with retry and optional caching."""
        # Check cache for cacheable endpoints
        if self.cache_ttl > 0 and endpoint in self.CACHEABLE_ENDPOINTS:
            from . import cache
            cached = cache.get(endpoint, body, ttl=self.cache_ttl)
            if cached is not None:
                return cached

        response = self._retry_request(
            "post",
            f"{self._base}/{endpoint}",
            headers=self._headers(),
            json=body,
            timeout=self.TIMEOUT,
        )
        if response.status_code >= 400:
            self._handle_error(response)
        self._track_browser_time(response)
        result = response.json()

        # Store in cache
        if self.cache_ttl > 0 and endpoint in self.CACHEABLE_ENDPOINTS:
            from . import cache
            cache.put(endpoint, body, result)

        return result

    def _post_binary(self, endpoint: str, body: dict) -> tuple[bytes, dict]:
        """POST returning binary response (screenshot, pdf) with retry."""
        response = self._retry_request(
            "post",
            f"{self._base}/{endpoint}",
            headers=self._headers(),
            json=body,
            timeout=self.TIMEOUT,
        )
        if response.status_code >= 400:
            self._handle_error(response)
        self._track_browser_time(response)
        return response.content, dict(response.headers)

    def _get_json(self, endpoint: str, params: dict | None = None) -> dict:
        """GET returning JSON response with retry."""
        response = self._retry_request(
            "get",
            f"{self._base}/{endpoint}",
            headers=self._headers(),
            params=params,
            timeout=self.TIMEOUT,
        )
        if response.status_code >= 400:
            self._handle_error(response)
        self._track_browser_time(response)
        return response.json()

    def _delete(self, endpoint: str) -> dict:
        """DELETE request."""
        response = httpx.delete(
            f"{self._base}/{endpoint}",
            headers=self._headers(),
            timeout=self.TIMEOUT,
        )
        if response.status_code >= 400:
            self._handle_error(response)
        try:
            return response.json()
        except ValueError:
            return {"success": True}

    # ------------------------------------------------------------------
    # Body builder: flat kwargs → nested CF API JSON
    # ------------------------------------------------------------------

    @staticmethod
    def _build_body(url: str | None = None, html: str | None = None, **kwargs) -> dict:
        """Build API request body from flat kwargs."""
        body: dict = {}

        if url:
            body["url"] = url
        if html:
            body["html"] = html

        # gotoOptions
        goto = {}
        if "wait_until" in kwargs:
            goto["waitUntil"] = kwargs.pop("wait_until")
        if "timeout" in kwargs:
            goto["timeout"] = kwargs.pop("timeout")
        if goto:
            body["gotoOptions"] = goto

        # waitForSelector
        if "wait_for" in kwargs:
            body["waitForSelector"] = {"selector": kwargs.pop("wait_for")}

        # userAgent
        if "user_agent" in kwargs:
            body["userAgent"] = kwargs.pop("user_agent")

        # cookies
        if "cookies" in kwargs:
            body["cookies"] = kwargs.pop("cookies")

        # authenticate
        if "authenticate" in kwargs:
            body["authenticate"] = kwargs.pop("authenticate")

        # rejectResourceTypes
        if "reject_resources" in kwargs:
            body["rejectResourceTypes"] = kwargs.pop("reject_resources")

        # screenshotOptions
        screenshot_opts = {}
        if "full_page" in kwargs:
            screenshot_opts["fullPage"] = kwargs.pop("full_page")
        if "quality" in kwargs:
            screenshot_opts["quality"] = kwargs.pop("quality")
        if "image_type" in kwargs:
            screenshot_opts["type"] = kwargs.pop("image_type")
        if "omit_background" in kwargs:
            screenshot_opts["omitBackground"] = kwargs.pop("omit_background")
        if screenshot_opts:
            body["screenshotOptions"] = screenshot_opts

        # viewport
        viewport = {}
        if "width" in kwargs:
            viewport["width"] = kwargs.pop("width")
        if "height" in kwargs:
            viewport["height"] = kwargs.pop("height")
        if "device_scale_factor" in kwargs:
            viewport["deviceScaleFactor"] = kwargs.pop("device_scale_factor")
        if viewport:
            body["viewport"] = viewport

        # selector (for screenshot of specific element)
        if "selector" in kwargs:
            body["selector"] = kwargs.pop("selector")

        # pdfOptions
        pdf_opts = {}
        if "landscape" in kwargs:
            pdf_opts["landscape"] = kwargs.pop("landscape")
        if "print_background" in kwargs:
            pdf_opts["printBackground"] = kwargs.pop("print_background")
        if "scale" in kwargs:
            pdf_opts["scale"] = kwargs.pop("scale")
        if "paper_format" in kwargs:
            pdf_opts["format"] = kwargs.pop("paper_format")
        if pdf_opts:
            body["pdfOptions"] = pdf_opts

        # addStyleTag
        if "style_tag" in kwargs:
            body["addStyleTag"] = [{"content": kwargs.pop("style_tag")}]

        # Elements (for scrape)
        if "elements" in kwargs:
            body["elements"] = kwargs.pop("elements")

        # Links options
        if "visible_only" in kwargs:
            body["visibleLinksOnly"] = kwargs.pop("visible_only")
        if "internal_only" in kwargs:
            body["excludeExternalLinks"] = kwargs.pop("internal_only")

        # JSON/extract options
        if "prompt" in kwargs:
            body["prompt"] = kwargs.pop("prompt")
        if "response_format" in kwargs:
            body["response_format"] = kwargs.pop("response_format")

        # Crawl-specific options
        if "limit" in kwargs:
            body["limit"] = kwargs.pop("limit")
        if "depth" in kwargs:
            body["depth"] = kwargs.pop("depth")
        if "formats" in kwargs:
            body["formats"] = kwargs.pop("formats")
        if "render" in kwargs:
            body["render"] = kwargs.pop("render")
        if "source" in kwargs:
            body["source"] = kwargs.pop("source")
        if "max_age" in kwargs:
            body["maxAge"] = kwargs.pop("max_age")
        if "modified_since" in kwargs:
            body["modifiedSince"] = kwargs.pop("modified_since")

        # Crawl options (nested under 'options')
        crawl_opts = {}
        if "include_external" in kwargs:
            crawl_opts["includeExternalLinks"] = kwargs.pop("include_external")
        if "include_subdomains" in kwargs:
            crawl_opts["includeSubdomains"] = kwargs.pop("include_subdomains")
        if "include_patterns" in kwargs:
            crawl_opts["includePatterns"] = kwargs.pop("include_patterns")
        if "exclude_patterns" in kwargs:
            crawl_opts["excludePatterns"] = kwargs.pop("exclude_patterns")
        if crawl_opts:
            body["options"] = crawl_opts

        # Pass through any remaining kwargs directly
        for k, v in kwargs.items():
            if v is not None:
                body[k] = v

        return body

    # ------------------------------------------------------------------
    # Single-page endpoints
    # ------------------------------------------------------------------

    def get_content(self, url: str | None = None, **kwargs) -> str:
        """Fetch rendered HTML. Returns HTML string."""
        body = self._build_body(url=url, **kwargs)
        result = self._post_json("content", body)
        return result.get("result", result)

    def get_markdown(self, url: str, **kwargs) -> str:
        """Extract markdown from page. Returns markdown string."""
        body = self._build_body(url=url, **kwargs)
        result = self._post_json("markdown", body)
        return result.get("result", result)

    def take_screenshot(self, url: str, **kwargs) -> bytes:
        """Capture screenshot. Returns binary PNG/JPEG."""
        body = self._build_body(url=url, **kwargs)
        data, _ = self._post_binary("screenshot", body)
        return data

    def render_pdf(self, url: str, **kwargs) -> bytes:
        """Render page as PDF. Returns binary PDF."""
        body = self._build_body(url=url, **kwargs)
        data, _ = self._post_binary("pdf", body)
        return data

    def take_snapshot(self, url: str, **kwargs) -> dict:
        """Snapshot: HTML + base64 screenshot. Returns dict with 'content' and 'screenshot'."""
        body = self._build_body(url=url, **kwargs)
        result = self._post_json("snapshot", body)
        return result.get("result", result)

    def get_links(self, url: str, **kwargs) -> list[str]:
        """Extract links from page. Returns list of URLs."""
        body = self._build_body(url=url, **kwargs)
        result = self._post_json("links", body)
        return result.get("result", result)

    # ------------------------------------------------------------------
    # Structured extraction
    # ------------------------------------------------------------------

    def scrape(self, url: str, selectors: list[str], **kwargs) -> list[dict]:
        """Scrape elements by CSS selectors. Returns list of element results."""
        elements = [{"selector": s} for s in selectors]
        body = self._build_body(url=url, elements=elements, **kwargs)
        result = self._post_json("scrape", body)
        return result.get("result", result)

    def extract_json(self, url: str, prompt: str, response_format: dict | None = None,
                     **kwargs) -> dict:
        """AI-powered structured extraction via /json endpoint."""
        extra = {"prompt": prompt}
        if response_format:
            extra["response_format"] = response_format
        body = self._build_body(url=url, **extra, **kwargs)
        result = self._post_json("json", body)
        return result.get("result", result)

    # ------------------------------------------------------------------
    # Crawl lifecycle
    # ------------------------------------------------------------------

    def crawl_start(self, url: str, **kwargs) -> str:
        """Start a crawl job. Returns job ID."""
        body = self._build_body(url=url, **kwargs)
        result = self._post_json("crawl", body)
        # CF returns {"success": true, "result": "job-id-string"}
        job_id = result.get("result", result)
        if isinstance(job_id, dict):
            job_id = job_id.get("id", str(job_id))
        return str(job_id)

    def crawl_status(self, job_id: str) -> dict:
        """Get crawl job status (lightweight, no records)."""
        result = self._get_json(f"crawl/{job_id}", params={"limit": 0})
        return result.get("result", result)

    def crawl_get(self, job_id: str, limit: int | None = None,
                  cursor: str | None = None, status: str | None = None) -> dict:
        """Get crawl results with pagination."""
        params = {}
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        if status is not None:
            params["status"] = status
        result = self._get_json(f"crawl/{job_id}", params=params or None)
        return result.get("result", result)

    def crawl_get_all(self, job_id: str, status: str | None = None) -> Iterator[dict]:
        """Generator that auto-paginates through all crawl records."""
        cursor = None
        while True:
            result = self.crawl_get(job_id, limit=100, cursor=cursor, status=status)
            records = result.get("records", [])
            for record in records:
                yield record
            cursor = result.get("cursor")
            if not cursor or not records:
                break

    def crawl_wait(self, job_id: str, timeout: int = 600, poll_interval: float = 5,
                   callback=None) -> dict:
        """Poll crawl until complete. Returns final status dict.

        callback(status_dict) is called each poll for progress updates.
        """
        start = time.time()
        interval = poll_interval
        terminal_states = {
            "completed", "errored", "cancelled_by_user",
            "cancelled_due_to_timeout", "cancelled_due_to_limits",
        }

        while True:
            elapsed = time.time() - start
            if timeout and elapsed > timeout:
                raise FlareCrawlError(
                    f"Crawl timed out after {timeout}s", "TIMEOUT"
                )

            status = self.crawl_status(job_id)
            if callback:
                callback(status)

            if status.get("status") in terminal_states:
                return status

            time.sleep(interval)
            interval = min(interval * 1.5, 30)  # Exponential backoff, cap at 30s

    def crawl_cancel(self, job_id: str) -> bool:
        """Cancel a running crawl job."""
        result = self._delete(f"crawl/{job_id}")
        return result.get("success", True)

    # ------------------------------------------------------------------
    # Raw passthrough
    # ------------------------------------------------------------------

    def post_raw(self, endpoint: str, body: dict) -> dict:
        """Raw POST for --body passthrough. Returns full API response."""
        return self._post_json(endpoint, body)
