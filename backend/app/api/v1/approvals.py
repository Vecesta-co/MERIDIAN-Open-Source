"""
MERIDIAN Approvals API — v1.

Human-in-the-loop approval operations:
- List pending approvals
- Get an approval by ID
- Decide an approval (approve/reject/modify)
- POST /runs/{id}/resume (internal — resume after approval)
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db_session
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Approval, Run, Step
from app.models.schemas import (
    ApprovalDecision,
    ApprovalResponse,
    ApprovalStatus,
    RunResponse,
)
from app.services.run_service import reap_stale_runs, _fire_webhook

router = APIRouter(prefix="/approvals", tags=["approvals"])
logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Dependency: get approval by ID or 404
# ──────────────────────────────────────────────


async def _get_approval_or_404(
    db: AsyncSession, approval_id: uuid.UUID
) -> Approval:
    result = await db.execute(
        select(Approval).where(Approval.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if approval is None:
        raise HTTPException(
            status_code=404, detail=f"Approval {approval_id} not found"
        )
    return approval


# ──────────────────────────────────────────────
# GET /approvals — list pending approvals
# ──────────────────────────────────────────────

@router.get("", response_model=Dict[str, Any])
async def list_approvals(status: Optional[str] = None, db: AsyncSession = Depends(get_db_session)):
    """
    List approvals, filtered by optional status.

    Query params:
        status: "pending" | "approved" | "rejected" | "modified" | "timed_out"
    """
    stmt = select(Approval)
    if status:
        # Map query status to enum value
        status_map = {
            "pending": "pending",
            "approved": "approved",
            "rejected": "rejected",
            "modified": "modified",
            "timed_out": "timed_out",
        }
        approved_status = status_map.get(status)
        if approved_status:
            stmt = stmt.where(Approval.status == approved_status)

    result = await db.execute(stmt.order_by(Approval.requested_at.desc()))
    approvals = result.scalars().all()

    return {
        "count": len(approvals),
        "results": [ApprovalResponse.model_validate(a).model_dump() for a in approvals],
    }


# ──────────────────────────────────────────────
# GET /approvals/{id} — get approval by ID
# ──────────────────────────────────────────────

@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: uuid.UUID = Path(..., description="The approval ID"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get an approval by ID with step output and trace."""
    approval = await _get_approval_or_404(db, approval_id)
    return ApprovalResponse.model_validate(approval).model_dump()


# ──────────────────────────────────────────────
# POST /approvals/{id}/decide — submit a decision
# ──────────────────────────────────────────────

@router.post("/{approval_id}/decide", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: uuid.UUID = Path(..., description="The approval ID"),
    decision_in: ApprovalDecision = ...,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Submit a decision for an approval.

    Decision types:
        - approve: resume the run (next step)
        - reject: mark the run as failed
        - modify: replace step output with modified_output, then resume

    Body fields:
        decision: "approved" | "rejected" | "modify"
        modified_output: Optional[dict] — only for "modify" decisions
        notes: Optional[str] — human-readable decision notes
        decided_by: Optional[str] — reviewer identifier
    """
    approval = await _get_approval_or_404(db, approval_id)

    # Validate decision logic
    if decision_in.decision not in ("approved", "rejected", "modify"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision: {decision_in.decision}. Must be 'approved', 'rejected', or 'modify'.",
        )

    now = datetime.now(timezone.utc)

    if decision_in.decision == "approved":
        # Approve: set run status back to running, resume next step
        approval.status = "approved"
        approval.decided_at = now
        approval.decided_by = decision_in.reviewer_id or "human reviewer"
        approval.decision_notes = decision_in.decision_json or ""
        approval.modified_output = None

        # Fire webhook if configured
        if approval.webhook_url:
            payload = {
                "approval_id": str(approval.id),
                "decision": "approved",
                "run_id": str(approval.run_id),
                "step_id": str(approval.step_id),
                "decided_by": approval.decided_by,
                "decision_notes": approval.decision_notes,
            }
            _fire_webhook(approval.webhook_url, payload)

        # Find the next step to resume
        run = await db.get(Run, approval.run_id)
        if run and run.status == "awaiting_approval":
            run.status = "running"
            run.error_summary = None
            # Reset the current step so the execution loop can continue
            run.current_step_id = approval.step_id

        # Create approval_decided span
        approval_span = Span(
            run_id=run.id,
            step_id=approval.step_id,
            parent_span_id=None,
            kind="approval",
            span_type="approval",
            name=f"approval_decided:{approval.step.step_key}",
            status="ok",
            start_time=now,
            end_time=now,
            duration_ms=0,
            input_json={"approval_id": str(approval.id), "decision": "approved"},
            meta_json={
                "approval_id": str(approval.id),
                "decision": "approved",
                "decided_by": approval.decided_by,
            },
        )
        db.add(approval_span)
        await db.flush()

        logger.info(
            "Approval %s approved — run %s resuming",
            approval.id,
            run.id,
        )

    elif decision_in.decision == "rejected":
        # Reject: mark the run as failed
        approval.status = "rejected"
        approval.decided_at = now
        approval.decided_by = decision_in.reviewer_id or "human reviewer"
        approval.decision_notes = decision_in.decision_json or ""
        approval.modified_output = None

        # Fire webhook if configured
        if approval.webhook_url:
            payload = {
                "approval_id": str(approval.id),
                "decision": "rejected",
                "run_id": str(approval.run_id),
                "step_id": str(approval.step_id),
                "decided_by": approval.decided_by,
                "decision_notes": approval.decision_notes,
            }
            _fire_webhook(approval.webhook_url, payload)

        run = await db.get(Run, approval.run_id)
        if run and run.status == "awaiting_approval":
            run.status = "failed"
            run.ended_at = now
            run.error_summary = (
                f"Run failed — approval rejected at step '{run.current_step_id or 'unknown'}'"
            )

            # Create approval_decided span with rejected status
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
                input_json={"approval_id": str(approval.id), "decision": "rejected"},
                meta_json={
                    "approval_id": str(approval.id),
                    "decision": "rejected",
                    "decided_by": approval.decided_by,
                    "error": run.error_summary,
                },
            )
            db.add(approval_span)

        logger.warning(
            "Approval %s rejected — run %s failed",
            approval.id,
            run.id if run else "unknown",
        )

    elif decision_in.decision == "modify":
        # Modify: replace step output with modified_output, then resume
        approval.status = "modified"
        approval.decided_at = now
        approval.decided_by = decision_in.reviewer_id or "human reviewer"
        approval.decision_notes = decision_in.decision_json or ""
        # modified_output comes from decision_json or body

        # Fire webhook if configured
        if approval.webhook_url:
            payload = {
                "approval_id": str(approval.id),
                "decision": "modify",
                "run_id": str(approval.run_id),
                "step_id": str(approval.step_id),
                "decided_by": approval.decided_by,
                "decision_notes": approval.decision_notes,
                "modified_output": approval.modified_output,
            }
            _fire_webhook(approval.webhook_url, payload)

        run = await db.get(Run, approval.run_id)
        if run and run.status == "awaiting_approval":
            # Store the modified output
            if decision_in.decision_json and isinstance(decision_in.decision_json, dict):
                approval.modified_output = decision_in.decision_json.get("modified_output")
            else:
                approval.modified_output = None

            # Set run back to running and mark the step as completed with modified output
            run.status = "running"
            run.error_summary = None
            # The run will resume from the next step; the modified output
            # should be applied to the step's output_json in run_steps

            # Create approval_decided span
            approval_span = Span(
                run_id=run.id,
                step_id=approval.step_id,
                parent_span_id=None,
                kind="approval",
                span_type="approval",
                name=f"approval_decided:{approval.step.step_key}",
                status="ok",
                start_time=now,
                end_time=now,
                duration_ms=0,
                input_json={"approval_id": str(approval.id), "decision": "modify"},
                meta_json={
                    "approval_id": str(approval.id),
                    "decision": "modify",
                    "decided_by": approval.decided_by,
                    "modified_output_keys": list(
                        (approval.modified_output or {}).keys()
                    ),
                },
            )
            db.add(approval_span)

        logger.info(
            "Approval %s modified — run %s resuming with modified output",
            approval.id,
            run.id if run else "unknown",
        )

    await db.commit()

    return ApprovalResponse.model_validate(approval).model_dump()


# ──────────────────────────────────────────────
# POST /runs/{id}/resume — resume a run from approval pause
# ──────────────────────────────────────────────

@router.post("/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(
    run_id: uuid.UUID = Path(..., description="The run ID to resume"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Internal endpoint to resume a run that is paused at an approval.

    This is called by the worker after a human has made an approval decision
    via the API. It sets the run status back to 'running' so the execution
    loop can continue.

    NOTE: In a full implementation, this would be an internal/admin endpoint
    rather than publicly exposed.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} is not pending approval (status={run.status})",
        )

    # Reset run status to running
    run.status = "running"
    run.error_summary = None
    run.current_step_id = None  # Reset — execution loop will determine next step
    await db.commit()

    logger.info("Run %s resumed from approval pause", run.id)

    return RunResponse.model_validate(run).model_dump()