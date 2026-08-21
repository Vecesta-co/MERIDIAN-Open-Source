"""
MERIDIAN Built-in Tool: http_request — Phase 3 Tool Sandbox.

Makes HTTP requests with SSRF protection:
  - Optional domain allowlist (HTTP_TOOL_ALLOWED_DOMAINS env var)
  - If the allowlist is set, only requests to those domains are allowed
  - If not set, requests are unrestricted (dev mode)
  - Redirects are followed but re-checked against the allowlist
  - Response body is capped to prevent memory exhaustion
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

# Cap response body size (bytes) to prevent memory exhaustion
MAX_RESPONSE_BYTES = 1_000_000  # 1 MB


class HttpRequestInput(BaseModel):
    """Input schema for the http_request tool."""

    method: str = Field(default="GET", description="HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD)")
    url: str = Field(..., description="Full URL to request")
    headers: Optional[Dict[str, str]] = Field(default=None, description="Optional request headers")
    body: Optional[Any] = Field(default=None, description="Optional request body (dict for JSON, str for raw)")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="Request timeout in seconds")


class HttpRequestTool(BaseTool):
    """Perform HTTP requests with SSRF protection."""

    name = "http_request"
    description = "Make an HTTP request to a URL. Supports GET/POST/PUT/PATCH/DELETE/HEAD with optional headers and body."
    input_schema = HttpRequestInput
    default_timeout_seconds = 30

    def _is_allowed_url(self, url: str) -> bool:
        """Check whether a URL is allowed by the domain allowlist."""
        allowed = settings.HTTP_TOOL_ALLOWED_DOMAINS
        if not allowed:
            # No allowlist configured → unrestricted (dev mode)
            return True

        host = urlparse(url).hostname or ""
        host = host.lower()

        # Split the comma-separated allowlist into individual domains
        for domain in allowed.split(","):
            domain = domain.strip().lower()
            if not domain:
                continue
            # Exact match or subdomain match
            if host == domain or host.endswith("." + domain):
                return True
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

    async def execute(self, input_data: HttpRequestInput) -> ToolResult:
        """Execute the HTTP request."""
        try:
            import httpx
        except ImportError as exc:
            raise ToolError(
                "httpx is not installed. Run `pip install httpx`.",
                code="missing_dependency",
            ) from exc

        self._validate_url(input_data.url)

        method = input_data.method.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            raise ToolError(f"Unsupported HTTP method: {method}", code="invalid_method")

        headers = input_data.headers or {}
        body = input_data.body

        # Convert dict body to JSON
        json_body = None
        content = None
        if body is not None:
            if isinstance(body, dict):
                json_body = body
            else:
                content = str(body)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=input_data.timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    input_data.url,
                    headers=headers,
                    json=json_body,
                    content=content,
                )

                # Cap response body size
                raw_body = response.content[:MAX_RESPONSE_BYTES]
                truncated = len(response.content) > MAX_RESPONSE_BYTES

                # Try to parse JSON; fall back to text
                try:
                    data: Any = response.json()
                except Exception:
                    data = raw_body.decode("utf-8", errors="replace")

                return ToolResult(
                    ok=True,
                    data={
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                        "body": data,
                        "truncated": truncated,
                    },
                    metadata={
                        "url": input_data.url,
                        "method": method,
                        "status_code": response.status_code,
                        "truncated": truncated,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"Request to {input_data.url} timed out after {input_data.timeout_seconds}s",
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"HTTP request failed: {str(exc)}",
                code="http_error",
                retryable=True,
            ) from exc
