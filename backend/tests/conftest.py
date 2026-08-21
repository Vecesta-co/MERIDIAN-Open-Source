"""
MERIDIAN Test Configuration.

Provides shared fixtures and test configuration for all test modules.
Uses an in-memory SQLite database for isolated, dependency-free testing.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import Settings, settings
from app.db.models import Base
from app.db.session import get_db_session


# ──────────────────────────────────────────────
# In-memory SQLite test database
# ──────────────────────────────────────────────

test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestSessionFactory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Override the FastAPI dependency to use the test database."""
    async with TestSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Create an async HTTP client for testing FastAPI endpoints.

    Uses ASGITransport to run the app against an in-memory SQLite DB.
    Tables are recreated for each test for isolation.
    """
    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override the DB dependency
    app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Clean up
    app.dependency_overrides.clear()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def app_settings() -> Settings:
    """Return the current application settings for test assertions."""
    return settings


@pytest.fixture
def mission_payload() -> Dict[str, str]:
    """Payload for creating a test mission via N8N webhook."""
    return {
        "id": "test-mission-001",
        "name": "Test Mission",
        "description": "A test mission for N8N webhook testing",
        "category": "integration",
        "goal": "Test goal for mission execution",
        "steps": [
            {
                "key": "step1",
                "name": "First step",
                "step_type": "llm",
                "agent_key": "agent1",
                "prompt_template": "Execute the task",
            }
        ],
    }
