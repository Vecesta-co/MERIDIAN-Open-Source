"""
MERIDIAN Built-in Tools — Phase 3 Tool Sandbox.

Registers the MVP tool set:
  - http_request: HTTP requests with SSRF protection
  - firecrawl_scrape: web scraping via Firecrawl API
  - supabase_query: safe SELECT queries (allowlisted tables / named queries)
  - rag_query: pgvector similarity search
"""

from typing import TYPE_CHECKING, Any, List

from app.tools.builtins.firecrawl_scrape import FirecrawlScrapeTool
from app.tools.builtins.http_request import HttpRequestTool
from app.tools.builtins.browseuse_action import BrowseuseActionTool
from app.tools.builtins.rag_query import RagQueryTool
from app.tools.builtins.supabase_crud import SupabaseCrudTool
from app.tools.builtins.supabase_query import SupabaseQueryTool

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

#: All built-in tool classes (easily add new tools by appending here).
#: Typed as Any �?" these are concrete classes registered by name; the registry
#: validates input against each tool's schema at execution time.
BUILTIN_TOOL_CLASSES: List[Any] = [
    HttpRequestTool,
    FirecrawlScrapeTool,
    BrowseuseActionTool,
    SupabaseCrudTool,
    SupabaseQueryTool,
    RagQueryTool,
]


def register_builtin_tools(registry: "ToolRegistry") -> None:
    """Register all built-in tools into the given registry."""
    for tool_cls in BUILTIN_TOOL_CLASSES:
        registry.register_class(tool_cls)


__all__ = [
    "BUILTIN_TOOL_CLASSES",
    "register_builtin_tools",
    "HttpRequestTool",
    "FirecrawlScrapeTool",
    "SupabaseQueryTool",
    "RagQueryTool",
]
