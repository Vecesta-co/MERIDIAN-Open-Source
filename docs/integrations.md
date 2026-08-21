# MERIDIAN Phase 8 — Integration Bus

## Overview

The Integration Bus (Phase 8) provides authenticated webhook triggers, safe tool execution with sandbox mitigations, and integration health monitoring. Each integration is implemented as a **Tool** with a documented I/O schema, and all tools return standardized [`ToolResult`](#toolresult-structure) objects.

## Core Concepts

### ToolResult Structure

All tools return a `ToolResult` with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `ok` | `bool` | Whether the operation succeeded |
| `data` | `Any` | Successful output data (rows, counts, etc.) |
| `error` | `str` | Error code if `ok is False` (e.g., `table_not_allowed`, `invalid_input`) |
| `message` | `str` | Human-readable description |
| `metadata` | `Dict` | Additional info (operation type, table, original/truncated sizes, etc.) |

### Abuse Vector Mitigations

All tools enforce security boundaries:

- **Domain allowlists**: `browseuse_action` and `http_request` reject URLs with domains not in `BROWSEUSE_ALLOWED_DOMAINS` / `HTTP_TOOL_ALLOWED_DOMAINS`
- **Scheme blocking**: `data:`, `file:`, `javascript:` schemes are blocked in BrowseUse
- **SQL injection prevention**: Column names validated to alphanumerics + underscores; `*` wildcard allowed
- **Row count caps**: `MAX_ROWS = 500` hard cap per CRUD operation
- **Output truncation**: Tool output capped at `TOOL_MAX_OUTPUT_CHARS = 20000` with metadata flag

## Tools

### 1. BrowseUse (`browseuse_action`)

**Purpose**: Safe web navigation and content extraction with SSRF protection.

**Input Schema** (`BrowseuseInput`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action_type` | `str` | Yes | `visit`, `extract`, or `fill` |
| `url` | `str` | Yes | Target URL (must pass domain allowlist) |
| `selectors` | `Dict[str, str]` | No | CSS selectors for extract action |
| `text` | `str` | No | Text for fill action |
| `screenshot` | `bool` | No | Whether to capture screenshot (returns `None` without remote endpoint) |
| `timeout_seconds` | `int` | No | Per-tool timeout (default 30) |

**Output Example**:

```json
{
  "ok": true,
  "data": {
    "action_type": "visit",
    "title": "Example Page",
    "links": [{"text": "Home", "url": "https://example.com"}]
  },
  "metadata": {
    "placeholder": true
  }
}
```

**SSRF Protection**: URL must match `BROWSEUSE_ALLOWED_DOMAINS` when configured; `data:`, `file:`, `javascript:` schemes are always blocked.

**Environment Variables**:

- `BROWSEUSE_ALLOWED_DOMAINS` — comma-separated allowlist (empty = dev mode, all http/https allowed)
- `BROWSEUSE_ENDPOINT` — remote API endpoint URL (optional)

### 2. Supabase CRUD (`supabase_crud`)

**Purpose**: Safe CRUD (select/insert/update/delete) against configured Supabase tables.

**Input Schemas**:

- `SupabaseCrudSelectInput`: `table`, `columns`, `where`, `order_by`, `limit`
- `SupabaseCrudInsertInput`: `table`, `records`, `returning`
- `SupabaseCrudUpdateInput`: `table`, `updates`, `where`, `returning`
- `SupabaseCrudDeleteInput`: `table`, `where`, `returning`

**Table Allowlist**: `SUPABASE_CRUD_ALLOWED_TABLES` — comma-separated list; tables not in list are rejected with `table_not_allowed`.

**Column Validation**: Names must be `*` or contain only alphanumerics and underscores; SQL injection patterns are rejected with `invalid_input`.

**Row Count Cap**: `MAX_ROWS = 500` — requests for more rows are capped.

**Output Truncation**: Output capped at `TOOL_MAX_OUTPUT_CHARS = 20000` with `original_size_chars` and `truncated_size_chars` metadata.

**Environment Variables**:

- `SUPABASE_CRUD_ALLOWED_TABLES` — comma-separated allowlist (empty = deny all by default)
- `SUPABASE_DATABASE_URL` — Supabase connection URL

**Example SELECT**:

```json
{
  "input": {"table": "users", "columns": ["id", "name"], "where": {"status": "active"}}
}
```

**Output**:

```json
{
  "ok": true,
  "data": {
    "rows": [{"id": 1, "name": "Alice"}],
    "row_count": 1
  },
  "metadata": {
    "operation": "select",
    "table": "users"
  }
}
```

### 3. N8N Webhook (`POST /tools/n8n-webhook/{mission_id}`)

**Purpose**: Authenticated webhook trigger that starts a mission run from N8N.

**Authentication**: `X-Meridian-Webhook-Secret` header must match `MERIDIAN_WEBHOOK_SECRET` env var.

**Request Body**:

| Field | Description |
|-------|-------------|
| `input_context` | Optional dict passed through as step input |

**Success Response**:

```json
{
  "run_id": "uuid-string",
  "mission_id": "test-mission-001",
  "message": "N8N webhook triggered run started"
}
```

**Error Responses**:

- `401` — "Invalid webhook secret" — wrong or missing `X-Meridian-Webhook-Secret` header
- `422` — mission_id not found or invalid

**Environment Variables**:

- `MERIDIAN_WEBHOOK_SECRET` — shared secret for webhook authentication (no default; must be set for production)

### 4. Integrations Status (`GET /integrations/status`)

**Purpose**: Health check endpoint that reports configuration status of all integrations.

**Response**:

```json
{
  "webhook": {
    "configured": true,
    "env": "MERIDIAN_WEBHOOK_SECRET"
  },
  "firecrawl": {
    "configured": false,
    "env": "FIRECRAWL_API_KEY"
  },
  "supabase": {
    "configured": true,
    "env": "SUPABASE_DATABASE_URL"
  },
  "http_allowlist": {
    "configured": false,
    "env": "HTTP_TOOL_ALLOWED_DOMAINS"
  }
}
```

## Configuration

### Required Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MERIDIAN_WEBHOOK_SECRET` | Webhook authentication secret | *None (must set for prod)* |
| `BROWSEUSE_ALLOWED_DOMAINS` | Comma-separated domain allowlist for BrowseUse | *empty (dev mode)* |
| `SUPABASE_CRUD_ALLOWED_TABLES` | Comma-separated table allowlist for CRUD | *empty (deny all)* |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BROWSEUSE_ENDPOINT` | Remote browseuse API endpoint | *None* |
| `FIRECRAWL_API_KEY` | Firecrawl API key | *None* |
| `SUPABASE_DATABASE_URL` | Supabase database URL | *None* |
| `HTTP_TOOL_ALLOWED_DOMAINS` | Comma-separated domain allowlist for HTTP requests | *empty (dev mode)* |

**.env.example**:

```env
# Application
APP_NAME=meridian
APP_VERSION=0.1.0
DEBUG=False
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/meridian
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/meridian

# Tool Sandbox
BROWSEUSE_ALLOWED_DOMAINS=example.com,api.test.com
BROWSEUSE_ENDPOINT=https://browseuse.example.com/api
SUPABASE_CRUD_ALLOWED_TABLES=users,orders,products
HTTP_TOOL_ALLOWED_DOMAINS=example.com,api.test.com

# Security
MERIDIAN_WEBHOOK_SECRET=my-super-secret-key
```

## API Endpoints

### `POST /tools/n8n-webhook/{mission_id}`

- **Auth**: `X-Meridian-Webhook-Secret` header
- **Body**: `{ "input_context": {...} }`
- **Returns**: `{ run_id, mission_id, message }`

### `GET /integrations/status`

- **Returns**: Config status of webhook, firecrawl, supabase, http_allowlist
- **No external calls** — only checks env var presence

### `POST /tools/execute`

- **Body**: `{ "tool_name": "supabase_crud", "input": {...} }`
- **Returns**: `ToolResult` with operation output

### `GET /tools`

- **Returns**: List of registered tools with input schemas