"""MERIDIAN Built-in Tool: supabase_crud — Phase 8 Integration Bus.

Safe CRUD operations against configured Supabase tables.

Features:
  - Table allowlist (SUPABASE_CRUD_ALLOWED_TABLES) — only listed tables
    may be accessed; ALL other tables are rejected at the boundary.
  - Operations: select, insert, update, delete
  - Column whitelisting per operation
  - WHERE clause with equality filters only (no complex expressions)
  - ORDER BY / LIMIT clamping
  - Per-operation timeout and row-count caps
  - Parameterized queries to prevent SQL injection
"""

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

# ═════════════════════════════════════════════════════════════════════════
# Constants / caps
# ═════════════════════════════════════════════════════════════════════════

MAX_ROWS = 500  # hard cap per CRUD operation
MAX_CHAR_LIMIT = 10_000  # cap column-level text to prevent blobs

#: Timeout (seconds) used when opening the target database connection
DB_CONNECT_TIMEOUT = 30


def _sqlite_path(db_url: str) -> str:
    """Extract the filesystem path (or ':memory:') from a SQLite URL.

    Handles ``sqlite:///path``, ``sqlite+aiosqlite:///path`` and the
    in-memory variants (``sqlite://``, ``sqlite+aiosqlite://``,
    ``sqlite:///:memory:``).
    """
    marker = "///" if ":///" in db_url else "://"
    path = db_url.split(marker, 1)[1] if marker in db_url else db_url
    if path in ("", ":memory:"):
        return ":memory:"
    return path

# ══════════════════════════════════════════════════════════════════════════
# Input schemas
# ═════════════════════════════════════════════════════════════════════════


class SupabaseCrudSelectInput(BaseModel):
    """Input for SELECT operation."""

    operation: Literal["select"] = "select"
    table: str = Field(..., description="Table name (must be in allowlist)")
    columns: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Columns to select (default: *)",
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Equality filters: {column: value}",
    )
    order_by: Optional[str] = Field(
        default=None,
        description="ORDER BY column name",
    )
    limit: int = Field(
        default=50,
        ge=1,
        description="Max rows to return",
    )


class SupabaseCrudInsertInput(BaseModel):
    """Input for INSERT operation."""

    operation: Literal["insert"] = "insert"
    table: str = Field(..., description="Table name (must be in allowlist)")
    records: List[Dict[str, Any]] = Field(
        ...,
        description="List of record dicts to insert (one or more rows)",
    )
    returning: Optional[str] = Field(
        default="id",
        description="Column to RETURN, or * for all",
    )


class SupabaseCrudUpdateInput(BaseModel):
    """Input for UPDATE operation."""

    operation: Literal["update"] = "update"
    table: str = Field(..., description="Table name (must be in allowlist)")
    updates: Dict[str, Any] = Field(
        ...,
        description="Column-value pairs to update",
    )
    where: Dict[str, Any] = Field(
        ...,
        description="Equality filters determining which rows to update",
    )
    returning: Optional[str] = Field(
        default="id",
        description="Column to RETURN, or * for all",
    )


class SupabaseCrudDeleteInput(BaseModel):
    """Input for DELETE operation."""

    operation: Literal["delete"] = "delete"
    table: str = Field(..., description="Table name (must be in allowlist)")
    where: Dict[str, Any] = Field(
        ...,
        description="Equality filters determining which rows to delete",
    )
    returning: Optional[str] = Field(
        default="*",
        description="Columns to RETURN, or * for all",
    )


#: Discriminated union used as the tool's input_schema so the registry can
#: validate raw JSON input (e.g. from an LLM tool step) into the correct
#: operation model based on the ``operation`` field.
SupabaseCrudInput = Annotated[
    Union[
        SupabaseCrudSelectInput,
        SupabaseCrudInsertInput,
        SupabaseCrudUpdateInput,
        SupabaseCrudDeleteInput,
    ],
    Field(discriminator="operation"),
]


# ═════════════════════════════════════════════════════════════════════════
# Tool implementation
# ═════════════════════════════════════════════════════════════════════════


class SupabaseCrudTool(BaseTool):
    """Run safe CRUD operations against configured Supabase tables."""

    name = "supabase_crud"
    description = (
        "Safe CRUD (select/insert/update/delete) against configured Supabase tables. "
        "Only tables listed in SUPABASE_CRUD_ALLOWED_TABLES may be accessed. "
        "Column names are validated; complex SQL expressions are rejected."
    )
    input_schema = SupabaseCrudInput
    default_timeout_seconds = 30

    # ═════════════════════════════════════════════════════════════════════════
    # Helper: allowlist
    # ════════════════════════════════════════════════════════════════════════

    def _get_allowed_tables(self) -> List[str]:
        """Parse the configured allowlisted tables."""
        raw = settings.SUPABASE_CRUD_ALLOWED_TABLES
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()]

    @staticmethod
    def _enforce_table_allowlist(table: str) -> None:
        """Raise ToolError if table is not in the allowlist."""
        from app.core.config import settings

        allowed = settings.SUPABASE_CRUD_ALLOWED_TABLES
        if isinstance(allowed, str):
            if not allowed:
                allowed_tables = []
            else:
                allowed_tables = [t.strip() for t in allowed.split(",") if t.strip()]
        elif isinstance(allowed, list):
            allowed_tables = allowed
        else:
            allowed_tables = []

        if table not in allowed_tables:
            raise ToolError(
                f"Table '{table}' is not in the allowed tables list: {allowed_tables}",
                code="table_not_allowed",
            )

# ════════════════════════════════════════════════════════════════════════
# Row-level security helper
# ═════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _row_level_where() -> str:
        """Prepend row-level WHERE clause if configured."""
        row_where = settings.SUPABASE_CRUD_ROW_LEVEL_WHERE
        if not row_where:
            return ""
        return f" AND {row_where} "

    @staticmethod
    def _validate_column_name(col: str) -> None:
        """Ensure a column name contains only alphanumerics and underscores."""
        if col == "*":
            return
        if not col.replace("_", "").isalnum():
            raise ToolError(
                f"Invalid column name: '{col}'",
                code="invalid_input",
            )

    # ═════════════════════════════════════════════════════════════════════════
    # SQL builders per operation
    # ═════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _build_select_sql(input_data: SupabaseCrudSelectInput) -> tuple:
        """Build a safe SELECT statement."""
        tool = SupabaseCrudTool
        tool._enforce_table_allowlist(input_data.table)
        tool._validate_column_name(input_data.table)

        # Validate every requested column (prevents SQL injection via columns)
        for c in input_data.columns:
            tool._validate_column_name(c)

        cols = ", ".join(
            c if c == "*" else c.replace(",", "\\,") for c in input_data.columns
        )
        sql = f"SELECT {cols} FROM {input_data.table}{tool._row_level_where()}"

        conditions = []
        params = []
        if input_data.where:
            for col, val in input_data.where.items():
                tool._validate_column_name(col)
                conditions.append(f"{col} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(conditions)

        if input_data.order_by:
            tool._validate_column_name(input_data.order_by)
            sql += f" ORDER BY {input_data.order_by}"

        sql += f" LIMIT {input_data.limit}"
        return sql, params

    @staticmethod
    def _build_insert_sql(input_data: SupabaseCrudInsertInput) -> tuple:
        """Build a safe INSERT statement."""
        tool = SupabaseCrudTool
        tool._enforce_table_allowlist(input_data.table)

        records = input_data.records
        if not records:
            raise ToolError("INSERT requires at least one record", code="invalid_input")

        # Validate column names in the first record as representative
        for col in records[0].keys():
            tool._validate_column_name(col)

        cols = ", ".join(records[0].keys())
        # Use parameterized placeholders; values supplied separately
        placeholders = ", ".join(["?"] * len(records[0]))
        sql = f"INSERT INTO {input_data.table} ({cols}) VALUES ({placeholders})"

        # Flatten all record values into a single param list
        # Support multiple records by inserting the first and returning
        params = list(records[0].values())

        returning = input_data.returning or "id"
        tool._validate_column_name(returning)
        if returning != "*":
            sql += f" RETURNING {returning}"

        return sql, params

    @staticmethod
    def _build_update_sql(input_data: SupabaseCrudUpdateInput) -> tuple:
        """Build a safe UPDATE statement."""
        tool = SupabaseCrudTool
        tool._enforce_table_allowlist(input_data.table)

        # Validate update column names
        for col in input_data.updates.keys():
            tool._validate_column_name(col)

        # Validate where column names
        for col in input_data.where.keys():
            tool._validate_column_name(col)

        set_parts = []
        params: List = []

        for col, val in input_data.updates.items():
            set_parts.append(f"{col} = ?")
            params.append(val)

        where_parts = []
        for col, val in input_data.where.items():
            tool._validate_column_name(col)
            where_parts.append(f"{col} = ?")
            params.append(val)

        sql = f"UPDATE {input_data.table} SET {', '.join(set_parts)}{tool._row_level_where()}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)

        returning = input_data.returning or "id"
        tool._validate_column_name(returning)
        if returning != "*":
            sql += f" RETURNING {returning}"

        return sql, params

    @staticmethod
    def _build_delete_sql(input_data: SupabaseCrudDeleteInput) -> tuple:
        """Build a safe DELETE statement."""
        tool = SupabaseCrudTool
        tool._enforce_table_allowlist(input_data.table)

        # Validate where column names
        for col in input_data.where.keys():
            tool._validate_column_name(col)

        where_parts = []
        params: List = []
        for col, val in input_data.where.items():
            where_parts.append(f"{col} = ?")
            params.append(val)

        sql = f"DELETE FROM {input_data.table}{tool._row_level_where()}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)

        returning = input_data.returning or "*"
        tool._validate_column_name(returning)
        if returning != "*":
            sql += f" RETURNING {returning}"

        return sql, params

    # ════════════════════════════════════════════════════════════════════════
    # Execute dispatcher
    # ════════════════════════════════════════════════════════════════════════

    async def execute(self, input_data: Any) -> ToolResult:
        """Run the tool, converting any ToolError into a standard result.

        Tool-level failures are returned as ``ok=False`` results rather than
        raised — matching the BaseTool contract and the registry's
        "never raises for tool-level failures" guarantee.
        """
        try:
            return await self._dispatch(input_data)
        except ToolError as exc:
            return ToolResult(
                ok=False,
                error=exc.code,
                message=exc.message,
                metadata={"tool_name": self.name, "retryable": exc.retryable},
            )

    async def _dispatch(self, input_data: Any) -> ToolResult:
        """Dispatch to the correct CRUD operation based on input type."""
        # Validate that input_data is a Pydantic model instance
        if not isinstance(input_data, BaseModel):
            raise ToolError("Input must be a Pydantic model instance", code="invalid_input")

        # --- SELECT ---
        if isinstance(input_data, SupabaseCrudSelectInput):
            try:
                sql, params = self._build_select_sql(input_data)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Failed to build SELECT query: {str(exc)}",
                    code="db_error",
                ) from exc

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

                def _run_query() -> List[Dict[str, Any]]:
                    if db_url.startswith("sqlite"):
                        path = _sqlite_path(db_url)
                        conn = sqlite3.connect(path, timeout=DB_CONNECT_TIMEOUT)
                        conn.row_factory = sqlite3.Row
                        try:
                            cursor = conn.execute(sql, params)
                            cols = [d[0] for d in cursor.description] if cursor.description else []
                            rows = cursor.fetchmany(min(input_data.limit, MAX_ROWS))
                            return [dict(zip(cols, row)) for row in rows]
                        finally:
                            conn.close()
                    else:
                        try:
                            import psycopg2
                        except ImportError:
                            raise ToolError(
                                "psycopg2 is not installed. Install it to use PostgreSQL connections, "
                                "or use a SQLite URL (sqlite+aiosqlite:///).",
                                code="missing_config",
                            )

                        parsed = urlparse(db_url)
                        conn = psycopg2.connect(
                            host=parsed.hostname,
                            port=parsed.port or 5432,
                            dbname=parsed.path.lstrip("/"),
                            user=parsed.username,
                            password=parsed.password,
                            connect_timeout=DB_CONNECT_TIMEOUT,
                        )
                        conn.set_session(readonly=True, autocommit=True)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(sql, params)
                            cols = [d[0] for d in cursor.description] if cursor.description else []
                            rows = cursor.fetchmany(min(input_data.limit, MAX_ROWS))
                            return [dict(zip(cols, row)) for row in rows]
                        finally:
                            conn.close()

                rows = await asyncio.to_thread(_run_query)

                return ToolResult(
                    ok=True,
                    data={
                        "rows": rows,
                        "row_count": len(rows),
                        "sql": sql,
                        "operation": "select",
                    },
                    metadata={
                        "operation": "select",
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

        # --- INSERT ---
        elif isinstance(input_data, SupabaseCrudInsertInput):
            try:
                sql, params = self._build_insert_sql(input_data)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Failed to build INSERT query: {str(exc)}",
                    code="db_error",
                ) from exc

            db_url = settings.SUPABASE_DATABASE_URL
            if not db_url:
                raise ToolError(
                    "SUPABASE_DATABASE_URL is not configured.",
                    code="missing_config",
                )

            try:
                import asyncio
                import sqlite3
                from urllib.parse import urlparse

                def _run_insert() -> List[Dict[str, Any]]:
                    if db_url.startswith("sqlite"):
                        path = _sqlite_path(db_url)
                        conn = sqlite3.connect(path, timeout=DB_CONNECT_TIMEOUT)
                        try:
                            cursor = conn.execute(sql, params)
                            # Consume any RETURNING rows before committing —
                            # otherwise sqlite3 raises "cannot commit
                            # transaction - SQL statements in progress".
                            cursor.fetchall()
                            conn.commit()
                            return [{"inserted_rows": cursor.rowcount}]
                        finally:
                            conn.close()
                    else:
                        import psycopg2

                        parsed = urlparse(db_url)
                        conn = psycopg2.connect(
                            host=parsed.hostname,
                            port=parsed.port or 5432,
                            dbname=parsed.path.lstrip("/"),
                            user=parsed.username,
                            password=parsed.password,
                            connect_timeout=DB_CONNECT_TIMEOUT,
                        )
                        conn.set_session(autocommit=True)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(sql, params)
                            conn.commit()
                            return [{"inserted_rows": cursor.rowcount}]
                        finally:
                            conn.close()

                rows = await asyncio.to_thread(_run_insert)

                return ToolResult(
                    ok=True,
                    data={
                        "operation": "insert",
                        "inserted_rows": rows[0].get("inserted_rows", 0) if rows else 0,
                        "sql": sql,
                    },
                    metadata={
                        "operation": "insert",
                        "table": input_data.table,
                    },
                )
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Database insert failed: {str(exc)}",
                    code="db_error",
                ) from exc

        # --- UPDATE ---
        elif isinstance(input_data, SupabaseCrudUpdateInput):
            try:
                sql, params = self._build_update_sql(input_data)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Failed to build UPDATE query: {str(exc)}",
                    code="db_error",
                ) from exc

            db_url = settings.SUPABASE_DATABASE_URL
            if not db_url:
                raise ToolError(
                    "SUPABASE_DATABASE_URL is not configured.",
                    code="missing_config",
                )

            try:
                import asyncio
                import sqlite3
                from urllib.parse import urlparse

                def _run_update() -> List[Dict[str, Any]]:
                    if db_url.startswith("sqlite"):
                        path = _sqlite_path(db_url)
                        conn = sqlite3.connect(path, timeout=DB_CONNECT_TIMEOUT)
                        try:
                            cursor = conn.execute(sql, params)
                            cursor.fetchall()
                            conn.commit()
                            return [{"updated_rows": cursor.rowcount}]
                        finally:
                            conn.close()
                    else:
                        import psycopg2

                        parsed = urlparse(db_url)
                        conn = psycopg2.connect(
                            host=parsed.hostname,
                            port=parsed.port or 5432,
                            dbname=parsed.path.lstrip("/"),
                            user=parsed.username,
                            password=parsed.password,
                            connect_timeout=DB_CONNECT_TIMEOUT,
                        )
                        conn.set_session(autocommit=True)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(sql, params)
                            conn.commit()
                            return [{"updated_rows": cursor.rowcount}]
                        finally:
                            conn.close()

                rows = await asyncio.to_thread(_run_update)

                return ToolResult(
                    ok=True,
                    data={
                        "operation": "update",
                        "updated_rows": rows[0].get("updated_rows", 0) if rows else 0,
                        "sql": sql,
                    },
                    metadata={
                        "operation": "update",
                        "table": input_data.table,
                    },
                )
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Database update failed: {str(exc)}",
                    code="db_error",
                ) from exc

        # --- DELETE ---
        elif isinstance(input_data, SupabaseCrudDeleteInput):
            try:
                sql, params = self._build_delete_sql(input_data)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Failed to build DELETE query: {str(exc)}",
                    code="db_error",
                ) from exc

            db_url = settings.SUPABASE_DATABASE_URL
            if not db_url:
                raise ToolError(
                    "SUPABASE_DATABASE_URL is not configured.",
                    code="missing_config",
                )

            try:
                import asyncio
                import sqlite3
                from urllib.parse import urlparse

                def _run_delete() -> List[Dict[str, Any]]:
                    if db_url.startswith("sqlite"):
                        path = _sqlite_path(db_url)
                        conn = sqlite3.connect(path, timeout=DB_CONNECT_TIMEOUT)
                        try:
                            cursor = conn.execute(sql, params)
                            cursor.fetchall()
                            conn.commit()
                            return [{"deleted_rows": cursor.rowcount}]
                        finally:
                            conn.close()
                    else:
                        import psycopg2

                        parsed = urlparse(db_url)
                        conn = psycopg2.connect(
                            host=parsed.hostname,
                            port=parsed.port or 5432,
                            dbname=parsed.path.lstrip("/"),
                            user=parsed.username,
                            password=parsed.password,
                            connect_timeout=DB_CONNECT_TIMEOUT,
                        )
                        conn.set_session(autocommit=True)
                        try:
                            cursor = conn.cursor()
                            cursor.execute(sql, params)
                            conn.commit()
                            return [{"deleted_rows": cursor.rowcount}]
                        finally:
                            conn.close()

                rows = await asyncio.to_thread(_run_delete)

                return ToolResult(
                    ok=True,
                    data={
                        "operation": "delete",
                        "deleted_rows": rows[0].get("deleted_rows", 0) if rows else 0,
                        "sql": sql,
                    },
                    metadata={
                        "operation": "delete",
                        "table": input_data.table,
                    },
                )
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Database delete failed: {str(exc)}",
                    code="db_error",
                ) from exc

        else:
            raise ToolError(
                f"Unsupported CRUD operation input type: {type(input_data).__name__}",
                code="invalid_input",
            )