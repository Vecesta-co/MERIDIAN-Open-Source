"""
MERIDIAN Mission Versions API — v1.

Placeholder routes for mission version management.
Returns 501 Not Implemented for all endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/missions/{mission_id}/versions", tags=["missions"])


@router.get("")
async def list_mission_versions(mission_id: str, request: Request):
    """List all versions of a mission. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("")
async def create_mission_version(mission_id: str, request: Request):
    """Create a new version of a mission. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{version_id}")
async def get_mission_version(mission_id: str, version_id: str, request: Request):
    """Get a specific mission version. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.delete("/{version_id}")
async def delete_mission_version(mission_id: str, version_id: str, request: Request):
    """Delete a mission version. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
