"""
MERIDIAN Application Configuration.

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

    # LLM Integration (Phase 2 Agent Runtime — LiteLLM)
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

    # Security
    SECRET_KEY: str = "change-me-in-production"
    API_KEY: Optional[str] = None

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
