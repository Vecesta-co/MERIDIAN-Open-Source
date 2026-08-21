"""
MERIDIAN API v1 Placeholder Route Tests.

Tests that all non-health API routes return 501 Not Implemented:
- All /api/v1/* endpoints must return 501
- Must have consistent error response shape
"""

import pytest
from httpx import AsyncClient


# List of (method, path) tuples for all v1 placeholder endpoints
# Paths must match the actual router prefixes defined in routers.
API_V1_ENDPOINTS = [
    # Missions (DELETE only; other missions endpoints implemented in Phase 1)
    ("DELETE", "/api/v1/missions/00000000-0000-0000-0000-000000000001"),
    # Runs — core lifecycle endpoints implemented in Phase 2 (POST/GET/cancel).
    # Trace/summary/spans implemented in Phase 4. Evals implemented in Phase 5.
    # Tools — GET /tools and POST /tools/execute are implemented in Phase 3.
    # Registration endpoints remain 501 placeholders.
    ("POST", "/api/v1/tools"),
    ("GET", "/api/v1/tools/00000000-0000-0000-0000-000000000001"),
    ("PUT", "/api/v1/tools/00000000-0000-0000-0000-000000000001"),
    ("DELETE", "/api/v1/tools/00000000-0000-0000-0000-000000000001"),
    # Evals — definition CRUD + run evals implemented in Phase 5.
    # Global results listing remains a 501 placeholder.
    ("GET", "/api/v1/evals/results"),
    ("GET", "/api/v1/evals/results/00000000-0000-0000-0000-000000000001"),
    # Traces
    ("GET", "/api/v1/traces"),
    ("POST", "/api/v1/traces"),
    ("GET", "/api/v1/traces/00000000-0000-0000-0000-000000000001"),
    ("GET", "/api/v1/traces/runs/00000000-0000-0000-0000-000000000001"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", API_V1_ENDPOINTS)
async def test_api_v1_returns_501(async_client: AsyncClient, method: str, path: str):
    """All /api/v1/* endpoints must return 501 Not Implemented."""
    response = await async_client.request(method, path)
    assert response.status_code == 501, (
        f"Expected 501 for {method} {path}, got {response.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", API_V1_ENDPOINTS[:5])  # Test first 5 for shape
async def test_api_v1_501_response_shape(
    async_client: AsyncClient, method: str, path: str
):
    """501 responses must have the correct error response shape."""
    response = await async_client.request(method, path)
    data = response.json()

    assert "detail" in data
    assert "path" in data
    assert "method" in data

    assert data["detail"] == "Not Implemented"
    assert isinstance(data["path"], str)
    assert isinstance(data["method"], str)
