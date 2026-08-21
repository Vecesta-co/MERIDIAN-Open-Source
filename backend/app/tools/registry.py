"""
MERIDIAN Tool Registry — Phase 3 Tool Sandbox.

Central registry that:
  - Holds all registered tools by name
  - Validates tool input against each tool's Pydantic schema
  - Enforces per-tool timeouts (asyncio.timeout)
  - Enforces a maximum output size (truncates + notes in metadata)
  - Produces a standard error format for all failures
  - Wraps tool output in a labeled TOOL_RESULT block for LLM context
    (prompt-injection hygiene — tool output is untrusted data)
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, TypeVar

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseTool)


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    # ──────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance by its name."""
        if not tool.name:
            raise ValueError("Tool must have a name")
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool registration: %s", tool.name)
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def register_class(self, tool_cls: type[T]) -> None:
        """Instantiate and register a tool class."""
        self.register(tool_cls())

    # ──────────────────────────────────────────────
    # Lookup
    # ──────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name, or None if not registered."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check whether a tool is registered."""
        return name in self._tools

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return metadata for all registered tools (for GET /tools)."""
        return [tool.info() for tool in sorted(self._tools.values(), key=lambda t: t.name)]

    # ──────────────────────────────────────────────
    # Execution
    # ──────────────────────────────────────────────

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        timeout_seconds: Optional[int] = None,
        dry_run: bool = False,
    ) -> ToolResult:
        """
        Execute a tool by name with JSON input.

        Enforces:
          - Input validation against the tool's Pydantic schema
          - Per-tool timeout (default from tool, overridable per-call)
          - Maximum output size (truncated + noted in metadata)
          - Standard error format for all failures
          - Dry-run mode (simulate execution without external calls)

        Returns a ToolResult (never raises for tool-level failures).
        """
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(
                ok=False,
                error="unknown_tool",
                message=f"Tool '{tool_name}' is not registered",
                metadata={"tool_name": tool_name},
            )

        # Validate input against the tool's schema
        try:
            # TypeAdapter handles plain model classes and Annotated/Union
            # input schemas (e.g. the supabase_crud discriminated union).
            from pydantic import TypeAdapter

            validated = TypeAdapter(tool.input_schema).validate_python(tool_input)
        except Exception as exc:
            return ToolResult(
                ok=False,
                error="invalid_input",
                message=f"Invalid input for tool '{tool_name}': {str(exc)}",
                metadata={"tool_name": tool_name},
            )

        # Dry-run mode: simulate execution without making external calls.
        # Returns a canned success with a dry_run marker so callers can
        # test tool wiring without side effects or network/DB access.
        if dry_run:
            logger.info("Tool '%s' dry-run (no external call)", tool_name)
            return ToolResult(
                ok=True,
                data={
                    "dry_run": True,
                    "tool_name": tool_name,
                    "message": f"Dry-run simulation of '{tool_name}' — no external call made",
                },
                metadata={"tool_name": tool_name, "dry_run": True},
            )

        # Enforce timeout (asyncio.timeout is the modern API — works in
        # Python 3.11+ and avoids the deprecated coroutine-passing form
        # of asyncio.wait_for which is removed in Python 3.14).
        timeout = timeout_seconds or tool.default_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                result = await tool.execute(validated)
        except TimeoutError:
            logger.warning("Tool '%s' timed out after %ss", tool_name, timeout)
            return ToolResult(
                ok=False,
                error="timeout",
                message=f"Tool '{tool_name}' timed out after {timeout}s",
                metadata={"tool_name": tool_name, "timeout_seconds": timeout},
            )
        except ToolError as exc:
            logger.warning("Tool '%s' failed: %s (%s)", tool_name, exc.message, exc.code)
            return ToolResult(
                ok=False,
                error=exc.code,
                message=exc.message,
                metadata={"tool_name": tool_name, "retryable": exc.retryable},
            )
        except Exception as exc:
            logger.exception("Tool '%s' raised unexpected error: %s", tool_name, exc)
            return ToolResult(
                ok=False,
                error="tool_error",
                message=f"Tool '{tool_name}' failed: {str(exc)}",
                metadata={"tool_name": tool_name},
            )

        # Mark empty results explicitly (ok=True but data is None)
        if result.ok and result.data is None:
            metadata = dict(result.metadata or {})
            metadata["empty_result"] = True
            result = ToolResult(
                ok=result.ok,
                data=result.data,
                error=result.error,
                message=result.message or "Tool returned an empty result",
                metadata=metadata,
            )

        # Enforce maximum output size (truncate + note)
        result = self._truncate_result(result, tool_name)
        return result

    # ──────────────────────────────────────────────
    # Output handling
    # ──────────────────────────────────────────────

    def _truncate_result(self, result: ToolResult, tool_name: str) -> ToolResult:
        """
        Truncate tool output data to TOOL_MAX_OUTPUT_CHARS.

        Adds a 'truncated' flag to metadata so callers know the output
        was cut. Truncation happens on the JSON-serialized form so the
        LLM never receives an oversized payload.
        """
        max_chars = settings.TOOL_MAX_OUTPUT_CHARS
        if result.data is None:
            return result

        try:
            serialized = json.dumps(result.data, default=str)
        except (TypeError, ValueError):
            serialized = str(result.data)

        if len(serialized) <= max_chars:
            return result

        truncated = serialized[:max_chars]
        # Try to keep valid JSON; if not, wrap as a dict so ToolResult.data
        # remains a valid dict (Pydantic requires data to be a dict).
        try:
            data: Any = json.loads(truncated)
        except json.JSONDecodeError:
            data = {"truncated_output": truncated + "…[TRUNCATED]"}

        metadata = dict(result.metadata or {})
        metadata["truncated"] = True
        metadata["original_size_chars"] = len(serialized)
        metadata["truncated_size_chars"] = len(truncated)

        logger.warning(
            "Tool '%s' output truncated: %d -> %d chars",
            tool_name,
            len(serialized),
            len(truncated),
        )
        return ToolResult(
            ok=result.ok,
            data=data,
            error=result.error,
            message=result.message,
            metadata=metadata,
        )

    # ──────────────────────────────────────────────
    # Prompt-injection hygiene
    # ──────────────────────────────────────────────

    @staticmethod
    def wrap_tool_output(tool_name: str, result: ToolResult) -> str:
        """
        Wrap tool output in a labeled TOOL_RESULT block for LLM context.

        This is the core prompt-injection hygiene mechanism. Tool output
        is untrusted data — it may contain instructions, fake system
        prompts, or prompt-injection payloads. By wrapping it in a
        clearly delimited, labeled block, we:
          1. Make it obvious to the LLM that the content is tool data,
             not instructions.
          2. JSON-encode the payload to strip control characters and
             prevent raw instruction text from being interpreted.
          3. Provide a clear boundary (END_TOOL_RESULT) so injected
             content cannot easily escape the block.

        Format:
            TOOL_RESULT(tool="<name>", ok=<bool>, truncated=<bool>)
            <json-encoded data or error message>
            END_TOOL_RESULT
        """
        truncated = bool((result.metadata or {}).get("truncated"))
        if result.ok:
            payload = json.dumps(result.data, default=str)
        else:
            payload = json.dumps(
                {"error": result.error, "message": result.message},
                default=str,
            )

        header = f'TOOL_RESULT(tool="{tool_name}", ok={str(result.ok).lower()}, truncated={str(truncated).lower()})'
        return f"{header}\n{payload}\nEND_TOOL_RESULT"


# ──────────────────────────────────────────────
# Singleton registry
# ──────────────────────────────────────────────

_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Return the process-wide tool registry (lazily initialised)."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        # Import built-ins here to avoid circular imports at module load.
        from app.tools.builtins import register_builtin_tools

        register_builtin_tools(_registry)
    return _registry
