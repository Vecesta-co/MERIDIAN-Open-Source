"""
MERIDIAN Runs API — v1.

Phase 2 — Agent Runtime. Implements run lifecycle endpoints:
  - POST /runs                 : create a run from a published mission and enqueue it
  - GET  /runs/{run_id}        : get run detail (with steps + spans)
  - GET  /runs/{run_id}/steps  : get run steps
  - POST /runs/{run_id}/cancel : request cancellation

Trace / summary / evals endpoints remain 501 placeholders (Phase 4+).
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models.schemas import (
    NotImplementedResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunResponse,
    RunStepDetailResponse,
)
from app.services.run_service import (
    RunValidationError,
    cancel_run,
    create_run,
    get_run_detail,
    get_run_steps,
)
from app.services.worker import enqueue_run

logger = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunResponse])
async def list_runs(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """List all runs (most recent first)."""
    from app.services.run_service import list_runs as _list_runs
    runs = await _list_runs(db)
    return [RunResponse.model_validate(r) for r in runs]


@router.post("", response_model=RunResponse, status_code=201)
async def start_run(
    payload: RunCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Start a new run from a published mission.

    Creates a pending run, enqueues it on the RQ worker, and returns
    the run metadata. The worker executes the mission asynchronously.
    """
    try:
        run = await create_run(
            db,
            mission_id=payload.mission_id,
            input_context=payload.input_context,
        )
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # Enqueue the run for the worker (best-effort — if Redis is down,
    # the run stays pending and will be picked up by the reaper).
    try:
        enqueue_run(run.id)
    except Exception as exc:
        logger.warning("Run %s created but failed to enqueue: %s", run.id, exc)

    return RunResponse.model_validate(run)


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Get a run by ID with its steps and spans."""
    try:
        detail = await get_run_detail(db, run_id)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RunDetailResponse.model_validate(detail)


@router.get("/{run_id}/steps", response_model=list[RunStepDetailResponse])
async def get_run_steps_endpoint(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Get the run steps for a run."""
    try:
        steps = await get_run_steps(db, run_id)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [RunStepDetailResponse.model_validate(s) for s in steps]


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run_endpoint(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Request cancellation of a running mission."""
    try:
        run = await cancel_run(db, run_id)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RunResponse.model_validate(run)


# ──────────────────────────────────────────────
# Phase 4+ placeholders (still 501)
# ──────────────────────────────────────────────


@router.get("/{run_id}/trace")
async def get_run_trace(run_id: str, request: Request):
    """Get the full trace tree for a run. Not yet implemented (Phase 4)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{run_id}/summary")
async def get_run_summary(run_id: str, request: Request):
    """Get a lightweight summary of a run. Not yet implemented (Phase 4)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{run_id}/evals")
async def get_run_evals(run_id: str, request: Request):
    """Get eval results for a run. Not yet implemented (Phase 5)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )
