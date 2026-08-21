"""
MERIDIAN Built-in Tool: supabase_query — Phase 3 Tool Sandbox.

Safe database query tool. Does NOT allow arbitrary SQL by default.

Two safe modes:
  1. Named queries: execute a predefined SQL query from the
     SUPABASE_QUERY_NAMED_QUERIES config (JSON map of name → SQL).
  2. Table SELECT: SELECT columns FROM an allowlisted table with
     optional WHERE/ORDER BY/LIMIT — but only on tables listed in
     SUPABASE_QUERY_ALLOWED_TABLES.

Arbitrary SQL is rejected unless explicitly configured as a named
query by the platform operator.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

# Maximum rows returned to prevent memory exhaustion
MAX_ROWS = 100


class SupabaseQueryInput(BaseModel):
    """Input schema for the supabase_query tool."""

    query_name: Optional[str] = Field(
        default=None,
        description="Name of a predefined named query (configured by the operator)",
    )
    table: Optional[str] = Field(
        default=None,
        description="Table name to SELECT from (must be in the allowlist)",
    )
    columns: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Columns to select (default: *)",
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional equality filters: {column: value}",
    )
    order_by: Optional[str] = Field(default=None, description="Optional ORDER BY column")
    limit: int = Field(default=50, ge=1, le=MAX_ROWS, description="Max rows to return")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="Query timeout in seconds")


class SupabaseQueryTool(BaseTool):
    """Run safe SELECT queries against the configured database."""

    name = "supabase_query"
    description = "Run a safe SELECT query against the configured database. Supports named queries or SELECT on allowlisted tables."
    input_schema = SupabaseQueryInput
    default_timeout_seconds = 30

    def _get_named_queries(self) -> Dict[str, str]:
        """Parse the configured named queries JSON."""
        raw = settings.SUPABASE_QUERY_NAMED_QUERIES
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            logger.error("SUPABASE_QUERY_NAMED_QUERIES is not valid JSON")
        return {}

    def _get_allowed_tables(self) -> List[str]:
        """Parse the configured allowlisted tables."""
        raw = settings.SUPABASE_QUERY_ALLOWED_TABLES
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    def _build_safe_select(self, input_data: SupabaseQueryInput) -> str:
        """Build a safe SELECT statement from structured input."""
        if not input_data.table:
            raise ToolError(
                "Either 'query_name' or 'table' must be provided",
                code="invalid_input",
            )

        allowed_tables = self._get_allowed_tables()
        if input_data.table not in allowed_tables:
            raise ToolError(
                f"Table '{input_data.table}' is not in the allowed tables list: {allowed_tables}",
                code="table_not_allowed",
            )

        # Validate column names (alphanumeric + underscore only)
        for col in input_data.columns:
            if not col.replace("_", "").isalnum() and col != "*":
                raise ToolError(
                    f"Invalid column name: '{col}'",
                    code="invalid_input",
                )

        cols = ", ".join(input_data.columns)
        sql = f"SELECT {cols} FROM {input_data.table}"

        # Build WHERE from equality filters (parameterized values)
        if input_data.where:
            conditions = []
            params: List[Any] = []
            for col, val in input_data.where.items():
                if not col.replace("_", "").isalnum():
                    raise ToolError(
                        f"Invalid column name in where: '{col}'",
                        code="invalid_input",
                    )
                conditions.append(f"{col} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(conditions)

        if input_data.order_by:
            if not input_data.order_by.replace("_", "").isalnum():
                raise ToolError(
                    f"Invalid order_by column: '{input_data.order_by}'",
                    code="invalid_input",
                )
            sql += f" ORDER BY {input_data.order_by}"

        sql += f" LIMIT {input_data.limit}"
        return sql

    async def execute(self, input_data: SupabaseQueryInput) -> ToolResult:
        """Execute the safe query."""
        # Resolve named query or build safe SELECT
        if input_data.query_name:
            named = self._get_named_queries()
            sql = named.get(input_data.query_name)
            if sql is None:
                raise ToolError(
                    f"Named query '{input_data.query_name}' is not configured",
                    code="unknown_named_query",
                )
            params: List[Any] = []
        else:
            sql = self._build_safe_select(input_data)
            params = list((input_data.where or {}).values())

        # Connect to the database
        db_url = settings.SUPABASE_DATABASE_URL
        if not db_url:
            raise ToolError(
                "SUPABASE_DATABASE_URL is not configured. Set it to enable this tool.",
                code="missing_config",
            )

        try:
            import asyncio
            import sqlite3
            from urllib.parse import urlparse

            # Run the blocking DB query in a thread pool so it does NOT
            # block the asyncio event loop. This lets the tool-level
            # timeout (asyncio.timeout) interrupt the operation.
            def _run_query() -> List[Dict[str, Any]]:
                # Support both sqlite and postgres URLs
                if db_url.startswith("sqlite"):
                    # Extract path from sqlite:///path
                    path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
                    conn = sqlite3.connect(path, timeout=input_data.timeout_seconds)
                    conn.row_factory = sqlite3.Row
                    try:
                        cursor = conn.execute(sql, params)
                        return [dict(row) for row in cursor.fetchmany(MAX_ROWS)]
                    finally:
                        conn.close()
                else:
                    # Postgres via psycopg2 (sync, run in thread pool)
                    import psycopg2

                    parsed = urlparse(db_url)
                    conn = psycopg2.connect(
                        host=parsed.hostname,
                        port=parsed.port or 5432,
                        dbname=parsed.path.lstrip("/"),
                        user=parsed.username,
                        password=parsed.password,
                        connect_timeout=input_data.timeout_seconds,
                    )
                    conn.set_session(readonly=True, autocommit=True)
                    try:
                        cursor = conn.cursor()
                        cursor.execute(sql, params)
                        cols = [d[0] for d in cursor.description] if cursor.description else []
                        return [dict(zip(cols, row)) for row in cursor.fetchmany(MAX_ROWS)]
                    finally:
                        conn.close()

            rows = await asyncio.to_thread(_run_query)

            return ToolResult(
                ok=True,
                data={
                    "rows": rows,
                    "row_count": len(rows),
                    "sql": sql,
                },
                metadata={
                    "query_name": input_data.query_name,
                    "table": input_data.table,
                    "row_count": len(rows),
                },
            )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"Database query failed: {str(exc)}",
                code="db_error",
            ) from exc
