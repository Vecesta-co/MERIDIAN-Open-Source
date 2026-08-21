"""
MERIDIAN Built-in Tool: firecrawl_scrape — Phase 3 Tool Sandbox.

Scrapes a URL using the Firecrawl API. Requires FIRECRAWL_API_KEY.

The Firecrawl API is called over HTTPS. The URL to scrape is passed
as a parameter to Firecrawl's service — we do NOT fetch the target
URL directly, so SSRF risk is delegated to Firecrawl's own safeguards.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"


class FirecrawlScrapeInput(BaseModel):
    """Input schema for the firecrawl_scrape tool."""

    url: str = Field(..., description="URL to scrape")
    mode: str = Field(
        default="markdown",
        description="Output mode: markdown, html, rawHtml, links, screenshot",
    )
    only_main_content: bool = Field(
        default=True,
        description="Only return the main content of the page",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=300, description="Scrape timeout in seconds")


class FirecrawlScrapeTool(BaseTool):
    """Scrape a URL using the Firecrawl API."""

    name = "firecrawl_scrape"
    description = "Scrape a web page using Firecrawl. Returns page content in markdown/html/links format."
    input_schema = FirecrawlScrapeInput
    default_timeout_seconds = 60
    requires_api_key = True
    api_key_env_var = "FIRECRAWL_API_KEY"

    async def execute(self, input_data: FirecrawlScrapeInput) -> ToolResult:
        """Execute the Firecrawl scrape."""
        api_key = settings.FIRECRAWL_API_KEY
        if not api_key:
            raise ToolError(
                "FIRECRAWL_API_KEY is not configured. Set it in the environment to use this tool.",
                code="missing_api_key",
            )

        try:
            import httpx
        except ImportError as exc:
            raise ToolError(
                "httpx is not installed. Run `pip install httpx`.",
                code="missing_dependency",
            ) from exc

        payload: Dict[str, Any] = {
            "url": input_data.url,
            "formats": [input_data.mode],
            "onlyMainContent": input_data.only_main_content,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=input_data.timeout_seconds) as client:
                response = await client.post(
                    FIRECRAWL_API_URL,
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise ToolError(
                f"Firecrawl request timed out after {input_data.timeout_seconds}s",
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"Firecrawl request failed: {str(exc)}",
                code="http_error",
                retryable=True,
            ) from exc

        if response.status_code != 200:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after else 30
                raise ToolError(
                    f"Firecrawl rate limited. Retry after {wait_seconds}s",
                    code="rate_limited",
                    retryable=True,
                )
            raise ToolError(
                f"Firecrawl API returned status {response.status_code}: {response.text[:500]}",
                code="firecrawl_error",
            )

        try:
            result = response.json()
        except Exception as exc:
            raise ToolError(
                f"Firecrawl returned invalid JSON: {str(exc)}",
                code="invalid_response",
            ) from exc

        # Extract the requested format from the response
        data = result.get("data", {})
        content = data.get(input_data.mode) or data.get("content") or data.get("markdown")

        return ToolResult(
            ok=True,
            data={
                "url": input_data.url,
                "mode": input_data.mode,
                "content": content,
                "metadata": data.get("metadata", {}),
            },
            metadata={
                "url": input_data.url,
                "mode": input_data.mode,
                "status_code": response.status_code,
            },
        )
