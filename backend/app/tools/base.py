"""
MERIDIAN Tool Interface — Phase 3 Tool Sandbox.

Defines the base contract every tool must implement:
  - name: unique tool identifier
  - description: human-readable summary
  - input_schema: Pydantic model validating tool input
  - execute(): returns a structured ToolResult

All tools return a standardized JSON structure:
    {
        "ok": bool,
        "data": {...} | None,
        "error": str | None,
        "metadata": {...} | None
    }

Prompt-injection hygiene: tool outputs are ALWAYS treated as untrusted
data. The dispatcher wraps tool results in a labeled TOOL_RESULT block
before they are passed to any LLM (see registry.execute_tool).
"""

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ToolResult(BaseModel):
    """
    Standard structured result returned by every tool.

    Fields:
        ok: True if the tool executed successfully.
        data: The tool's output payload (JSON-serializable).
        error: Machine-readable error code (e.g. "timeout", "http_error").
        message: Human-readable error/status message.
        metadata: Extra info (duration_ms, truncated, tool_name, etc.).
    """

    model_config = ConfigDict(extra="allow")

    ok: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the standard JSON-serializable dict."""
        return self.model_dump(exclude_none=True)


class ToolError(Exception):
    """
    Raised by a tool when execution fails.

    Carries an error code so the dispatcher can produce a standard
    error format. Non-retryable by default (the dispatcher decides
    whether a tool error is retryable based on the error code).
    """

    def __init__(self, message: str, code: str = "tool_error", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class BaseTool(ABC):
    """
    Abstract base class for all MERIDIAN tools.

    Subclasses must define:
        name: str
        description: str
        input_schema: Pydantic model class (not instance)
        async execute(input_data) -> ToolResult

    Tools are added as plain Python modules under app/tools/builtins/
    and registered in the ToolRegistry. No plugin SDK is required —
    just subclass BaseTool and register it.
    """

    #: Unique tool name (e.g. "http_request")
    name: str = ""

    #: Human-readable description shown in GET /tools
    description: str = ""

    #: Pydantic model class validating the tool's input
    input_schema: type[BaseModel] = BaseModel

    #: Default timeout in seconds (overridable per-call)
    default_timeout_seconds: int = 30

    #: Whether this tool requires an API key / external service
    requires_api_key: bool = False

    #: Name of the env var holding the required API key (if any)
    api_key_env_var: Optional[str] = None

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"Tool {self.__class__.__name__} must define a 'name'")

    @abstractmethod
    async def execute(self, input_data: Any) -> ToolResult:
        """
        Execute the tool with validated input.

        Args:
            input_data: An instance of self.input_schema.

        Returns:
            ToolResult with ok=True and data on success, or ok=False
            with error/message on failure.
        """
        raise NotImplementedError

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def input_hash(self, input_data: BaseModel) -> str:
        """Compute a stable sha256 hash of the tool input (for tracing)."""
        raw = json.dumps(
            input_data.model_dump(mode="json"),
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def schema_dict(self) -> Dict[str, Any]:
        """Return the JSON-schema representation of the input schema."""
        # TypeAdapter handles both plain Pydantic model classes and
        # Annotated/Union input schemas (e.g. discriminated unions).
        from pydantic import TypeAdapter

        return TypeAdapter(self.input_schema).json_schema()

    def info(self) -> Dict[str, Any]:
        """Return tool metadata for GET /tools."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema_dict(),
            "default_timeout_seconds": self.default_timeout_seconds,
            "requires_api_key": self.requires_api_key,
            "api_key_env_var": self.api_key_env_var,
        }
