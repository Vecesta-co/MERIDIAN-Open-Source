"""
MERIDIAN Tool Service — Phase 3 Tool Sandbox.

Dispatcher facade over the ToolRegistry. Provides the runtime with a
single entry point to execute tools by name with JSON input, enforcing:
  - Per-tool timeouts
  - Maximum output size (truncate + note)
  - Standard error format
  - Prompt-injection hygiene (TOOL_RESULT wrapping for LLM context)

The real tool implementations live in app/tools/builtins/ and are
registered in the ToolRegistry. This module keeps the same
`execute_tool` signature used by the Phase 2 runtime for backward
compatibility.
"""

from typing import Any, Dict, Optional

from app.core.logging import get_logger
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry, get_registry

logger = get_logger(__name__)


def get_tool_registry() -> ToolRegistry:
    """Return the process-wide tool registry."""
    return get_registry()


def list_tools() -> list[Dict[str, Any]]:
    """List all registered tools with their schemas (for GET /tools)."""
    return get_tool_registry().list_tools()


def get_tool_info(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a single tool, or None if not registered."""
    tool = get_tool_registry().get(tool_name)
    return tool.info() if tool else None


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    timeout_seconds: Optional[int] = None,
    dry_run: bool = False,
) -> ToolResult:
    """
    Execute a tool by name with JSON input.

    Args:
        tool_name: Name of the registered tool.
        tool_input: JSON-serializable input dict validated against the
            tool's Pydantic schema.
        timeout_seconds: Optional per-call timeout override.
        dry_run: If True, simulate execution without making external
            calls (returns a canned success with a dry_run marker).

    Returns:
        ToolResult with ok=True and data on success, or ok=False with
        a standard error format on failure (unknown tool, invalid input,
        timeout, tool error).
    """
    return await get_tool_registry().execute_tool(
        tool_name,
        tool_input,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
    )


def wrap_tool_output(tool_name: str, result: ToolResult) -> str:
    """
    Wrap tool output in a labeled TOOL_RESULT block for LLM context.

    This is the prompt-injection hygiene mechanism: tool output is
    untrusted data and must be clearly delimited and labeled before
    being passed to an LLM.
    """
    return ToolRegistry.wrap_tool_output(tool_name, result)
