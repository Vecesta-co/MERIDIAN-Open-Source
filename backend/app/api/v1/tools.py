"""
MERIDIAN Tools API — v1.

Placeholder routes for tool registry operations.
Returns 501 Not Implemented for all endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request):
    """List all registered tools. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("")
async def register_tool(request: Request):
    """Register a new tool. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{tool_id}")
async def get_tool(tool_id: str, request: Request):
    """Get a tool by ID. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.put("/{tool_id}")
async def update_tool(tool_id: str, request: Request):
    """Update a tool definition. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str, request: Request):
    """Delete a tool. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
