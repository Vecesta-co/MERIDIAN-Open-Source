"""
MERIDIAN Runs API — v1.

Phase 2 — Agent Runtime. Implements run lifecycle endpoints:
  - POST /runs                 : create a run from a published mission and enqueue it
  - GET  /runs/{run_id}        : get run detail (with steps + spans)
  - GET  /runs/{run_id}/steps  : get run steps
  - POST /runs/{run_id}/cancel : request cancellation

Phase 4 — Trace Engine:
  - GET  /runs/{run_id}/trace   : nested trace tree
  - GET  /runs/{run_id}/summary : aggregated run summary (tokens, cost, errors)
  - GET  /runs/{run_id}/spans   : flat span list, filterable by type

Phase 5 — Eval Suite:
  - GET  /runs/{run_id}/evals       : eval results for a run
  - POST /runs/{run_id}/evals/run   : manually re-run attached evals
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.config import settings
from app.db.session import get_db_session
from app.models.schemas import (
    EvalResultResponse,
    EvalRunResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunResponse,
    RunStepDetailResponse,
    RunSummaryResponse,
    SpanNode,
)
from app.services import eval_service
from app.services.run_service import (
    RunValidationError,
    cancel_run,
    create_run,
    get_run_detail,
    get_run_steps,
)
from app.services.trace_service import (
    get_run_spans as _get_run_spans,
    get_run_summary as _get_run_summary,
    get_run_trace_tree as _get_run_trace_tree,
)
from app.services.worker import enqueue_run

logger = get_logger(__name__)


async def _execute_run_in_process(run_id: UUID) -> None:
    """Execute a run directly in the API process (fallback when Redis is down)."""
    try:
        from app.db.session import async_session_factory
        from app.services.run_service import execute_run

        async with async_session_factory() as db:
            await execute_run(db, run_id)
        logger.info("In-process execution finished for run %s", run_id)
    except Exception as exc:
        logger.error("In-process execution failed for run %s: %s", run_id, exc)

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


@router.get("/failing-steps")
async def list_failing_steps(
    request: Request,
    step_id: Optional[str] = Query(
        None, description="Filter results to a single step by UUID"
    ),
    min_failures: int = Query(
        1, ge=1, description="Minimum number of distinct runs where the step must have failed"
    ),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of steps to return"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Cross-run step failure query (Phase 4).

    Returns steps that have failed across multiple runs, ordered by failure
    count descending. Useful for identifying unstable steps in a mission.
    """
    from app.services.run_service import get_failing_steps

    step_uuid = None
    if step_id:
        try:
            step_uuid = UUID(step_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid step_id: {step_id}")

    failing_steps = await get_failing_steps(
        db,
        step_id=step_uuid,
        min_failures=min_failures,
        limit=limit,
    )
    return JSONResponse(content=failing_steps)


@router.get("/failing-steps/{step_id}/runs")
async def list_failing_runs(
    step_id: str,
    request: Request,
    limit: int = Query(
        50, ge=1, le=200, description="Maximum number of runs to return"
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List the runs where a specific step failed, newest first (Phase 4).

    Each run records at most one failure per step (a failing step ends the
    run), so each returned row is a distinct run. Complements the aggregate
    GET /runs/failing-steps query with the concrete run IDs.
    """
    from app.services.run_service import get_failing_runs

    try:
        step_uuid = UUID(step_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid step_id: {step_id}")

    failing_runs = await get_failing_runs(db, step_uuid, limit=limit)
    return JSONResponse(content=failing_runs)


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
    # the run stays pending and will be picked up by the reaper, or is
    # executed in-process when EXECUTE_RUNS_IN_PROCESS is enabled).
    try:
        enqueue_run(run.id)
    except Exception as exc:
        logger.warning("Run %s created but failed to enqueue: %s", run.id, exc)
        if settings.EXECUTE_RUNS_IN_PROCESS:
            import asyncio

            asyncio.create_task(_execute_run_in_process(run.id))

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
# Phase 4 — Trace Engine
# ──────────────────────────────────────────────


@router.get("/{run_id}/trace", response_model=SpanNode)
async def get_run_trace(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Get the full nested trace tree for a run."""
    try:
        tree = await _get_run_trace_tree(db, run_id)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return tree


@router.get("/{run_id}/summary", response_model=RunSummaryResponse)
async def get_run_summary(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Get a lightweight summary of a run (duration, tokens, cost, errors)."""
    try:
        summary = await _get_run_summary(db, run_id)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RunSummaryResponse.model_validate(summary)


@router.get("/{run_id}/spans")
async def get_run_spans(
    run_id: UUID,
    request: Request,
    type: Optional[str] = Query(None, description="Filter spans by type (e.g. llm_step, system, tool)"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a flat list of spans for a run, optionally filtered by type."""
    try:
        spans = await _get_run_spans(db, run_id, span_type=type)
    except RunValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return JSONResponse(content=spans)


@router.get("/{run_id}/evals", response_model=list[EvalResultResponse])
async def get_run_evals(
    run_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """Get all eval results for a run, newest first."""
    try:
        return await eval_service.get_run_eval_results(db, run_id)
    except eval_service.EvalValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{run_id}/evals/run", response_model=EvalRunResponse)
async def run_run_evals(
    run_id: UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Manually re-run all attached evals against a run.

    The run must already be in a terminal state (completed/failed/
    cancelled/timed_out). Re-running appends new result rows — it never
    re-runs the mission and never blocks execution.
    """
    try:
        return await eval_service.rerun_evals_for_run(db, run_id)
    except eval_service.EvalValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
