"""
MERIDIAN Approvals API — v1.

Placeholder routes for human-in-the-loop approval operations.
Returns 501 Not Implemented for all endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(request: Request):
    """List all approvals. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{approval_id}")
async def get_approval(approval_id: str, request: Request):
    """Get an approval by ID with step output and trace. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("/{approval_id}/decide")
async def decide_approval(approval_id: str, request: Request):
    """Submit a decision (approve/reject/modify) for an approval. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
