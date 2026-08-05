"""
MERIDIAN Health Endpoint Tests.

Tests for the /health endpoint:
- Must return 200 OK
- Must have correct response shape
- Should report database connectivity status
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(async_client: AsyncClient):
    """GET /health must return 200 OK."""
    response = await async_client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_shape(async_client: AsyncClient):
    """GET /health must return correct JSON structure."""
    response = await async_client.get("/health")
    data = response.json()

    assert "status" in data
    assert "version" in data
    assert "timestamp" in data
    assert "database_connected" in data

    assert data["status"] in ("healthy", "degraded")
    assert isinstance(data["version"], str)
    assert isinstance(data["database_connected"], bool)


@pytest.mark.asyncio
async def test_health_version(async_client: AsyncClient):
    """GET /health must return the app version."""
    from app.core.config import settings

    response = await async_client.get("/health")
    data = response.json()
    assert data["version"] == settings.APP_VERSION


@pytest.mark.asyncio
async def test_health_content_type(async_client: AsyncClient):
    """GET /health must return application/json."""
    response = await async_client.get("/health")
    assert response.headers["content-type"] == "application/json"
