"""
MERIDIAN Phase 3 — Tool Sandbox Tests.

Tests the tool sandbox:
  - Tool registry (register, list, get)
  - Tool execution (timeout, truncation, standard error format)
  - http_request SSRF protection (domain allowlist)
  - firecrawl_scrape (mocked — no network)
  - supabase_query (SELECT-only, allowlisted tables, named queries)
  - rag_query (pgvector, collection allowlist)
  - TOOL_RESULT wrapping (prompt-injection hygiene)
  - API endpoints (GET /tools, POST /tools/execute)
  - Runtime integration (tool steps in missions)
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import BaseModel

from app.core.config import settings
from app.tools.base import BaseTool, ToolError, ToolResult
from app.tools.registry import ToolRegistry, get_registry
from app.tools.builtins import BUILTIN_TOOL_CLASSES
from app.tools.builtins.http_request import HttpRequestTool
from app.tools.builtins.firecrawl_scrape import FirecrawlScrapeTool
from app.tools.builtins.supabase_query import SupabaseQueryTool
from app.tools.builtins.rag_query import RagQueryTool
from app.services import tool_service


# ──────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────


class _ToolInput(BaseModel):
    """Minimal input schema for test tools."""

    pass


class SlowTool(BaseTool):
    """A test tool that sleeps longer than its timeout."""

    name = "slow_tool"
    description = "A tool that takes too long"
    input_schema = _ToolInput
    default_timeout_seconds = 1

    async def execute(self, input_data) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(ok=True, data={"done": True})


class BigOutputTool(BaseTool):
    """A test tool that returns a very large output."""

    name = "big_output_tool"
    description = "A tool that returns a huge payload"
    input_schema = _ToolInput
    default_timeout_seconds = 5

    async def execute(self, input_data) -> ToolResult:
        return ToolResult(ok=True, data={"payload": "x" * 100_000})


class FailingTool(BaseTool):
    """A test tool that always fails."""

    name = "failing_tool"
    description = "A tool that always errors"
    input_schema = _ToolInput
    default_timeout_seconds = 5

    async def execute(self, input_data) -> ToolResult:
        raise ToolError("Something went wrong", code="custom_error")


@pytest.fixture
def fresh_registry():
    """A fresh ToolRegistry with only the test tools registered."""
    registry = ToolRegistry()
    registry.register(SlowTool())
    registry.register(BigOutputTool())
    registry.register(FailingTool())
    return registry


# ──────────────────────────────────────────────
# 1. Tool Registry
# ──────────────────────────────────────────────


def test_registry_register_and_get():
    """Register a tool and retrieve it by name."""
    registry = ToolRegistry()
    tool = HttpRequestTool()
    registry.register(tool)

    assert registry.has("http_request")
    assert registry.get("http_request") is tool
    assert registry.get("nonexistent") is None


def test_registry_register_class():
    """Register a tool class (instantiated automatically)."""
    registry = ToolRegistry()
    registry.register_class(HttpRequestTool)

    assert registry.has("http_request")
    assert isinstance(registry.get("http_request"), HttpRequestTool)


def test_registry_list_tools_sorted():
    """list_tools returns tools sorted by name with metadata."""
    registry = ToolRegistry()
    registry.register_class(BigOutputTool)
    registry.register_class(SlowTool)
    registry.register_class(FailingTool)

    tools = registry.list_tools()
    names = [t["name"] for t in tools]
    assert names == sorted(names)
    assert all("input_schema" in t for t in tools)
    assert all("description" in t for t in tools)
    assert all("default_timeout_seconds" in t for t in tools)


def test_builtin_tools_registered():
    """All built-in tools are registered in the singleton registry."""
    registry = get_registry()
    for tool_cls in BUILTIN_TOOL_CLASSES:
        assert registry.has(tool_cls.name), f"Missing built-in tool: {tool_cls.name}"


def test_builtin_tool_metadata():
    """Built-in tools expose correct metadata."""
    registry = get_registry()

    http = registry.get("http_request")
    assert http is not None
    assert http.description
    assert http.default_timeout_seconds > 0

    firecrawl = registry.get("firecrawl_scrape")
    assert firecrawl is not None
    assert firecrawl.requires_api_key is True
    assert firecrawl.api_key_env_var == "FIRECRAWL_API_KEY"


# ──────────────────────────────────────────────
# 2. Tool Execution
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_success(fresh_registry):
    """A successful tool execution returns ok=True with data."""
    # Use a simple inline tool with a proper input schema
    from pydantic import BaseModel as PydanticBaseModel

    class EchoInput(PydanticBaseModel):
        message: str

    class EchoTool(BaseTool):
        name = "echo_tool"
        description = "Echoes input"
        input_schema = EchoInput
        default_timeout_seconds = 5

        async def execute(self, input_data) -> ToolResult:
            return ToolResult(ok=True, data={"echo": input_data.model_dump()})

    fresh_registry.register(EchoTool())
    result = await fresh_registry.execute_tool("echo_tool", {"message": "hello"})

    assert result.ok is True
    assert result.data == {"echo": {"message": "hello"}}
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_unknown_tool(fresh_registry):
    """Executing an unknown tool returns a standard error result."""
    result = await fresh_registry.execute_tool("nonexistent", {})

    assert result.ok is False
    assert result.error == "unknown_tool"
    assert "nonexistent" in result.message


@pytest.mark.asyncio
async def test_execute_invalid_input(fresh_registry):
    """Invalid input against the tool's schema returns invalid_input."""
    class StrictTool(BaseTool):
        name = "strict_tool"
        description = "Requires a number"
        default_timeout_seconds = 5

        class Input(BaseModel):
            count: int

        input_schema = Input

        async def execute(self, input_data) -> ToolResult:
            return ToolResult(ok=True, data={"count": input_data.count})

    fresh_registry.register(StrictTool())
    result = await fresh_registry.execute_tool("strict_tool", {"count": "not-a-number"})

    assert result.ok is False
    assert result.error == "invalid_input"


@pytest.mark.asyncio
async def test_execute_tool_timeout(fresh_registry):
    """A tool that exceeds its timeout returns a timeout error."""
    result = await fresh_registry.execute_tool("slow_tool", {})

    assert result.ok is False
    assert result.error == "timeout"
    assert "timed out" in result.message


@pytest.mark.asyncio
async def test_execute_tool_timeout_override(fresh_registry):
    """A per-call timeout override is respected."""
    # slow_tool sleeps 5s; override timeout to 1s → should time out
    result = await fresh_registry.execute_tool("slow_tool", {}, timeout_seconds=1)

    assert result.ok is False
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_execute_tool_error(fresh_registry):
    """A tool that raises ToolError returns the standard error format."""
    result = await fresh_registry.execute_tool("failing_tool", {})

    assert result.ok is False
    assert result.error == "custom_error"
    assert "Something went wrong" in result.message


@pytest.mark.asyncio
async def test_execute_tool_output_truncation(fresh_registry):
    """Large tool outputs are truncated with a metadata note."""
    result = await fresh_registry.execute_tool("big_output_tool", {})

    assert result.ok is True
    assert result.metadata is not None
    assert result.metadata.get("truncated") is True
    assert result.metadata.get("original_size_chars", 0) > settings.TOOL_MAX_OUTPUT_CHARS
    assert result.metadata.get("truncated_size_chars", 0) <= settings.TOOL_MAX_OUTPUT_CHARS


# ──────────────────────────────────────────────
# 3. TOOL_RESULT Wrapping (Prompt-Injection Hygiene)
# ──────────────────────────────────────────────


def test_wrap_tool_output_success():
    """Successful tool output is wrapped in a labeled TOOL_RESULT block."""
    result = ToolResult(ok=True, data={"status": "ok", "body": "hello"})
    wrapped = ToolRegistry.wrap_tool_output("http_request", result)

    assert wrapped.startswith('TOOL_RESULT(tool="http_request", ok=true, truncated=false)')
    assert '"status": "ok"' in wrapped
    assert wrapped.endswith("END_TOOL_RESULT")


def test_wrap_tool_output_error():
    """Failed tool output is wrapped with the error info."""
    result = ToolResult(ok=False, error="timeout", message="Timed out")
    wrapped = ToolRegistry.wrap_tool_output("http_request", result)

    assert wrapped.startswith('TOOL_RESULT(tool="http_request", ok=false, truncated=false)')
    assert '"error": "timeout"' in wrapped
    assert '"message": "Timed out"' in wrapped
    assert wrapped.endswith("END_TOOL_RESULT")


def test_wrap_tool_output_truncated():
    """Truncated tool output is flagged in the wrapper header."""
    result = ToolResult(
        ok=True,
        data={"payload": "x" * 100},
        metadata={"truncated": True},
    )
    wrapped = ToolRegistry.wrap_tool_output("big_output_tool", result)

    assert 'truncated=true' in wrapped


# ──────────────────────────────────────────────
# 4. http_request Tool (SSRF Protection)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_request_ssrf_allowlist():
    """http_request rejects domains not in the allowlist."""
    tool = HttpRequestTool()

    with patch.object(settings, "HTTP_TOOL_ALLOWED_DOMAINS", "example.com,api.test.com"):
        # Allowed: exact match
        assert tool._is_allowed_url("https://example.com/path") is True
        # Allowed: subdomain
        assert tool._is_allowed_url("https://sub.example.com/path") is True
        # Rejected: different domain
        assert tool._is_allowed_url("https://evil.com/path") is False
        # Rejected: lookalike domain
        assert tool._is_allowed_url("https://notexample.com/path") is False


@pytest.mark.asyncio
async def test_http_request_ssrf_validation():
    """http_request validates URL scheme and hostname."""
    tool = HttpRequestTool()

    with patch.object(settings, "HTTP_TOOL_ALLOWED_DOMAINS", "example.com"):
        # Non-http scheme rejected
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="ftp://example.com/file", method="GET")
            )
        assert exc.value.code == "invalid_url"

        # Missing hostname rejected
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="http:///path", method="GET")
            )
        assert exc.value.code == "invalid_url"

        # Disallowed domain rejected
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="https://evil.com/path", method="GET")
            )
        assert exc.value.code == "domain_not_allowed"


@pytest.mark.asyncio
async def test_http_request_success_mocked():
    """http_request makes a successful request (mocked httpx)."""
    tool = HttpRequestTool()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"hello": "world"}'
    mock_response.headers = {"content-type": "application/json"}
    mock_response.json.return_value = {"hello": "world"}

    mock_client = AsyncMock()
    mock_client.request.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute(
            tool.input_schema(url="https://example.com/api", method="GET")
        )

    assert result.ok is True
    assert result.data is not None
    assert result.data["status_code"] == 200
    assert result.data["body"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_http_request_timeout_mocked():
    """http_request handles timeouts gracefully."""
    tool = HttpRequestTool()

    mock_client = AsyncMock()
    mock_client.request.side_effect = __import__("httpx").TimeoutException("timed out")
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="https://example.com/api", method="GET")
            )

    assert exc.value.code == "timeout"
    assert exc.value.retryable is True


# ──────────────────────────────────────────────
# 5. firecrawl_scrape Tool (Mocked — No Network)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_firecrawl_requires_api_key():
    """firecrawl_scrape fails cleanly when FIRECRAWL_API_KEY is missing."""
    tool = FirecrawlScrapeTool()

    with patch.object(settings, "FIRECRAWL_API_KEY", None):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="https://example.com", mode="markdown")
            )

    assert exc.value.code == "missing_api_key"


@pytest.mark.asyncio
async def test_firecrawl_success_mocked():
    """firecrawl_scrape succeeds with a mocked API response (no network)."""
    tool = FirecrawlScrapeTool()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "markdown": "# Hello World\n\nThis is scraped content.",
            "metadata": {"title": "Example"},
        }
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch.object(settings, "FIRECRAWL_API_KEY", "test-key"), patch(
        "httpx.AsyncClient", return_value=mock_client
    ):
        result = await tool.execute(
            tool.input_schema(url="https://example.com", mode="markdown")
        )

    assert result.ok is True
    assert result.data is not None
    assert result.data["content"] == "# Hello World\n\nThis is scraped content."
    assert result.data["url"] == "https://example.com"
    assert result.data["mode"] == "markdown"


@pytest.mark.asyncio
async def test_firecrawl_api_error_mocked():
    """firecrawl_scrape returns a clean error on non-200 API response."""
    tool = FirecrawlScrapeTool()

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch.object(settings, "FIRECRAWL_API_KEY", "bad-key"), patch(
        "httpx.AsyncClient", return_value=mock_client
    ):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(url="https://example.com", mode="markdown")
            )

    assert exc.value.code == "firecrawl_error"
    assert "401" in exc.value.message


# ──────────────────────────────────────────────
# 6. supabase_query Tool (Safe SELECT)
# ──────────────────────────────────────────────


def test_supabase_query_builds_safe_select():
    """supabase_query builds a safe SELECT with allowlisted table."""
    tool = SupabaseQueryTool()

    with patch.object(settings, "SUPABASE_QUERY_ALLOWED_TABLES", "users,orders"):
        input_data = tool.input_schema(
            table="users",
            columns=["id", "name"],
            where={"status": "active"},
            order_by="created_at",
            limit=10,
        )
        sql = tool._build_safe_select(input_data)

    assert sql == "SELECT id, name FROM users WHERE status = ? ORDER BY created_at LIMIT 10"


def test_supabase_query_rejects_disallowed_table():
    """supabase_query rejects tables not in the allowlist."""
    tool = SupabaseQueryTool()

    with patch.object(settings, "SUPABASE_QUERY_ALLOWED_TABLES", "users"):
        input_data = tool.input_schema(table="secrets", columns=["*"])
        with pytest.raises(ToolError) as exc:
            tool._build_safe_select(input_data)

    assert exc.value.code == "table_not_allowed"


def test_supabase_query_rejects_invalid_columns():
    """supabase_query rejects SQL injection via column names."""
    tool = SupabaseQueryTool()

    with patch.object(settings, "SUPABASE_QUERY_ALLOWED_TABLES", "users"):
        # SQL injection attempt in column
        input_data = tool.input_schema(
            table="users",
            columns=["id; DROP TABLE users; --"],
        )
        with pytest.raises(ToolError) as exc:
            tool._build_safe_select(input_data)
        assert exc.value.code == "invalid_input"

        # SQL injection attempt in where column
        input_data = tool.input_schema(
            table="users",
            columns=["id"],
            where={"id; DROP TABLE users; --": 1},
        )
        with pytest.raises(ToolError) as exc:
            tool._build_safe_select(input_data)
        assert exc.value.code == "invalid_input"


def test_supabase_query_named_queries():
    """supabase_query resolves named queries from config."""
    tool = SupabaseQueryTool()

    named = json.dumps({"recent_users": "SELECT id, name FROM users ORDER BY created_at DESC LIMIT 5"})
    with patch.object(settings, "SUPABASE_QUERY_NAMED_QUERIES", named):
        queries = tool._get_named_queries()
        assert "recent_users" in queries
        assert "SELECT" in queries["recent_users"]


@pytest.mark.asyncio
async def test_supabase_query_missing_config():
    """supabase_query fails cleanly when SUPABASE_DATABASE_URL is missing."""
    tool = SupabaseQueryTool()

    with patch.object(settings, "SUPABASE_DATABASE_URL", None), patch.object(
        settings, "SUPABASE_QUERY_ALLOWED_TABLES", "users"
    ):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(table="users", columns=["*"])
            )

    assert exc.value.code == "missing_config"


@pytest.mark.asyncio
async def test_supabase_query_sqlite_success():
    """supabase_query executes against a SQLite database (dev mode)."""
    tool = SupabaseQueryTool()

    # Create a temp SQLite DB with a users table
    import sqlite3
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, status TEXT)")
        conn.execute("INSERT INTO users (name, status) VALUES ('Alice', 'active')")
        conn.execute("INSERT INTO users (name, status) VALUES ('Bob', 'inactive')")
        conn.commit()
        conn.close()

        with patch.object(settings, "SUPABASE_DATABASE_URL", f"sqlite:///{path}"), patch.object(
            settings, "SUPABASE_QUERY_ALLOWED_TABLES", "users"
        ):
            result = await tool.execute(
                tool.input_schema(
                    table="users",
                    columns=["id", "name"],
                    where={"status": "active"},
                )
            )

        assert result.ok is True
        assert result.data is not None
        assert result.data["row_count"] == 1
        assert result.data["rows"][0]["name"] == "Alice"
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 7. rag_query Tool
# ──────────────────────────────────────────────


def test_rag_query_collection_allowlist():
    """rag_query validates collection against the allowlist."""
    tool = RagQueryTool()

    with patch.object(settings, "RAG_COLLECTIONS", "docs,articles"):
        allowed = tool._get_allowed_collections()
        assert "docs" in allowed
        assert "articles" in allowed


@pytest.mark.asyncio
async def test_rag_query_rejects_disallowed_collection():
    """rag_query rejects collections not in the allowlist."""
    tool = RagQueryTool()

    with patch.object(settings, "RAG_COLLECTIONS", "docs"), patch.object(
        settings, "SUPABASE_DATABASE_URL", "postgresql://user:pass@localhost:5432/db"
    ):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(collection="secrets", query_text="test", top_k=5)
            )

    assert exc.value.code == "collection_not_allowed"


@pytest.mark.asyncio
async def test_rag_query_missing_config():
    """rag_query fails cleanly when SUPABASE_DATABASE_URL is missing."""
    tool = RagQueryTool()

    with patch.object(settings, "SUPABASE_DATABASE_URL", None):
        with pytest.raises(ToolError) as exc:
            await tool.execute(
                tool.input_schema(collection="docs", query_text="test", top_k=5)
            )

    assert exc.value.code == "missing_config"


# ──────────────────────────────────────────────
# 8. Tool Service (Dispatcher Facade)
# ──────────────────────────────────────────────


def test_tool_service_list_tools():
    """tool_service.list_tools returns all registered tools."""
    tools = tool_service.list_tools()
    names = [t["name"] for t in tools]

    assert "http_request" in names
    assert "firecrawl_scrape" in names
    assert "supabase_query" in names
    assert "rag_query" in names


def test_tool_service_get_tool_info():
    """tool_service.get_tool_info returns metadata for a tool."""
    info = tool_service.get_tool_info("http_request")
    assert info is not None
    assert info["name"] == "http_request"
    assert "input_schema" in info

    assert tool_service.get_tool_info("nonexistent") is None


@pytest.mark.asyncio
async def test_tool_service_execute_unknown():
    """tool_service.execute_tool returns a standard error for unknown tools."""
    result = await tool_service.execute_tool("nonexistent", {})
    assert result.ok is False
    assert result.error == "unknown_tool"


# ──────────────────────────────────────────────
# 9. API Endpoints
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_tools_endpoint(async_client: AsyncClient):
    """GET /tools lists all registered tools with schemas."""
    response = await async_client.get("/api/v1/tools")
    assert response.status_code == 200

    tools = response.json()
    assert isinstance(tools, list)
    assert len(tools) >= 4  # at least the 4 built-in tools

    names = [t["name"] for t in tools]
    assert "http_request" in names
    assert "firecrawl_scrape" in names
    assert "supabase_query" in names
    assert "rag_query" in names

    # Each tool has the required metadata
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert "default_timeout_seconds" in tool
        assert "requires_api_key" in tool


@pytest.mark.asyncio
async def test_post_tools_execute_unknown_tool(async_client: AsyncClient):
    """POST /tools/execute returns a standard error for unknown tools."""
    response = await async_client.post(
        "/api/v1/tools/execute",
        json={"tool_name": "nonexistent", "input": {}},
    )
    assert response.status_code == 200  # ToolResult is returned, not HTTP error
    data = response.json()
    assert data["ok"] is False
    assert data["error"] == "unknown_tool"


@pytest.mark.asyncio
async def test_post_tools_execute_invalid_input(async_client: AsyncClient):
    """POST /tools/execute validates input against the tool schema."""
    response = await async_client.post(
        "/api/v1/tools/execute",
        json={"tool_name": "http_request", "input": {"url": 123}},  # url must be str
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"] == "invalid_input"


@pytest.mark.asyncio
async def test_post_tools_execute_http_request_ssrf(async_client: AsyncClient):
    """POST /tools/execute enforces SSRF protection for http_request."""
    with patch.object(settings, "HTTP_TOOL_ALLOWED_DOMAINS", "example.com"):
        response = await async_client.post(
            "/api/v1/tools/execute",
            json={
                "tool_name": "http_request",
                "input": {"url": "https://evil.com/path", "method": "GET"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "domain_not_allowed"


@pytest.mark.asyncio
async def test_post_tools_execute_firecrawl_missing_key(async_client: AsyncClient):
    """POST /tools/execute returns missing_api_key for firecrawl without key."""
    with patch.object(settings, "FIRECRAWL_API_KEY", None):
        response = await async_client.post(
            "/api/v1/tools/execute",
            json={
                "tool_name": "firecrawl_scrape",
                "input": {"url": "https://example.com", "mode": "markdown"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert data["error"] == "missing_api_key"


@pytest.mark.asyncio
async def test_post_tools_execute_success_mocked(async_client: AsyncClient):
    """POST /tools/execute returns tool data on success (mocked)."""
    mock_result = ToolResult(
        ok=True,
        data={"status_code": 200, "body": "Mocked response"},
        metadata={"duration_ms": 5},
    )

    with patch(
        "app.api.v1.tools.tool_service.execute_tool",
        AsyncMock(return_value=mock_result),
    ):
        response = await async_client.post(
            "/api/v1/tools/execute",
            json={
                "tool_name": "http_request",
                "input": {"url": "https://example.com", "method": "GET"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["data"]["status_code"] == 200
    assert data["data"]["body"] == "Mocked response"


# ──────────────────────────────────────────────
# 10. Runtime Integration (Tool Steps in Missions)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_with_tool_step_success(async_client: AsyncClient):
    """A mission with a tool step executes the tool and completes."""
    from app.services.run_service import execute_run
    from tests.conftest import TestSessionFactory

    # Create a mission with an LLM step + tool step
    payload = {
        "name": "Tool Mission",
        "goal": "Test tool integration",
        "steps": [
            {
                "key": "llm_step",
                "name": "LLM Step",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Do something",
                "order_index": 0,
            },
            {
                "key": "tool_step",
                "name": "Tool Step",
                "step_type": "tool",
                "tool_refs": [{"tool_name": "http_request"}],
                "order_index": 1,
            },
        ],
    }
    response = await async_client.post("/api/v1/missions", json=payload)
    assert response.status_code == 201
    mission_id = response.json()["id"]

    publish_resp = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert publish_resp.status_code == 200

    run_resp = await async_client.post(
        "/api/v1/runs", json={"mission_id": mission_id, "input_context": {}}
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    # Mock LLM and tool execution
    mock_llm = AsyncMock(
        return_value={
            "text": "LLM output",
            "model": "gpt-4o-mini",
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
            "finish_reason": "stop",
        }
    )
    mock_tool_result = ToolResult(
        ok=True,
        data={"status_code": 200, "body": "Tool output"},
        metadata={"duration_ms": 10},
    )

    with patch("app.services.run_service.llm_service.call_llm", mock_llm), patch(
        "app.services.run_service.tool_service.execute_tool",
        AsyncMock(return_value=mock_tool_result),
    ):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run_id))

    assert executed.status == "completed"

    # Verify the run detail shows the tool step completed with TOOL_RESULT
    detail_resp = await async_client.get(f"/api/v1/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    assert len(detail["run_steps"]) == 2
    tool_rs = detail["run_steps"][1]
    assert tool_rs["status"] == "completed"
    assert "TOOL_RESULT" in str(tool_rs["output_json"])

    # Verify tool span was recorded
    tool_spans = [s for s in detail["spans"] if s["kind"] == "tool"]
    assert len(tool_spans) == 1
    assert tool_spans[0]["name"] == "tool:http_request"
    assert tool_spans[0]["status"] == "ok"
    assert tool_spans[0]["meta_json"]["tool_name"] == "http_request"


@pytest.mark.asyncio
async def test_run_with_tool_step_failure(async_client: AsyncClient):
    """A mission with a failing tool step fails the run."""
    from app.services.run_service import execute_run
    from tests.conftest import TestSessionFactory

    payload = {
        "name": "Tool Failure Mission",
        "goal": "Test tool failure",
        "steps": [
            {
                "key": "tool_step",
                "name": "Tool Step",
                "step_type": "tool",
                "tool_refs": [{"tool_name": "http_request"}],
                "order_index": 0,
            },
        ],
    }
    response = await async_client.post("/api/v1/missions", json=payload)
    assert response.status_code == 201
    mission_id = response.json()["id"]

    publish_resp = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert publish_resp.status_code == 200

    run_resp = await async_client.post(
        "/api/v1/runs", json={"mission_id": mission_id, "input_context": {}}
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    # Mock tool failure
    mock_tool_result = ToolResult(
        ok=False,
        error="http_error",
        message="Connection refused",
    )

    with patch(
        "app.services.run_service.tool_service.execute_tool",
        AsyncMock(return_value=mock_tool_result),
    ):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run_id))

    assert executed.status == "failed"
    summary = executed.error_summary or ""
    assert "http_error" in summary or "Connection refused" in summary

    # Verify the tool span was recorded as error
    detail_resp = await async_client.get(f"/api/v1/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    tool_spans = [s for s in detail["spans"] if s["kind"] == "tool"]
    assert len(tool_spans) == 1
    assert tool_spans[0]["status"] == "error"
    assert tool_spans[0]["error_json"]["error"] == "http_error"


@pytest.mark.asyncio
async def test_run_with_multiple_tool_refs(async_client: AsyncClient):
    """A tool step with multiple tool_refs executes all in order."""
    from app.services.run_service import execute_run
    from tests.conftest import TestSessionFactory

    payload = {
        "name": "Multi Tool Mission",
        "goal": "Test multiple tools",
        "steps": [
            {
                "key": "tool_step",
                "name": "Tool Step",
                "step_type": "tool",
                "tool_refs": [
                    {"tool_name": "http_request", "input": {"url": "https://a.com"}},
                    {"tool_name": "http_request", "input": {"url": "https://b.com"}},
                ],
                "order_index": 0,
            },
        ],
    }
    response = await async_client.post("/api/v1/missions", json=payload)
    assert response.status_code == 201
    mission_id = response.json()["id"]

    publish_resp = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert publish_resp.status_code == 200

    run_resp = await async_client.post(
        "/api/v1/runs", json={"mission_id": mission_id, "input_context": {}}
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    # Track tool calls
    tool_calls = []

    async def fake_execute_tool(tool_name, tool_input, timeout_seconds=None):
        tool_calls.append((tool_name, tool_input))
        return ToolResult(
            ok=True,
            data={"url": tool_input.get("url"), "status_code": 200},
            metadata={"duration_ms": 5},
        )

    with patch(
        "app.services.run_service.tool_service.execute_tool",
        fake_execute_tool,
    ):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run_id))

    assert executed.status == "completed"
    assert len(tool_calls) == 2
    assert tool_calls[0][1]["url"] == "https://a.com"
    assert tool_calls[1][1]["url"] == "https://b.com"

    # Verify both tool spans were recorded
    detail_resp = await async_client.get(f"/api/v1/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    tool_spans = [s for s in detail["spans"] if s["kind"] == "tool"]
    assert len(tool_spans) == 2
    assert all(s["status"] == "ok" for s in tool_spans)
