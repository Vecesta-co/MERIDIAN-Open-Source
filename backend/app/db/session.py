"""
MERIDIAN Database Session Management.

Provides async database session factory and dependency injection.
Uses SQLAlchemy async engine with connection pooling.
"""

from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# Create async engine.
# SQLite (local dev/testing) does not support pool_size / max_overflow.
_engine_kwargs: dict = {
    "echo": settings.DB_ECHO,
    "pool_pre_ping": True,
}

if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite in-memory or file-based engine (no pooling args, no pre-ping)
    _engine_kwargs.pop("pool_pre_ping", None)
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)


# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an async database session.

    Yields:
        An AsyncSession instance for the request lifecycle.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Database session rolled back: %s", str(exc))
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """
    Check if the database connection is alive.

    Uses a short timeout to avoid hanging when database is unavailable.
    Returns:
        True if connection is healthy, False otherwise.
    """
    try:
        import asyncio
        async with asyncio.timeout(3.0):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except asyncio.TimeoutError:
        logger.warning("Database connection check timed out")
        return False
    except Exception as exc:
        logger.error("Database connection check failed: %s", str(exc))
        return False
