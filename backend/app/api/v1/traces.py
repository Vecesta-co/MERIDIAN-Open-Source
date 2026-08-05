"""
MERIDIAN Traces API — v1.

Placeholder routes for trace/observability operations.
Returns 501 Not Implemented for all endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("")
async def list_traces(request: Request):
    """List all trace spans. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("")
async def create_span(request: Request):
    """Create a new trace span. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{span_id}")
async def get_span(span_id: str, request: Request):
    """Get a span by ID. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/runs/{run_id}")
async def get_run_traces(run_id: str, request: Request):
    """Get all spans for a run. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
