"""
MERIDIAN Run Service — Phase 2 Agent Runtime.

Core execution engine:
  - Creates and manages runs (pending → running → completed/failed/cancelled)
  - Executes mission steps sequentially by order_index
  - Calls LLM via LiteLLM for llm-kind steps
  - Stubs tool steps (Tool Sandbox arrives in Phase 3)
  - Stubs approval steps (pausing hook only — Phase 6)
  - Records per-step spans (trace basics)
  - Enforces step timeouts and retry with exponential backoff
  - Handles cancellation requests between steps
  - Crash-safe: stale running runs are reaped by a watchdog

NOT IMPLEMENTED in Phase 2 (per scope):
  - Tool sandbox execution (stub only)
  - Approval gates (pausing hook placeholder only)
  - Eval suite (post-run hook placeholder only)
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Mission, MissionVersion, Run, RunStep, Span, Step
from app.services import llm_service, tool_service

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class RunValidationError(Exception):
    """Raised when a run cannot be created due to invalid input.

    Carries an HTTP status code so the API layer can map it directly
    to the correct response (400 for validation failures, 404 for
    missing resources).
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class RunExecutionError(Exception):
    """Raised when a run fails during execution."""


class NonRetryableStepError(Exception):
    """Raised when a step fails with a non-retryable error.

    Unlike transient errors (timeout, rate limit), this should NOT be
    retried. Examples: tool sandbox not implemented, unknown step kind.
    """


# ──────────────────────────────────────────────
# Run creation
# ──────────────────────────────────────────────

async def create_run(
    db: AsyncSession,
    mission_id: uuid.UUID,
    input_context: Optional[Dict[str, Any]] = None,
) -> Run:
    """
    Create a new run for a published mission.

    Validates that the mission exists and is published.
    Creates a 'pending' run and returns it.
    The actual enqueue happens in the API layer (worker.py).

    Raises:
        RunValidationError: If mission not found or not published.
    """
    # Load mission
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise RunValidationError(f"Mission {mission_id} not found", status_code=404)

    if mission.state != "published":
        raise RunValidationError(
            f"Mission '{mission.name}' is not published (state={mission.state}). "
            "Only published missions can be run."
        )

    # Get the latest published version
    result = await db.execute(
        select(MissionVersion)
        .where(MissionVersion.mission_id == mission_id)
        .order_by(MissionVersion.version_int.desc())
        .limit(1)
    )
    mission_version = result.scalar_one_or_none()
    if mission_version is None:
        raise RunValidationError(f"Mission '{mission.name}' has no versions")

    # Create the run
    run = Run(
        mission_id=mission_id,
        mission_version_id=mission_version.id,
        status="pending",
        cancel_requested=False,
        triggered_by="manual",
    )
    db.add(run)
    await db.flush()

    # Store input context in the run's spans (trace) — a run-level span
    now = datetime.now(timezone.utc)
    run_span = Span(
        run_id=run.id,
        kind="run",
        name=f"Run {run.id}",
        status="ok",
        start_time=now,
        input_json=input_context or {},
        meta_json={"mission_id": str(mission_id), "mission_name": mission.name},
    )
    db.add(run_span)
    await db.flush()

    logger.info(
        "Created run %s for mission '%s' (version %d)",
        run.id,
        mission.name,
        mission_version.version_int,
    )
    return run


# ──────────────────────────────────────────────
# Run execution (worker)
# ──────────────────────────────────────────────

async def execute_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    input_context: Optional[Dict[str, Any]] = None,
) -> Run:
    """
    Execute a run step by step.

    This is the core worker loop. It:
      1. Loads the run and its mission version + steps
      2. Marks the run as 'running'
      3. Executes each step (by order_index) with retry/backoff
      4. Stores per-step outputs and spans
      5. Marks the run as completed/failed/cancelled

    Handles cancellation between steps. Not crash-safe on its own —
    the watchdog (reap_stale_runs) handles worker crashes.
    """
    # Load run with mission version + steps
    result = await db.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.mission_version).selectinload(MissionVersion.steps),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise RunExecutionError(f"Run {run_id} not found")

    mission_version = run.mission_version
    steps = sorted(mission_version.steps, key=lambda s: s.order_index)

    # Load input context from the run's span if not provided directly.
    # This allows the worker to re-execute a run (e.g. after a crash)
    # without the caller having to remember the original input.
    if not input_context:
        span_result = await db.execute(
            select(Span).where(Span.run_id == run_id, Span.kind == "run")
        )
        run_span = span_result.scalar_one_or_none()
        if run_span is not None and run_span.input_json:
            input_context = run_span.input_json

    # Mark running
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.error_summary = None
    await db.flush()

    # Pre-create run_steps rows so clients can see all steps upfront
    run_step_map: Dict[uuid.UUID, RunStep] = {}
    for step in steps:
        rs = RunStep(
            run_id=run.id,
            step_id=step.id,
            status="pending",
            attempt_count=0,
        )
        db.add(rs)
        await db.flush()
        run_step_map[step.id] = rs

    await db.commit()

    # Execute steps
    prior_outputs: Dict[str, Any] = {}
    if input_context:
        prior_outputs["input"] = input_context

    # Global run timeout: sum of step timeouts + margin.
    # If the run exceeds this deadline, it is marked timed_out.
    total_timeout = sum((s.timeout_seconds or 300) for s in steps) + settings.RUN_TIMEOUT_MARGIN_SECONDS
    run_deadline = datetime.now(timezone.utc) + timedelta(seconds=total_timeout)

    for step in steps:
        # Check global run timeout
        if datetime.now(timezone.utc) >= run_deadline:
            run.status = "timed_out"
            run.ended_at = datetime.now(timezone.utc)
            run.error_summary = f"Run exceeded global timeout of {total_timeout}s"
            # Mark remaining pending/running steps as timed_out
            for rs in run_step_map.values():
                if rs.status in ("pending", "running"):
                    rs.status = "timed_out"
                    rs.ended_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning("Run %s timed out after %ds", run.id, total_timeout)
            return run

        # Check cancellation between steps
        await db.refresh(run)
        if run.cancel_requested:
            run.status = "cancelled"
            run.ended_at = datetime.now(timezone.utc)
            run.error_summary = "Cancelled by user"
            # Mark remaining pending steps as cancelled
            for rs in run_step_map.values():
                if rs.status == "pending":
                    rs.status = "cancelled"
            await db.commit()
            logger.info("Run %s cancelled between steps", run.id)
            return run

        # Update current step pointer
        run.current_step_id = step.id
        await db.flush()

        rs = run_step_map[step.id]
        rs.status = "running"
        rs.started_at = datetime.now(timezone.utc)
        await db.flush()

        # Execute step with retry
        step_output = await _execute_step_with_retry(
            db=db,
            run=run,
            step=step,
            rs=rs,
            prior_outputs=prior_outputs,
        )

        if step_output.get("cancelled"):
            # Cancellation during step execution
            run.status = "cancelled"
            run.ended_at = datetime.now(timezone.utc)
            run.error_summary = "Cancelled during step execution"
            await db.commit()
            return run

        if step_output.get("ok"):
            rs.status = "completed"
            rs.output_json = step_output.get("output")
            rs.ended_at = datetime.now(timezone.utc)
            prior_outputs[step.step_key] = step_output.get("output")
        else:
            rs.status = "failed"
            rs.error = step_output.get("error")
            rs.ended_at = datetime.now(timezone.utc)
            run.status = "failed"
            run.ended_at = datetime.now(timezone.utc)
            run.error_summary = step_output.get("error")
            await db.commit()
            logger.error("Run %s failed at step '%s': %s", run.id, step.name, step_output.get("error"))
            return run

        await db.commit()

    # All steps completed
    run.status = "completed"
    run.ended_at = datetime.now(timezone.utc)
    run.current_step_id = None
    run.error_summary = None
    await db.commit()

    # Post-run hook: eval suite (placeholder — Phase 5)
    await _post_run_eval_hook(run)

    logger.info("Run %s completed successfully", run.id)
    return run


async def _execute_step_with_retry(
    db: AsyncSession,
    run: Run,
    step: Step,
    rs: RunStep,
    prior_outputs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a single step with retry/backoff.

    Returns a dict:
        {"ok": True, "output": ...}   on success
        {"ok": False, "error": ...}   on non-retryable failure
        {"cancelled": True}           if cancelled during step
    """
    max_retries = step.max_retries or 0
    timeout_seconds = step.timeout_seconds or 300

    for attempt in range(max_retries + 1):
        # Check cancellation before each attempt
        await db.refresh(run)
        if run.cancel_requested:
            rs.status = "cancelled"
            rs.ended_at = datetime.now(timezone.utc)
            await db.flush()
            return {"cancelled": True}

        rs.attempt_count = attempt + 1
        await db.flush()

        try:
            if step.kind == "llm":
                output = await _execute_llm_step(step, prior_outputs, timeout_seconds)
            elif step.kind == "tool":
                output = await _execute_tool_step(step, prior_outputs, timeout_seconds)
            elif step.kind == "approval":
                output = await _execute_approval_step(step, prior_outputs, timeout_seconds)
            else:
                return {
                    "ok": False,
                    "error": f"Unknown step kind: {step.kind}",
                }

            # Record a span for this step. For LLM steps, promote
            # model + token counts into the span meta for observability.
            span_meta: Dict[str, Any] = {"attempt": attempt + 1}
            if step.kind == "llm" and isinstance(output, dict):
                if output.get("model"):
                    span_meta["model"] = output["model"]
                if output.get("tokens"):
                    span_meta["tokens"] = output["tokens"]
            await _record_step_span(
                db=db,
                run=run,
                step=step,
                rs=rs,
                success=True,
                output=output,
                meta=span_meta,
            )
            return {"ok": True, "output": output}

        except NonRetryableStepError as exc:
            # Non-retryable: fail immediately, record span, do NOT retry.
            rs.status = "failed"
            rs.error = str(exc)
            rs.ended_at = datetime.now(timezone.utc)
            await _record_step_span(
                db=db,
                run=run,
                step=step,
                rs=rs,
                success=False,
                error=str(exc),
                meta={"attempt": attempt + 1, "retryable": False},
            )
            return {"ok": False, "error": str(exc)}

        except (TimeoutError, RuntimeError) as exc:
            # Transient — retryable
            rs.attempt_count = attempt + 1
            await db.flush()
            if attempt < max_retries:
                backoff = 2 ** attempt  # exponential backoff: 1s, 2s, 4s...
                logger.warning(
                    "Step '%s' attempt %d failed (%s). Retrying in %ds...",
                    step.name,
                    attempt + 1,
                    str(exc),
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                # Final failure
                await _record_step_span(
                    db=db,
                    run=run,
                    step=step,
                    rs=rs,
                    success=False,
                    error=str(exc),
                    meta={"attempt": attempt + 1},
                )
                return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": "Unknown execution error"}


async def _execute_llm_step(
    step: Step,
    prior_outputs: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    """
    Execute an LLM step by rendering the prompt template with
    prior step outputs and calling the LLM.
    """
    prompt_template = step.prompt_template or "You are executing step: {step_name}"
    rendered = _render_template(prompt_template, step, prior_outputs)

    result = await llm_service.call_llm(
        prompt=rendered,
        timeout_seconds=timeout_seconds,
    )

    return {
        "text": result["text"],
        "model": result["model"],
        "tokens": {
            "prompt": result["prompt_tokens"],
            "completion": result["completion_tokens"],
            "total": result["total_tokens"],
        },
    }


async def _execute_tool_step(
    step: Step,
    prior_outputs: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    """
    Execute a tool step. STUB — Tool Sandbox not implemented in Phase 2.

    Raises NonRetryableStepError (not RuntimeError) so the retry loop
    does NOT waste retries on a guaranteed-persistent failure.

    Phase 3 will implement real tool dispatch: extract the tool input
    from tool_refs, look up the tool in the Tool registry, validate
    against its input_schema, and await the sandboxed result.
    """
    tool_refs = step.tool_refs or []
    if not tool_refs:
        raise NonRetryableStepError("Tool step has no tool_refs configured")

    # tool_refs entries may be plain strings or dicts with
    # {tool_name, input}. Extract the first tool name + input.
    first_ref = tool_refs[0]
    if isinstance(first_ref, dict):
        tool_name = first_ref.get("tool_name") or first_ref.get("name") or "unknown"
        tool_input = first_ref.get("input") or first_ref.get("args") or {}
    else:
        tool_name = first_ref
        tool_input = {}

    result = await tool_service.execute_tool(tool_name, tool_input, timeout_seconds)
    if not result.get("ok"):
        raise NonRetryableStepError(result.get("message", "Tool not implemented"))
    return result


async def _execute_approval_step(
    step: Step,
    prior_outputs: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    """
    Execute an approval step. STUB — Approval Gate not implemented in Phase 2.
    This is a placeholder hook that will be wired in Phase 6.
    For now, approval steps auto-pass (return the input as output) so the
    run can continue.
    """
    # TODO(Phase 6): Implement real approval gate — pause run, create Approval
    # record, wait for human decision.
    logger.warning(
        "Approval step '%s' is a placeholder — auto-passing. Approval Gate arrives in Phase 6.",
        step.name,
    )
    return {
        "approved": True,
        "note": "Approval Gate not implemented in Phase 2 — auto-approved",
        "step": step.name,
    }


async def _record_step_span(
    db: AsyncSession,
    run: Run,
    step: Step,
    rs: RunStep,
    success: bool,
    output: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a trace span for a step execution."""
    now = datetime.now(timezone.utc)
    span = Span(
        run_id=run.id,
        step_id=step.id,
        kind="step",
        name=f"step:{step.step_key}",
        status="ok" if success else "error",
        start_time=rs.started_at or now,
        end_time=now,
        input_json={
            "step_key": step.step_key,
            "kind": step.kind,
            "order_index": step.order_index,
        },
        output_json=output if success else None,
        error_json={"error": error} if error else None,
        meta_json=meta or {},
    )
    db.add(span)
    # Link the run_step to this span
    rs.span_id = span.id
    await db.flush()


def _render_template(
    template: str,
    step: Step,
    prior_outputs: Dict[str, Any],
) -> str:
    """
    Render a prompt template with prior step outputs.

    Simple placeholder substitution:
      - {{step_name}} → step.name
      - {{step_key}} → step.step_key
      - {{goal}} → mission goal (if available)
      - {{prior.<step_key>}} → prior step output text
      - {{input}} → input context
    """
    rendered = template
    rendered = rendered.replace("{{step_name}}", step.name or "")
    rendered = rendered.replace("{{step_key}}", step.step_key or "")

    # Substitute prior outputs (with truncation to prevent context overflow)
    for key, value in prior_outputs.items():
        if isinstance(value, dict):
            text = value.get("text", str(value))
        else:
            text = str(value)
        # Truncate oversized prior outputs to protect the LLM context window
        if len(text) > settings.MAX_CONTEXT_CHARS:
            text = text[: settings.MAX_CONTEXT_CHARS] + "\n...[truncated]"
        rendered = rendered.replace(f"{{{{prior.{key}}}}}", text)

    # Generic input substitution (also truncated)
    if "input" in prior_outputs:
        input_text = str(prior_outputs["input"])
        if len(input_text) > settings.MAX_CONTEXT_CHARS:
            input_text = input_text[: settings.MAX_CONTEXT_CHARS] + "\n...[truncated]"
        rendered = rendered.replace("{{input}}", input_text)

    return rendered


# ──────────────────────────────────────────────
# Cancellation
# ──────────────────────────────────────────────

async def cancel_run(db: AsyncSession, run_id: uuid.UUID) -> Run:
    """
    Request cancellation of a run. Sets cancel_requested=True.
    The worker checks this between steps and marks the run cancelled.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise RunValidationError(f"Run {run_id} not found", status_code=404)

    if run.status in ("completed", "failed", "cancelled", "timed_out"):
        raise RunValidationError(
            f"Run {run_id} is already in terminal state '{run.status}'"
        )

    run.cancel_requested = True
    # Commit so the flag is visible to the worker process (which may
    # use a separate DB session).
    await db.commit()
    logger.info("Cancellation requested for run %s", run.id)
    return run


# ──────────────────────────────────────────────
# Watchdog / crash recovery
# ──────────────────────────────────────────────

async def reap_stale_runs(db: AsyncSession) -> int:
    """
    Watchdog: find runs stuck in 'running' status for longer than
    STALE_RUN_THRESHOLD_MINUTES and mark them as 'failed' (crashed).

    This handles the scenario where a worker process crashes mid-run
    and never updates the run status. Returns the number of runs reaped.

    A real implementation would use a lock (e.g. Redis SETNX) to avoid
    double-reaping, but for Phase 2 a simple age-based check suffices.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=settings.STALE_RUN_THRESHOLD_MINUTES)

    result = await db.execute(
        select(Run).where(
            Run.status == "running",
            Run.started_at < threshold,
        )
    )
    stale_runs = result.scalars().all()

    for run in stale_runs:
        run.status = "failed"
        run.ended_at = datetime.now(timezone.utc)
        run.error_summary = (
            f"Run exceeded stale threshold ({settings.STALE_RUN_THRESHOLD_MINUTES} min) — "
            "worker likely crashed. Marked failed by watchdog."
        )
        logger.warning("Watchdog reaped stale run %s", run.id)

    if stale_runs:
        await db.commit()

    return len(stale_runs)


# ──────────────────────────────────────────────
# Post-run hooks (placeholders)
# ──────────────────────────────────────────────

async def _post_run_eval_hook(run: Run) -> None:
    """
    Post-run hook: eval suite placeholder.
    Phase 5 will wire automated evals here.
    """
    logger.info("Post-run eval hook placeholder (Phase 5) — run %s", run.id)


# ──────────────────────────────────────────────
# Query helpers
# ──────────────────────────────────────────────

async def list_runs(db: AsyncSession, limit: int = 100) -> List[Run]:
    """List the most recent runs, newest first."""
    result = await db.execute(
        select(Run)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_run_detail(db: AsyncSession, run_id: uuid.UUID) -> Run:
    """Get a run with its steps (run_steps) eager-loaded."""
    result = await db.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.run_steps).selectinload(RunStep.step),
            selectinload(Run.spans),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise RunValidationError(f"Run {run_id} not found", status_code=404)
    # Ensure run_steps are ordered by step order_index for stable responses.
    run.run_steps.sort(key=lambda rs: rs.step.order_index)
    return run


async def get_run_steps(db: AsyncSession, run_id: uuid.UUID) -> List[RunStep]:
    """Get all run_steps for a run, ordered by step order_index."""
    result = await db.execute(
        select(RunStep)
        .where(RunStep.run_id == run_id)
        .options(selectinload(RunStep.step))
        .order_by(RunStep.step_id)  # placeholder; sort in Python by order_index
    )
    steps = result.scalars().all()
    return sorted(steps, key=lambda rs: rs.step.order_index)
