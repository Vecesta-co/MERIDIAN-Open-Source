# MERIDIAN Tools Contract — Phase 3 Tool Sandbox

## Overview

The Tool Sandbox provides a secure, extensible way for agent missions to interact with external systems. Every tool follows a strict contract so the runtime can dispatch, validate, time, truncate, and trace tool calls uniformly.

## Tool Interface

Every tool is a Python class that subclasses `BaseTool` (defined in `backend/app/tools/base.py`):

```python
class BaseTool(ABC):
    name: str                    # Unique tool identifier (e.g. "http_request")
    description: str             # Human-readable summary (shown in GET /tools)
    input_schema: type[BaseModel]  # Pydantic model validating tool input
    default_timeout_seconds: int   # Default execution timeout (overridable per-call)
    requires_api_key: bool         # Whether the tool needs an external API key
    api_key_env_var: Optional[str] # Env var holding the required API key

    async def execute(self, input_data: BaseModel) -> ToolResult:
        ...
```

### ToolResult

Every tool returns a `ToolResult` (Pydantic model):

```json
{
  "ok": true,
  "data": { "...": "tool output payload" },
  "error": null,
  "message": null,
  "metadata": { "duration_ms": 42 }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | `true` if the tool executed successfully |
| `data` | dict \| null | The tool's output payload (JSON-serializable) |
| `error` | str \| null | Machine-readable error code (e.g. `timeout`, `http_error`) |
| `message` | str \| null | Human-readable error/status message |
| `metadata` | dict \| null | Extra info (duration_ms, truncated, tool_name, etc.) |

### ToolError

Tools raise `ToolError` for expected failures:

```python
class ToolError(Exception):
    def __init__(self, message: str, code: str = "tool_error", retryable: bool = False):
        ...
```

The dispatcher converts `ToolError` into a standard `ToolResult(ok=False, error=code, message=message)`.

## Tool Registry

The `ToolRegistry` (in `backend/app/tools/registry.py`) is the central dispatcher:

- **Register**: `registry.register(tool)` or `registry.register_class(ToolClass)`
- **Lookup**: `registry.get(name)`, `registry.has(name)`
- **List**: `registry.list_tools()` → metadata for all tools (for GET /tools)
- **Execute**: `registry.execute_tool(name, input, timeout_seconds=None)`

### Execution Pipeline

1. **Lookup** — unknown tool → `ToolResult(ok=False, error="unknown_tool")`
2. **Validate** — input validated against the tool's Pydantic schema → `error="invalid_input"` on failure
3. **Timeout** — `asyncio.wait_for` enforces the per-tool timeout → `error="timeout"`
4. **Execute** — the tool's `execute()` runs; `ToolError` → standard error format; unexpected exceptions → `error="tool_error"`
5. **Truncate** — output data is JSON-serialized and capped at `TOOL_MAX_OUTPUT_CHARS` (default 20,000). If truncated, `metadata.truncated=true` with original/truncated sizes.

## Built-in Tools

| Tool | Name | Requires API Key | Description |
|------|------|-----------------|-------------|
| HTTP Request | `http_request` | No | Make HTTP requests with SSRF protection (domain allowlist) |
| Firecrawl Scrape | `firecrawl_scrape` | Yes (`FIRECRAWL_API_KEY`) | Scrape web pages via the Firecrawl API |
| Supabase Query | `supabase_query` | No | Safe SELECT queries (allowlisted tables or named queries) |
| RAG Query | `rag_query` | No | pgvector similarity search with embedding generation |

### http_request

**Input schema:**
```json
{
  "method": "GET",
  "url": "https://example.com/api",
  "headers": {"Authorization": "Bearer ..."},
  "body": {"key": "value"},
  "timeout_seconds": 30
}
```

**SSRF protection:** If `HTTP_TOOL_ALLOWED_DOMAINS` is set (comma-separated), only requests to those domains (exact or subdomain match) are allowed. If empty, requests are unrestricted (dev mode). Redirects are followed but re-checked against the allowlist.

**Output:**
```json
{
  "status_code": 200,
  "headers": {"content-type": "application/json"},
  "body": {"hello": "world"},
  "truncated": false
}
```

### firecrawl_scrape

**Input schema:**
```json
{
  "url": "https://example.com",
  "mode": "markdown",
  "only_main_content": true,
  "timeout_seconds": 60
}
```

**Requires:** `FIRECRAWL_API_KEY` environment variable.

**Output:**
```json
{
  "url": "https://example.com",
  "mode": "markdown",
  "content": "# Page content...",
  "metadata": {"title": "Example"}
}
```

### supabase_query

**Input schema:**
```json
{
  "query_name": "recent_users",
  "table": "users",
  "columns": ["id", "name"],
  "where": {"status": "active"},
  "order_by": "created_at",
  "limit": 50,
  "timeout_seconds": 30
}
```

**Two safe modes:**
1. **Named queries** — execute a predefined SQL query from `SUPABASE_QUERY_NAMED_QUERIES` (JSON map of name → SQL). Configured by the platform operator.
2. **Table SELECT** — SELECT columns FROM an allowlisted table (from `SUPABASE_QUERY_ALLOWED_TABLES`) with optional WHERE/ORDER BY/LIMIT. Column names are validated (alphanumeric + underscore only) to prevent SQL injection.

**Arbitrary SQL is rejected** unless explicitly configured as a named query by the platform operator.

**Output:**
```json
{
  "rows": [{"id": 1, "name": "Alice"}],
  "row_count": 1,
  "sql": "SELECT id, name FROM users WHERE status = ? LIMIT 50"
}
```

### rag_query

**Input schema:**
```json
{
  "collection": "docs",
  "query_text": "What is MERIDIAN?",
  "top_k": 5,
  "timeout_seconds": 30
}
```

**Requires:** `SUPABASE_DATABASE_URL` (Postgres with pgvector extension).

**Collection allowlist:** `RAG_COLLECTIONS` (comma-separated) restricts which collections can be queried.

**Output:**
```json
{
  "collection": "docs",
  "query_text": "What is MERIDIAN?",
  "results": [
    {"id": 1, "content": "...", "metadata": {}, "similarity": 0.92}
  ],
  "result_count": 1
}
```

## TOOL_RESULT Wrapping (Prompt-Injection Hygiene)

Tool output is **untrusted data** — it may contain prompt-injection payloads, fake system prompts, or malicious instructions. Before tool output is passed to an LLM, the dispatcher wraps it in a labeled block:

```
TOOL_RESULT(tool="http_request", ok=true, truncated=false)
{"status_code": 200, "body": "..."}
END_TOOL_RESULT
```

This provides:
1. **Clear labeling** — the LLM knows the content is tool data, not instructions
2. **JSON encoding** — strips control characters and prevents raw instruction text from being interpreted
3. **Explicit boundary** — `END_TOOL_RESULT` prevents injected content from easily escaping the block

## API Endpoints

### GET /api/v1/tools

Lists all registered tools with their metadata and input schemas.

```json
[
  {
    "name": "http_request",
    "description": "Make an HTTP request to a URL...",
    "input_schema": {"type": "object", "properties": {...}},
    "default_timeout_seconds": 30,
    "requires_api_key": false,
    "api_key_env_var": null
  }
]
```

### POST /api/v1/tools/execute

Executes a tool by name with JSON input (admin/dev use).

**Request:**
```json
{
  "tool_name": "http_request",
  "input": {"url": "https://example.com", "method": "GET"},
  "timeout_seconds": 30
}
```

**Response:**
```json
{
  "ok": true,
  "data": {"status_code": 200, "body": "..."},
  "error": null,
  "message": null,
  "metadata": {"duration_ms": 42}
}
```

## Runtime Integration

A mission step can reference tools via `tool_refs`:

```yaml
steps:
  - key: research
    name: Research the topic
    step_type: tool
    tool_refs:
      - tool_name: http_request
        input:
          url: "https://example.com/api"
          method: GET
```

The runtime:
1. Executes all `tool_refs` in listed order
2. Renders `{{prior.<step_key>}}` / `{{input}}` placeholders in tool input strings
3. Records a `tool` span (child of the step span) for each tool call
4. Appends each result as a `TOOL_RESULT` block
5. Fails the step non-retryably if any tool fails

## Security Notes

- **SSRF prevention**: `http_request` enforces a domain allowlist (`HTTP_TOOL_ALLOWED_DOMAINS`). Only exact or subdomain matches are allowed.
- **Arbitrary SQL prevention**: `supabase_query` only allows SELECT on allowlisted tables or predefined named queries. Column names are validated to prevent injection.
- **API key handling**: `firecrawl_scrape` requires `FIRECRAWL_API_KEY`; keys are read from environment variables only, never stored or logged.
- **Output size limits**: All tool outputs are truncated to `TOOL_MAX_OUTPUT_CHARS` (default 20,000) to prevent context overflow and memory exhaustion.
- **Timeout enforcement**: Every tool call has a timeout (default 30s, overridable per-call) to prevent hangs.
