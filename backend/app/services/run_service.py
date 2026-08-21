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
from app.db.models import Approval, Mission, MissionVersion, Run, RunStep, Span, Step
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
        span_type="system",
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
    # Also capture the run span id so step spans can reference it as
    # their parent — this is what makes the trace tree reconstruct
    # correctly with the run span at the root (Phase 4).
    run_span_id = None
    span_result = await db.execute(
        select(Span).where(Span.run_id == run_id, Span.kind == "run")
    )
    run_span = span_result.scalar_one_or_none()
    if run_span is not None:
        run_span_id = run_span.id
        if not input_context and run_span.input_json:
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
            await _finalize_run_span(db, run, "error", run.error_summary)
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
            await _finalize_run_span(db, run, "cancelled", "Cancelled by user")
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
            run_span_id=run_span_id,
        )

        if step_output.get("cancelled"):
            # Cancellation during step execution
            run.status = "cancelled"
            run.ended_at = datetime.now(timezone.utc)
            run.error_summary = "Cancelled during step execution"
            await _finalize_run_span(db, run, "cancelled", run.error_summary)
            await db.commit()
            return run

        if step_output.get("paused"):
            # Run paused at an approval gate — stop the loop and return.
            await db.refresh(run)
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
            await _finalize_run_span(db, run, "error", run.error_summary)
            await db.commit()
            logger.error("Run %s failed at step '%s': %s", run.id, step.name, step_output.get("error"))
            # Post-run hook: eval suite (Phase 5) — non-blocking
            await _post_run_eval_hook(run)
            return run

        await db.commit()

    # All steps completed
    run.status = "completed"
    run.ended_at = datetime.now(timezone.utc)
    run.current_step_id = None
    run.error_summary = None
    await _finalize_run_span(db, run, "ok")
    await db.commit()

    # Post-run hook: eval suite (Phase 5) — enqueues async eval job
    await _post_run_eval_hook(run)

    logger.info("Run %s completed successfully", run.id)
    return run


async def _execute_step_with_retry(
    db: AsyncSession,
    run: Run,
    step: Step,
    rs: RunStep,
    prior_outputs: Dict[str, Any],
    run_span_id: Optional[uuid.UUID] = None,
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

    # Create the step span ONCE per step so all retry attempts share the
    # same parent span. This is what groups the multiple LLM/tool spans
    # from retries under a single step node in the trace tree (Phase 4).
    step_start = datetime.now(timezone.utc)
    step_span = Span(
        run_id=run.id,
        step_id=step.id,
        parent_span_id=run_span_id,
        kind="step",
        span_type="system",
        name=f"step:{step.step_key}",
        status="ok",
        start_time=step_start,
        input_json={
            "step_key": step.step_key,
            "kind": step.kind,
            "order_index": step.order_index,
        },
    )
    db.add(step_span)
    await db.flush()
    step_span_id = step_span.id

    for attempt in range(max_retries + 1):
        # Check cancellation before each attempt
        await db.refresh(run)
        if run.cancel_requested:
            rs.status = "cancelled"
            rs.ended_at = datetime.now(timezone.utc)
            # Finalize the step span so the trace does not show an open "ok" span.
            step_span.status = "cancelled"
            step_span.end_time = datetime.now(timezone.utc)
            step_span.duration_ms = round(
                (step_span.end_time - step_start).total_seconds() * 1000, 2
            )
            step_span.error_json = {"error": "Cancelled during step execution"}
            step_span.meta_json = {"attempt": attempt + 1, "retryable": False}
            rs.span_id = step_span.id
            await db.flush()
            return {"cancelled": True}

        rs.attempt_count = attempt + 1
        await db.flush()

        try:
            if step.kind == "llm":
                output = await _execute_llm_step(
                    db, run, step, prior_outputs, timeout_seconds, step_span_id
                )
            elif step.kind == "tool":
                output = await _execute_tool_step(
                    db, run, step, prior_outputs, timeout_seconds, step_span_id
                )
            elif step.kind == "approval":
                output = await _execute_approval_step(
                    db=db, run=run, step=step, prior_outputs=prior_outputs
                )
                if output.get("paused"):
                    # Approval gate: pause the run, do NOT continue the loop.
                    # The run is now in 'awaiting_approval' status and will resume
                    # when a human decision is made via the API.
                    logger.info(
                        "Run %s paused at step '%s' awaiting approval",
                        run.id,
                        step.name,
                    )
                    # Finalize the step as completed with output
                    rs.status = "completed"
                    rs.output_json = prior_outputs.get(step.step_key)
                    rs.ended_at = datetime.now(timezone.utc)
                    await db.flush()
                    await db.commit()
                    return output
            else:
                error_msg = f"Unknown step kind: {step.kind}"
                # Finalize the step span as an error (previously left open).
                step_span.status = "error"
                step_span.end_time = datetime.now(timezone.utc)
                step_span.duration_ms = round(
                    (step_span.end_time - step_start).total_seconds() * 1000, 2
                )
                step_span.error_json = {"error": error_msg}
                step_span.meta_json = {"attempt": attempt + 1, "retryable": False}
                rs.span_id = step_span.id
                await db.flush()
                return {"ok": False, "error": error_msg}

            # Update the step span with output + model/token info.
            span_meta: Dict[str, Any] = {"attempt": attempt + 1}
            if step.kind == "llm" and isinstance(output, dict):
                if output.get("model"):
                    span_meta["model"] = output["model"]
                if output.get("tokens"):
                    span_meta["tokens"] = output["tokens"]
            step_span.status = "ok"
            step_span.end_time = datetime.now(timezone.utc)
            step_span.duration_ms = round((step_span.end_time - step_start).total_seconds() * 1000, 2)
            step_span.output_json = output
            step_span.meta_json = span_meta
            rs.span_id = step_span.id
            await db.flush()
            return {"ok": True, "output": output}

        except NonRetryableStepError as exc:
            # Non-retryable: fail immediately, record span, do NOT retry.
            rs.status = "failed"
            rs.error = str(exc)
            rs.ended_at = datetime.now(timezone.utc)
            step_span.status = "error"
            step_span.end_time = datetime.now(timezone.utc)
            step_span.error_json = {"error": str(exc)}
            step_span.meta_json = {"attempt": attempt + 1, "retryable": False}
            rs.span_id = step_span.id
            await db.flush()
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
                step_span.status = "error"
                step_span.end_time = datetime.now(timezone.utc)
                step_span.duration_ms = round((step_span.end_time - step_start).total_seconds() * 1000, 2)
                step_span.error_json = {"error": str(exc)}
                step_span.meta_json = {"attempt": attempt + 1}
                rs.span_id = step_span.id
                await db.flush()
                return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": "Unknown execution error"}


async def _finalize_run_span(
    db: AsyncSession,
    run: Run,
    status: str,
    error: Optional[str] = None,
) -> None:
    """
    Finalize the run-level span with an end time and an outcome status.

    The run span is created in create_run with status='ok' and no end_time.
    This mirrors the run's real outcome so the trace tree root reflects it
    (completed → ok, failed/timed_out → error, cancelled → cancelled).
    """
    run_span_result = await db.execute(
        select(Span).where(Span.run_id == run.id, Span.kind == "run")
    )
    run_span = run_span_result.scalar_one_or_none()
    if run_span is None:
        return

    run_span.status = status
    end = run.ended_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    run_span.end_time = end
    if run_span.start_time:
        start = run_span.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        run_span.duration_ms = round((end - start).total_seconds() * 1000, 2)
    if error:
        run_span.error_json = {"error": error}
    await db.flush()


async def _execute_llm_step(
    db: AsyncSession,
    run: Run,
    step: Step,
    prior_outputs: Dict[str, Any],
    timeout_seconds: int,
    step_span_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Execute an LLM step by rendering the prompt template with
    prior step outputs and calling the LLM.

    Records an LLM span (kind="llm") as a child of the step span,
    with model + token counts in meta_json for observability.
    """
    prompt_template = step.prompt_template or "You are executing step: {step_name}"
    rendered = _render_template(prompt_template, step, prior_outputs)

    start = datetime.now(timezone.utc)
    try:
        result = await llm_service.call_llm(
            prompt=rendered,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        # Record a failed LLM span
        end = datetime.now(timezone.utc)
        llm_span = Span(
            run_id=run.id,
            step_id=step.id,
            parent_span_id=step_span_id,
            kind="llm",
            span_type="llm_step",
            name=f"llm:{step.step_key}",
            status="error",
            start_time=start,
            end_time=end,
            duration_ms=round((end - start).total_seconds() * 1000, 2),
            input_json={"prompt": rendered[:2000]},
            error_json={"error": str(exc)},
        )
        db.add(llm_span)
        await db.flush()
        raise

    # Record a successful LLM span with model + token info
    end = datetime.now(timezone.utc)
    llm_span = Span(
        run_id=run.id,
        step_id=step.id,
        parent_span_id=step_span_id,
        kind="llm",
        span_type="llm_step",
        name=f"llm:{step.step_key}",
        status="ok",
        start_time=start,
        end_time=end,
        duration_ms=round((end - start).total_seconds() * 1000, 2),
        model=result["model"],
        tokens_in=result["prompt_tokens"],
        tokens_out=result["completion_tokens"],
        input_json={"prompt": rendered[:2000]},
        output_json={"text": result["text"][:2000]},
        meta_json={
            "model": result["model"],
            "tokens": {
                "prompt": result["prompt_tokens"],
                "completion": result["completion_tokens"],
                "total": result["total_tokens"],
            },
        },
    )
    db.add(llm_span)
    await db.flush()

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
    db: AsyncSession,
    run: Run,
    step: Step,
    prior_outputs: Dict[str, Any],
    timeout_seconds: int,
    step_span_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Execute a tool step (Phase 3 — Tool Sandbox).

    Executes ALL tool_refs in listed order. Each tool call:
      - Is dispatched through the ToolRegistry (validated, timed, truncated)
      - Records a tool span (kind="tool") as a child of the step span
      - Appends its output as a labeled TOOL_RESULT block

    Returns a dict with:
      - tool_results: list of per-tool results
      - tool_context: concatenated TOOL_RESULT blocks (for LLM summarization)
      - text: the tool_context (so prior steps can reference it)
    """
    tool_refs = step.tool_refs or []
    if not tool_refs:
        raise NonRetryableStepError("Tool step has no tool_refs configured")

    tool_results = []
    tool_context_parts = []

    for ref in tool_refs:
        # tool_refs entries may be plain strings or dicts with
        # {tool_name, input}. Extract tool name + input.
        if isinstance(ref, dict):
            tool_name = ref.get("tool_name") or ref.get("name") or "unknown"
            tool_input = ref.get("input") or ref.get("args") or {}
        else:
            tool_name = ref
            tool_input = {}

        # Render tool input templates with prior outputs
        tool_input = _render_tool_input(tool_input, step, prior_outputs)

        start = datetime.now(timezone.utc)
        result = await tool_service.execute_tool(
            tool_name,
            tool_input,
            timeout_seconds=timeout_seconds,
        )
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        # Record a tool span (child of the step span)
        # input_hash: stable fingerprint of the tool input (for Phase 4
        #   trace diffing — finding identical tool calls across runs).
        # output_summary: truncated preview of the output for quick
        #   inspection without loading the full payload.
        tool_input_hash = _tool_input_hash(tool_name, tool_input)
        output_summary = _tool_output_summary(result.data if result.ok else None)
        tool_span = Span(
            run_id=run.id,
            step_id=step.id,
            parent_span_id=step_span_id,
            kind="tool",
            span_type="tool",
            name=f"tool:{tool_name}",
            status="ok" if result.ok else "error",
            start_time=start,
            end_time=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            input_json={"tool_name": tool_name, "input": tool_input},
            output_json=result.data if result.ok else None,
            error_json={"error": result.error, "message": result.message} if not result.ok else None,
            meta_json={
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "truncated": bool((result.metadata or {}).get("truncated")),
                "empty_result": bool((result.metadata or {}).get("empty_result")),
                "input_hash": tool_input_hash,
                "output_summary": output_summary,
            },
        )
        db.add(tool_span)
        await db.flush()

        tool_results.append(
            {
                "tool_name": tool_name,
                "ok": result.ok,
                "data": result.data,
                "error": result.error,
                "message": result.message,
                "duration_ms": duration_ms,
            }
        )

        # Wrap tool output in a labeled TOOL_RESULT block (prompt-injection hygiene)
        tool_context_parts.append(
            tool_service.wrap_tool_output(tool_name, result)
        )

        # If a tool fails, the step fails (non-retryable for sandbox errors)
        if not result.ok:
            raise NonRetryableStepError(
                f"Tool '{tool_name}' failed: {result.message or result.error}"
            )

    tool_context = "\n\n".join(tool_context_parts)

    return {
        "tool_results": tool_results,
        "tool_context": tool_context,
        "text": tool_context,
    }


def _render_tool_input(
    tool_input: Any,
    step: Step,
    prior_outputs: Dict[str, Any],
) -> Any:
    """
    Render string values in tool input with prior step outputs.

    Supports {{prior.<step_key>}} and {{input}} placeholders in
    string fields of the tool input dict.
    """
    if isinstance(tool_input, dict):
        return {
            k: _render_tool_input(v, step, prior_outputs)
            for k, v in tool_input.items()
        }
    if isinstance(tool_input, list):
        return [_render_tool_input(v, step, prior_outputs) for v in tool_input]
    if isinstance(tool_input, str):
        rendered = tool_input
        for key, value in prior_outputs.items():
            if isinstance(value, dict):
                text = value.get("text", str(value))
            else:
                text = str(value)
            if len(text) > settings.MAX_CONTEXT_CHARS:
                text = text[: settings.MAX_CONTEXT_CHARS] + "\n...[truncated]"
            rendered = rendered.replace(f"{{{{prior.{key}}}}}", text)
        if "input" in prior_outputs:
            input_text = str(prior_outputs["input"])
            if len(input_text) > settings.MAX_CONTEXT_CHARS:
                input_text = input_text[: settings.MAX_CONTEXT_CHARS] + "\n...[truncated]"
            rendered = rendered.replace("{{input}}", input_text)
        return rendered
    return tool_input


def _tool_input_hash(tool_name: str, tool_input: Any) -> str:
    """
    Compute a stable fingerprint of a tool call's input.

    Used for Phase 4 trace diffing — finding identical tool calls
    across runs. Uses SHA-256 over the JSON-serialized input so the
    hash is deterministic and collision-resistant.
    """
    import hashlib
    import json

    try:
        serialized = json.dumps(
            {"tool": tool_name, "input": tool_input},
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        serialized = f"{tool_name}:{str(tool_input)}"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _tool_output_summary(data: Any, max_chars: int = 200) -> str:
    """
    Build a short preview of a tool's output for trace inspection.

    Truncates the JSON-serialized output to `max_chars` so the summary
    is cheap to store and quick to scan in the Phase 4 dashboard.
    """
    import json

    if data is None:
        return ""
    try:
        serialized = json.dumps(data, default=str)
    except (TypeError, ValueError):
        serialized = str(data)
    if len(serialized) <= max_chars:
        return serialized
    return serialized[:max_chars] + "…[truncated]"


async def _execute_approval_step(
    db: AsyncSession,
    run: Run,
    step: Step,
    prior_outputs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute an approval step.

    Phase 6 implementation:
    - Records the step output
    - Creates an approval record with status='pending'
    - Captures full context at pause time (step goal, mission name, prior output summary)
    - Sets run.status = 'awaiting_approval'
    - Breaks the execution loop — the run is now paused
    - Awaits a human decision via the API

    Returns a dict indicating the run has paused for approval.
    The caller should not continue the step loop.
    """
    now = datetime.now(timezone.utc)

    # Build context snapshot at approval request time
    mission = await db.get(Mission, run.mission_id)
    mission_name = mission.name if mission else None

    # Get prior step output summary (first output key as preview)
    prior_output = prior_outputs.get(step.step_key)
    prior_summary = None
    if isinstance(prior_output, dict):
        # Use the first text-like value as a summary, or the first key
        for k, v in prior_output.items():
            if isinstance(v, str) and len(v) > 0:
                prior_summary = v[:200]  # truncate for context
                break
        if prior_summary is None:
            # Fallback: serialize first value
            first_val = next(iter(prior_output.values()), None)
            if first_val:
                prior_summary = str(first_val)[:200]

    # Build context JSON for the approval record
    context_json = {
        "step_goal": step.prompt_template or "",
        "mission_name": mission_name,
        "step_key": step.step_key,
        "step_name": step.name,
        "prior_output_summary": prior_summary,
        "prior_output_keys": list(prior_output.keys()) if isinstance(prior_output, dict) else None,
    }

    # Record the step output in run_steps
    result = await db.execute(
        select(RunStep).where(RunStep.run_id == run.id, RunStep.step_id == step.id)
    )
    rs = result.scalar_one()
    rs.status = "completed"
    rs.output_json = prior_outputs.get(step.step_key)
    rs.ended_at = now
    await db.flush()

    # Create approval record (pending) with full context
    approval = Approval(
        run_id=run.id,
        step_id=step.id,
        status="pending",
        requested_at=now,
        timeout_at=now + timedelta(seconds=step.timeout_seconds or 3600),
        timeout_seconds=step.timeout_seconds or 3600,
        context_json=context_json,
        original_output=prior_outputs.get(step.step_key),
    )
    db.add(approval)
    await db.flush()

    # Set run status to awaiting_approval and stop the execution loop
    run.status = "awaiting_approval"
    run.ended_at = None  # Reset — run is not terminal, just paused
    await db.flush()

    # Create approval_requested span
    approval_span = Span(
        run_id=run.id,
        step_id=step.id,
        parent_span_id=None,
        kind="approval",
        span_type="approval",
        name=f"approval_requested:{step.step_key}",
        status="ok",
        start_time=now,
        end_time=now,
        duration_ms=0,
        input_json={"step_key": step.step_key, "step_name": step.name},
        meta_json={
            "approval_id": str(approval.id),
            "timeout_seconds": step.timeout_seconds or 3600,
            "step_key": step.step_key,
        },
    )
    db.add(approval_span)
    await db.flush()

    logger.info(
        "Run %s paused at step '%s' awaiting approval (approval %s, timeout in %ds, mission=%s)",
        run.id,
        step.name,
        approval.id,
        step.timeout_seconds or 3600,
        mission_name,
    )

    return {
        "paused": True,
        "approval_id": str(approval.id),
        "status": "pending",
        "timeout_at": approval.timeout_at.isoformat(),
        "message": f"Run paused at step '{step.name}' — awaiting human approval (mission: {mission_name})",
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
# Approval Scheduler
# ──────────────────────────────────────────────

async def timeout_pending_approvals(db: AsyncSession) -> int:
    """
    Scheduler job: find pending approvals past timeout_at and mark them timed_out.

    For each timed-out approval:
      - Mark the approval status as 'timed_out'
      - Mark the associated run as 'timed_out'
      - Create an approval_decided span with timed_out status
      - Log the event

    Returns the number of approvals timed out.
    """
    now = datetime.now(timezone.utc)

    # Find pending approvals where timeout_at is in the past
    result = await db.execute(
        select(Approval).where(
            Approval.status == "pending",
            Approval.timeout_at < now,
        )
    )
    timed_out_approvals = result.scalars().all()

    count = 0
    for approval in timed_out_approvals:
        count += 1
        approval.status = "timed_out"
        approval.decided_at = now
        approval.decided_by = "scheduler"
        approval.decision_notes = "Approval timed out due to inactivity"

        # Mark the associated run as timed_out
        run = await db.get(Run, approval.run_id)
        if run and run.status == "awaiting_approval":
            run.status = "timed_out"
            run.ended_at = now
            run.error_summary = (
                f"Run timed out — approval {approval.id} at step "
                f"'{approval.step.step_key}' exceeded timeout"
            )

            # Create approval_decided span with timed_out status
            approval_span = Span(
                run_id=run.id,
                step_id=approval.step_id,
                parent_span_id=None,
                kind="approval",
                span_type="approval",
                name=f"approval_decided:{approval.step.step_key}",
                status="error",
                start_time=now,
                end_time=now,
                duration_ms=0,
                input_json={"approval_id": str(approval.id), "decision": "timed_out"},
                meta_json={
                    "approval_id": str(approval.id),
                    "decision": "timed_out",
                    "decided_by": "scheduler",
                    "error": run.error_summary,
                },
            )
            db.add(approval_span)

        logger.warning(
            "Scheduler: approval %s timed out — run %s marked timed_out",
            approval.id,
            run.id if run else "unknown",
        )

    if timed_out_approvals:
        await db.commit()

    return count


async def _fire_webhook(webhook_url: str, payload: Dict[str, Any]) -> None:
    """
    Fire an HTTP POST webhook with the given payload.
    Used to notify external systems on approval status changes.
    Uses a short timeout and swallows errors — webhook failures must never
    block the approval decision or run lifecycle.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            await client.post(webhook_url, json=payload, follow_redirects=True)
    except Exception as exc:
        logger.warning("Webhook %s failed: %s", webhook_url, exc)


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
        await _finalize_run_span(db, run, "error", run.error_summary)
        logger.warning("Watchdog reaped stale run %s", run.id)

    if stale_runs:
        await db.commit()

    return len(stale_runs)


async def purge_old_spans(db: AsyncSession, retention_days: Optional[int] = None) -> int:
    """
    Trace retention policy (Phase 4).

    Deletes spans (and their parent spans) older than TRACE_RETENTION_DAYS,
    preventing unbounded trace growth. Returns the number of spans deleted.

    Runs are kept (they are the source of truth for run lifecycle), but their
    trace data is pruned. This is a soft-delete-friendly approach: we only
    remove spans, not runs or run_steps.
    """
    from sqlalchemy import CursorResult, delete
    from typing import cast

    retention_days = retention_days or settings.TRACE_RETENTION_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # Delete spans older than the cutoff (created_at is the safest bound —
    # start_time may be missing on some spans).
    result = await db.execute(
        delete(Span).where(Span.created_at < cutoff)
    )
    await db.commit()
    deleted = cast("CursorResult", result).rowcount or 0

    if deleted:
        logger.info(
            "Trace retention: deleted %d spans older than %d days",
            deleted,
            retention_days,
        )
    return deleted


# ──────────────────────────────────────────────
# Post-run hooks (placeholders)
# ──────────────────────────────────────────────

async def _post_run_eval_hook(run: Run) -> None:
    """
    Post-run hook: enqueue automated evals for a terminal run (Phase 5).

    Non-blocking by design — evals never block execution. When Redis is
    configured (worker mode) the eval job is enqueued for async processing;
    in dev/tests without Redis this is a no-op.
    """
    if not settings.REDIS_URL:
        logger.debug("Redis not configured; skipping eval enqueue for run %s", run.id)
        return
    try:
        from app.services import worker

        worker.enqueue_eval_job(run.id)
    except Exception as exc:
        # Eval failures must never fail the run.
        logger.warning("Failed to enqueue eval job for run %s: %s", run.id, exc)


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


async def get_failing_steps(
    db: AsyncSession,
    step_id: Optional[uuid.UUID] = None,
    min_failures: int = 1,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Cross-run step failure query (Phase 4).

    Returns steps that have failed across multiple runs, grouped by step_id,
    ordered by failure count descending. Optionally filtered to a single step.

    Each result includes:
      - step_id: the step UUID
      - step_key: the step's key (from the related Step)
      - step_name: the step's display name
      - failure_count: number of distinct runs where this step failed
      - total_attempts: total attempts across all runs (attempt_count sum)
      - max_attempt_count: the highest attempt_count in a single run
      - last_error: the most recent error message
      - last_failed_at: timestamp of the most recent failure
    """
    from sqlalchemy import func

    # Base query: run_steps with status='failed', grouped by step_id
    query = (
        select(
            RunStep.step_id,
            func.count(func.distinct(RunStep.run_id)).label("failure_count"),
            func.sum(RunStep.attempt_count).label("total_attempts"),
            func.max(RunStep.attempt_count).label("max_attempt_count"),
            func.max(RunStep.ended_at).label("last_failed_at"),
        )
        .where(RunStep.status == "failed")
        .group_by(RunStep.step_id)
        .having(func.count(func.distinct(RunStep.run_id)) >= min_failures)
        .order_by(func.count(func.distinct(RunStep.run_id)).desc())
        .limit(limit)
    )

    if step_id is not None:
        query = query.where(RunStep.step_id == step_id)

    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return []

    # Load step details for the matching step_ids
    step_ids = [r.step_id for r in rows]
    step_result = await db.execute(
        select(Step).where(Step.id.in_(step_ids))
    )
    steps_by_id = {s.id: s for s in step_result.scalars().all()}

    # Load the most recent error per failing step in a single query (avoids
    # the per-step N+1). Rows are ordered by ended_at desc, so the first row
    # seen per step holds the latest failure's error message.
    latest_errors: Dict[uuid.UUID, Optional[str]] = {}
    err_result = await db.execute(
        select(RunStep.step_id, RunStep.error)
        .where(
            RunStep.status == "failed",
            RunStep.step_id.in_(step_ids),
        )
        .order_by(RunStep.ended_at.desc())
    )
    for err_row in err_result.all():
        if err_row.step_id not in latest_errors:
            latest_errors[err_row.step_id] = err_row.error

    failing_steps: List[Dict[str, Any]] = []
    for row in rows:
        step = steps_by_id.get(row.step_id)
        last_error = latest_errors.get(row.step_id)

        failing_steps.append(
            {
                "step_id": str(row.step_id),
                "step_key": step.step_key if step else None,
                "step_name": step.name if step else None,
                "failure_count": row.failure_count,
                "total_attempts": row.total_attempts or 0,
                "max_attempt_count": row.max_attempt_count or 0,
                "last_error": last_error,
                "last_failed_at": row.last_failed_at.isoformat() if row.last_failed_at else None,
            }
        )

    return failing_steps


async def get_failing_runs(
    db: AsyncSession,
    step_id: uuid.UUID,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    List the runs where a specific step failed, newest first (Phase 4).

    Each run records at most one failure per step (a failing step ends the
    run), so every returned row is a distinct run. This complements the
    aggregate get_failing_steps query with the concrete list of run IDs.
    """
    result = await db.execute(
        select(RunStep.run_id, RunStep.ended_at, RunStep.error)
        .where(RunStep.step_id == step_id, RunStep.status == "failed")
        .order_by(RunStep.ended_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        {
            "run_id": str(row.run_id),
            "failed_at": row.ended_at.isoformat() if row.ended_at else None,
            "error": row.error,
        }
        for row in rows
    ]
