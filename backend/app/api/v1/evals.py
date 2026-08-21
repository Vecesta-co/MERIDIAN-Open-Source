"""
MERIDIAN Evals API — v1.

Phase 5: Eval Suite — definition management.

Endpoints:
- POST   /evals                Create an eval definition
- GET    /evals                List eval definitions
- GET    /evals/{id}           Get an eval definition
- PUT    /evals/{id}           Update an eval definition
- DELETE /evals/{id}           Delete an eval definition
- GET    /evals/results        (Phase 5+ placeholder)
- GET    /evals/results/{id}   (Phase 5+ placeholder)
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models.schemas import (
    EvalDefinitionCreate,
    EvalDefinitionResponse,
    EvalDefinitionUpdate,
    NotImplementedResponse,
)
from app.services import eval_service

router = APIRouter(prefix="/evals", tags=["evals"])
logger = get_logger(__name__)


def _http_400(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, eval_service.EvalValidationError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    return _http_400(str(exc))


@router.get("", response_model=List[EvalDefinitionResponse])
async def list_eval_definitions(
    session: AsyncSession = Depends(get_db_session),
):
    """List all eval definitions, newest first."""
    try:
        definitions = await eval_service.list_eval_definitions(session)
        return [
            eval_service._definition_to_response(d) for d in definitions
        ]
    except Exception as exc:
        logger.error("Failed to list eval definitions: %s", str(exc))
        raise _http_400("Failed to list eval definitions")


@router.post("", response_model=EvalDefinitionResponse, status_code=201)
async def create_eval_definition(
    payload: EvalDefinitionCreate,
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new eval definition."""
    try:
        definition = await eval_service.create_eval_definition(session, payload)
        return eval_service._definition_to_response(definition)
    except Exception as exc:
        raise _map_error(exc)


# NOTE: /results placeholders are declared BEFORE /{eval_id} so they are
# matched first (FastAPI matches routes in declaration order).


@router.get("/results")
async def list_eval_results(request: Request):
    """List eval results. Not yet implemented (Phase 5+)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/results/{result_id}")
async def get_eval_result(result_id: str, request: Request):
    """Get an eval result by ID. Not yet implemented (Phase 5+)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{eval_id}", response_model=EvalDefinitionResponse)
async def get_eval_definition(
    eval_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Get an eval definition by ID."""
    try:
        return await eval_service.get_eval_definition_or_404(session, eval_id)
    except Exception as exc:
        raise _map_error(exc)


@router.put("/{eval_id}", response_model=EvalDefinitionResponse)
async def update_eval_definition(
    eval_id: uuid.UUID,
    payload: EvalDefinitionUpdate,
    session: AsyncSession = Depends(get_db_session),
):
    """Update an eval definition."""
    try:
        definition = await eval_service.update_eval_definition(
            session, eval_id, payload
        )
        return eval_service._definition_to_response(definition)
    except Exception as exc:
        raise _map_error(exc)


@router.delete("/{eval_id}", status_code=204)
async def delete_eval_definition(
    eval_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
):
    """Delete an eval definition."""
    try:
        await eval_service.delete_eval_definition(session, eval_id)
    except Exception as exc:
        raise _map_error(exc)
    return None
