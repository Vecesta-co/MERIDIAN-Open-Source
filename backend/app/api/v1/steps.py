"""
MERIDIAN Steps API — v1.

Placeholder routes for step CRUD operations within a mission version.
Returns 501 Not Implemented for all endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/missions/{mission_id}/versions/{version_id}/steps", tags=["steps"])


@router.get("")
async def list_steps(mission_id: str, version_id: str, request: Request):
    """List all steps in a mission version. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("")
async def create_step(mission_id: str, version_id: str, request: Request):
    """Create a new step in a mission version. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{step_id}")
async def get_step(mission_id: str, version_id: str, step_id: str, request: Request):
    """Get a specific step. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.put("/{step_id}")
async def update_step(mission_id: str, version_id: str, step_id: str, request: Request):
    """Update a step. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.delete("/{step_id}")
async def delete_step(mission_id: str, version_id: str, step_id: str, request: Request):
    """Delete a step. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
