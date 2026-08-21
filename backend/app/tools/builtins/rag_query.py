"""
MERIDIAN Built-in Tool: rag_query — Phase 3 Tool Sandbox.

Queries a pgvector collection in Postgres for similar documents.

Inputs:
  - collection: name of the vector collection/table
  - query_text: the text to search for
  - top_k: number of results to return

Returns matched documents with similarity scores.

The embedding is generated via LiteLLM (text-embedding model) and the
vector search runs against a Postgres table with a pgvector column.
"""

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.base import BaseTool, ToolError, ToolResult

logger = get_logger(__name__)

# Default embedding model for RAG queries
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class RagQueryInput(BaseModel):
    """Input schema for the rag_query tool."""

    collection: str = Field(..., description="Name of the vector collection/table to search")
    query_text: str = Field(..., min_length=1, description="The text to search for")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results to return")
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="Query timeout in seconds")


class RagQueryTool(BaseTool):
    """Query a pgvector collection for similar documents."""

    name = "rag_query"
    description = "Search a pgvector collection for documents similar to the query text. Returns matched docs with scores."
    input_schema = RagQueryInput
    default_timeout_seconds = 30

    def _get_allowed_collections(self) -> List[str]:
        """Parse the configured allowed collections."""
        raw = settings.RAG_COLLECTIONS
        if not raw:
            return []
        return [c.strip() for c in raw.split(",") if c.strip()]

    async def _embed(self, text: str, timeout_seconds: int) -> List[float]:
        """Generate an embedding vector via LiteLLM."""
        try:
            import asyncio
            from importlib.util import find_spec

            if find_spec("litellm") is None:
                raise ImportError("litellm is not installed")
        except ImportError as exc:
            raise ToolError(
                "LiteLLM is not installed. Run `pip install litellm`.",
                code="missing_dependency",
            ) from exc

        model = settings.LITELLM_EMBEDDING_MODEL or DEFAULT_EMBEDDING_MODEL
        try:
            # Run the blocking embedding call in a thread pool so it does
            # NOT block the asyncio event loop. The tool-level timeout
            # (asyncio.timeout) can then interrupt it.
            def _embed_sync() -> List[float]:
                import litellm as _litellm
                response = _litellm.embedding(
                    model=model,
                    input=[text],
                )
                return response.data[0]["embedding"]

            embedding = await asyncio.to_thread(_embed_sync)
            return embedding
        except Exception as exc:
            raise ToolError(
                f"Embedding generation failed: {str(exc)}",
                code="embedding_error",
                retryable=True,
            ) from exc

    async def execute(self, input_data: RagQueryInput) -> ToolResult:
        """Execute the RAG query."""
        # Validate collection is allowed
        allowed = self._get_allowed_collections()
        if allowed and input_data.collection not in allowed:
            raise ToolError(
                f"Collection '{input_data.collection}' is not in the allowed collections: {allowed}",
                code="collection_not_allowed",
            )

        db_url = settings.SUPABASE_DATABASE_URL
        if not db_url:
            raise ToolError(
                "SUPABASE_DATABASE_URL is not configured. Set it to enable this tool.",
                code="missing_config",
            )

        # Generate embedding
        embedding = await self._embed(input_data.query_text, input_data.timeout_seconds)

        # Build the vector literal for pgvector
        vector_literal = "[" + ",".join(str(x) for x in embedding) + "]"

        # Validate collection name (alphanumeric + underscore only)
        if not input_data.collection.replace("_", "").isalnum():
            raise ToolError(
                f"Invalid collection name: '{input_data.collection}'",
                code="invalid_input",
            )

        sql = f"""
            SELECT id, content, metadata, 1 - (embedding <=> '{vector_literal}'::vector) AS similarity
            FROM {input_data.collection}
            ORDER BY embedding <=> '{vector_literal}'::vector
            LIMIT {input_data.top_k}
        """

        try:
            import asyncio
            import psycopg2
            from urllib.parse import urlparse

            # Run the blocking DB query in a thread pool so it does NOT
            # block the asyncio event loop. The tool-level timeout
            # (asyncio.timeout) can then interrupt it.
            def _run_vector_query() -> List[Dict[str, Any]]:
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
                    cursor.execute(sql)
                    cols = [d[0] for d in cursor.description] if cursor.description else []
                    return [dict(zip(cols, row)) for row in cursor.fetchall()]
                finally:
                    conn.close()

            rows = await asyncio.to_thread(_run_vector_query)

            return ToolResult(
                ok=True,
                data={
                    "collection": input_data.collection,
                    "query_text": input_data.query_text,
                    "results": rows,
                    "result_count": len(rows),
                },
                metadata={
                    "collection": input_data.collection,
                    "top_k": input_data.top_k,
                    "result_count": len(rows),
                },
            )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"RAG query failed: {str(exc)}",
                code="db_error",
            ) from exc
