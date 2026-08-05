"""
MERIDIAN Tool Service — Phase 2 Agent Runtime (STUB).

IMPORTANT: The Tool Sandbox is NOT implemented in Phase 2.
Per the hard scope limits, this module only provides a stub that
returns a structured "tool not implemented" error.

The real tool dispatch (web search, RAG, browser, API calls) will be
implemented in Phase 3 (Tool Sandbox).
"""

from typing import Any, Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """
    Stub tool executor.

    Phase 2: Always returns a structured error indicating the tool
    sandbox is not yet implemented. Phase 3 will replace this with a
    real dispatch that runs tools in an isolated subprocess/container.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Input parameters for the tool.
        timeout_seconds: Max execution time.

    Returns:
        Dict with a structured error:
            {
                "ok": False,
                "error": "tool_not_implemented",
                "message": "Tool sandbox is not implemented in Phase 2",
                "tool_name": tool_name,
            }
    """
    logger.warning(
        "Tool execution attempted but Tool Sandbox is not implemented: tool=%s",
        tool_name,
    )
    return {
        "ok": False,
        "error": "tool_not_implemented",
        "message": f"Tool '{tool_name}' is not implemented in Phase 2 (Tool Sandbox arrives in Phase 3)",
        "tool_name": tool_name,
    }
