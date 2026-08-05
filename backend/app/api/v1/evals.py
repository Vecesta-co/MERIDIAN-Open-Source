# MERIDIAN Evals API — v1.
# Placeholder routes for evaluation operations.
# Returns 501 Not Implemented for all endpoints.

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.schemas import NotImplementedResponse

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get("")
async def list_eval_definitions(request: Request):
    """List all eval definitions. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.post("")
async def create_eval_definition(request: Request):
    """Create a new eval definition. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{eval_id}")
async def get_eval_definition(eval_id: str, request: Request):
    """Get an eval definition by ID. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.put("/{eval_id}")
async def update_eval_definition(eval_id: str, request: Request):
    """Update an eval definition. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.delete("/{eval_id}")
async def delete_eval_definition(eval_id: str, request: Request):
    """Delete an eval definition. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/results")
async def list_eval_results(request: Request):
    """List eval results. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/results/{result_id}")
async def get_eval_result(result_id: str, request: Request):
    """Get an eval result by ID. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
