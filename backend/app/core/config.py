"""MERIDIAN Application Configuration.

Uses Pydantic Settings to load environment variables with validation.
All secrets and sensitive values are handled via environment variables only.
"""
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "meridian"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/meridian"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/meridian"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # Redis (task queue for Phase 2 Agent Runtime)
    REDIS_URL: Optional[str] = None

    # LLM Integration (Phase 2 Agent Runtime - LiteLLM)
    # LiteLLM reads provider-specific keys from env vars (e.g. OPENAI_API_KEY,
    # ANTHROPIC_API_KEY, GEMINI_API_KEY) or you can set LITELLM_API_KEY.
    LITELLM_MODEL: str = "gpt-4o-mini"
    LITELLM_API_KEY: Optional[str] = None
    LITELLM_API_BASE: Optional[str] = None

    # Run/Worker timeouts (Phase 2)
    RUN_TIMEOUT_MARGIN_SECONDS: int = 60  # margin added to sum of step timeouts
    STALE_RUN_THRESHOLD_MINUTES: int = 30  # watchdog: running runs older than this are marked failed
    WATCHDOG_INTERVAL_SECONDS: int = 60  # period between watchdog reap_stale_runs sweeps
    MAX_CONTEXT_CHARS: int = 20000  # cap per prior step output rendered into LLM prompts

    # Tool Sandbox (Phase 3)
    TOOL_DEFAULT_TIMEOUT_SECONDS: int = 30  # default per-tool timeout
    TOOL_MAX_OUTPUT_CHARS: int = 20000  # cap tool output passed to LLM (truncate + note)
    HTTP_TOOL_ALLOWED_DOMAINS: str = ""  # comma-separated allowlist; empty = unrestricted (dev)
    BROWSEUSE_ALLOWED_DOMAINS: str = ""  # comma-separated allowlist; empty = unrestricted (dev)
    BROWSEUSE_ENDPOINT: Optional[str] = None  # remote browseuse endpoint URL
    FIRECRAWL_API_KEY: Optional[str] = None  # required for firecrawl_scrape tool
    SUPABASE_DATABASE_URL: Optional[str] = None  # for supabase_query / rag_query tools
    SUPABASE_QUERY_ALLOWED_TABLES: str = ""  # comma-separated table allowlist for SELECT
    SUPABASE_QUERY_NAMED_QUERIES: str = ""  # JSON map: {name: sql} predefined safe queries
    SUPABASE_CRUD_ALLOWED_TABLES: str = ""  # comma-separated table allowlist for CRUD ops
    RAG_COLLECTIONS: str = ""  # comma-separated allowed pgvector collections
    LITELLM_EMBEDDING_MODEL: str = "text-embedding-3-small"  # embedding model for rag_query

    # Trace Engine (Phase 4) - Model Pricing Table
    # Cost per 1K tokens (USD). Used to compute LLM span cost on summary/trace
    # requests. Unknown models fall back to the "default" entry.
    MODEL_PRICING: dict = {
        "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.00060},
        "gpt-4o": {"input_per_1k": 0.00250, "output_per_1k": 0.01000},
        "claude-3-5-sonnet": {"input_per_1k": 0.00300, "output_per_1k": 0.01500},
        "claude-3-haiku": {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
        "gemini-1.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
        "default": {"input_per_1k": 0.00010, "output_per_1k": 0.00010},
    }

    # Trace retention policy (Phase 4)
    # Spans / traces older than this many days are purged by the watchdog.
    TRACE_RETENTION_DAYS: int = 30

    # Eval Suite (Phase 5)
    EVAL_LLM_TIMEOUT_SECONDS: int = 60  # per llm_judge call timeout
    EVAL_MAX_ARTIFACT_CHARS: int = 8000  # artifact truncation before judge prompt
    EVAL_LLM_MAX_TOKENS: int = 512  # cap on judge output

    # Security
    SECRET_KEY: str = "change-me-in-production"
    API_KEY: Optional[str] = None
    MERIDIAN_API_KEY: Optional[str] = None
    MERIDIAN_WEBHOOK_SECRET: Optional[str] = None

    # Row-level security for CRUD operations
    # If set, this AND-expression is prepended to WHERE clauses in all CRUD ops
    # Example: "user_id = current_user()" or "department_id = 42"
    SUPABASE_CRUD_ROW_LEVEL_WHERE: str = ""  # e.g., "company_id = current_company()"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    # When True (and Redis/RQ is unavailable), runs are executed directly
    # in the API process instead of being enqueued for a worker. Useful for
    # single-process demo/prosumer deployments without a Redis server.
    EXECUTE_RUNS_IN_PROCESS: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()