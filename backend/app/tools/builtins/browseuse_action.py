"""MERIDAN Built-in Tool: browseuse_action — Phase 8 Integration Bus.

Runs the BrowseUse action to fetch web page content with structured
output, artifacts, and built-in SSRF protection + timeouts.

The tool makes an HTTP request to the BrowseUse service (or a local
proxy) and returns a normalized ToolResult with extracted content,
links, and optional screenshot data.

SSRF protection: optional domain allowlist via BROWSEUSE_ALLOWED_DOMAINS.
If set, only requests to those domains are allowed. Scheme-only
blocking (no data:, file:, javascript:, about:).
Safe URL allowlist prevents probe of internal services.
Output capping prevents memory exhaustion.
"""

from typing import Any, Dict, Optional, List
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

# ═════════════════════════════════════════════════════════════════════════
# SSRF protect: allowlist
# ══════════════════════════════════════════════════════════════════════════

MAX_RESPONSE_BYTES = 2_000_000  # 2 MB
DEFAULT_TIMEOUT = 30
#: If no BROWSEUSE_ENDPOINT is configured, max total execution time
#: including any local subprocess BrowseUse call.
MAX_TOTAL_TIMEOUT_SECONDS = 120

# ══════════════════════════════════════════════════════════════════════════
# Input schema
# ══════════════════════════════════════════════════════════════════════════


class BrowseuseActionInput(BaseModel):
    """Input schema for the browseuse_action tool."""

    action_type: str = Field(
        ...,
        description="Type of action: 'visit', 'click', 'fill', 'extract', 'screenshot'",
    )
    url: str = Field(..., description="Target URL to act upon")
    selectors: Optional[Dict[str, str]] = Field(
        default=None,
        description="CSS selectors for extract/click/fill actions",
    )
    text: Optional[str] = Field(
        default=None,
        description="Text to fill in input fields",
    )
    screenshot: bool = Field(
        default=False,
        description="Whether to capture a screenshot (base64 PNG)",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT,
        ge=1,
        le=300,
        description="Request timeout in seconds",
    )


# ═════════════════════════════════════════════════════════════════════════
# Tool implementation
# ══════════════════════════════════════════════════════════════════════════


class BrowseuseActionTool(BaseTool):
    """Run a BrowseUse action against a target URL."""

    name = "browseuse_action"
    description = (
        "Execute a BrowseUse action (visit, click, fill, extract, screenshot) "
        "against a target URL. Returns structured output, artifacts, and metadata."
    )
    input_schema = BrowseuseActionInput
    default_timeout_seconds = DEFAULT_TIMEOUT

    def _is_allowed_url(self, url: str) -> bool:
        """Check whether a URL is allowed by the domain allowlist."""
        allowed = settings.BROWSEUSE_ALLOWED_DOMAINS
        if not allowed:
            # No allowlist configured — unrestricted (dev mode)
            # Still block dangerous schemes
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            if scheme in ("data", "file", "javascript", "about"):
                return False
            return True

        host = urlparse(url).hostname or ""
        host = host.lower()

        for domain in allowed.split(","):
            domain = domain.strip().lower()
            if not domain:
                continue
            if host == domain or host.endswith("." + domain):
                return True

        # Block dangerous schemes even if allowlist matches
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme in ("data", "file", "javascript", "about"):
            return False

        return False

    def _validate_url(self, url: str) -> None:
        """Validate URL scheme and enforce the domain allowlist."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolError(
                f"Only http/https URLs are allowed, got scheme '{parsed.scheme}'",
                code="invalid_url",
            )
        if not parsed.hostname:
            raise ToolError("URL must include a hostname", code="invalid_url")
        if not self._is_allowed_url(url):
            raise ToolError(
                f"Domain '{parsed.hostname}' is not in the allowed domains list",
                code="domain_not_allowed",
            )

    async def execute(self, input_data: BrowseuseActionInput) -> ToolResult:
        """Execute the BrowseUse action."""
        # Validate URL
        self._validate_url(input_data.url)

        # NOTE: In a full implementation, this would call the BrowseUse
        # service API or a local subprocess. For Phase 8 we provide a
        # mocked/local HTTP wrapper that proxies to an external service
        # while enforcing the allowlist and timeout.
        #
        # If BROWSEUSE_ENDPOINT is set we POST to it; otherwise we
        # return a structured placeholder that the LLM can work with.

        endpoint = settings.BROWSEUSE_ENDPOINT

        if endpoint:
            # Remote BrowseUse service — POST the action payload
            try:
                import httpx
            except ImportError as exc:
                raise ToolError(
                    "httpx is not installed. Run `pip install httpx`.",
                    code="missing_dependency",
                ) from exc

            payload: Dict[str, Any] = {
                "action_type": input_data.action_type,
                "url": input_data.url,
            }
            if input_data.selectors:
                payload["selectors"] = input_data.selectors
            if input_data.text is not None:
                payload["text"] = input_data.text
            payload["screenshot"] = input_data.screenshot

            try:
                async with httpx.AsyncClient(
                    timeout=input_data.timeout_seconds,
                ) as client:
                    resp = await client.post(
                        endpoint,
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                raise ToolError(
                    f"BrowseUse request timed out after {input_data.timeout_seconds}s",
                    code="timeout",
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise ToolError(
                    f"BrowseUse request failed: {str(exc)}",
                    code="http_error",
                    retryable=True,
                ) from exc

            if resp.status_code != 200:
                raise ToolError(
                    f"BrowseUse API returned status {resp.status_code}: {resp.text[:500]}",
                    code="browseuse_error",
                )

            try:
                result_data = resp.json()
            except Exception as exc:
                raise ToolError(
                    f"BrowseUse returned invalid JSON: {str(exc)}",
                    code="invalid_response",
                ) from exc

            # Cap output size
            data = result_data.get("data", {})
            content = data.get("content") or data.get("text") or ""
            if isinstance(content, str) and len(content) > MAX_RESPONSE_BYTES:
                content = content[:MAX_RESPONSE_BYTES] + "\n...[truncated]"

            return ToolResult(
                ok=True,
                data={
                    "action_type": input_data.action_type,
                    "url": input_data.url,
                    "content": content,
                    "screenshot_b64": data.get("screenshot_b64"),
                    "links": data.get("links", []),
                    "title": data.get("title"),
                    "metadata": data.get("metadata", {}),
                },
                metadata={
                    "action_type": input_data.action_type,
                    "url": input_data.url,
                    "status_code": resp.status_code,
                    "truncated": isinstance(content, str) and len(result_data.get("data", {}).get("content", "" or "")) > MAX_RESPONSE_BYTES,
                },
            )
        else:
            # No remote endpoint configured — return a structured placeholder
            # that the LLM can understand. The actual BrowseUse logic would
            # be plugged in later; for now we just validate and return a
            # deterministic scaffold.
            #
            # NOTE: A future Phase 9 implementation could invoke a local
            # BrowseUse subprocess here with enforceable timeouts via
            # subprocess timeout and kill signals.
            return ToolResult(
                ok=True,
                data={
                    "action_type": input_data.action_type,
                    "url": input_data.url,
                    "content": f"[browseuse placeholder: action={input_data.action_type}, url={input_data.url}]",
                    "screenshot_b64": None,
                    "links": [],
                    "title": None,
                    "metadata": {
                        "note": "No BROWSEUSE_ENDPOINT configured; returning scaffold.",
                    },
                },
                metadata={
                    "action_type": input_data.action_type,
                    "url": input_data.url,
                    "status_code": 200,
                    "placeholder": True,
                    "max_total_timeout_s": MAX_TOTAL_TIMEOUT_SECONDS,
                },
            )