"""
MERIDIAN Trace Service — Phase 4 Trace Engine.

Provides:
  - get_run_trace_tree(db, run_id)  → nested span tree (reconstructed from parent_span_id)
  - get_run_summary(db, run_id)     → duration, tokens, cost, errors, step success
  - get_run_spans(db, run_id, type) → flat filtered span list

Cost is computed on-request from the MODEL_PRICING table (not stored at write
time), so pricing changes don't require rewriting spans.
"""

import uuid
from collections import defaultdict
from datetime import timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Run, RunStep, Span

logger = get_logger(__name__)


def _to_utc(dt):
    """Ensure a datetime is timezone-aware (SQLite returns naive UTC values)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ──────────────────────────────────────────────
# Cost calculation
# ──────────────────────────────────────────────


def _calculate_cost(
    model: Optional[str],
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    meta_json: Optional[dict] = None,
) -> float:
    """
    Compute the cost of an LLM span in USD.

    Uses the MODEL_PRICING table, falling back to the "default" entry for
    unknown models. If no token counts are present, returns 0.0.

    Backward-compatible: reads tokens from `meta_json` if the dedicated
    columns are not populated (Phase 2/3 spans).
    """
    # Resolve token counts, preferring dedicated columns, falling back to meta_json
    if tokens_in is None and meta_json:
        tokens_in = meta_json.get("tokens", {}).get("prompt")
    if tokens_out is None and meta_json:
        tokens_out = meta_json.get("tokens", {}).get("completion")

    if tokens_in is None or tokens_out is None:
        return 0.0

    pricing = settings.MODEL_PRICING.get(
        model or "", settings.MODEL_PRICING.get("default", {})
    )
    input_rate = pricing.get("input_per_1k", 0.0)
    output_rate = pricing.get("output_per_1k", 0.0)

    return round(
        (tokens_in / 1000.0) * input_rate + (tokens_out / 1000.0) * output_rate,
        8,
    )


# ──────────────────────────────────────────────
# Span enrichment (adds computed fields)
# ──────────────────────────────────────────────


def _enrich_span(span: Span) -> Dict[str, Any]:
    """Convert a Span ORM object into a serializable dict with computed cost/duration."""
    meta = span.meta_json or {}
    duration_ms = span.duration_ms
    if duration_ms is None and span.start_time and span.end_time:
        duration_ms = round(
            (_to_utc(span.end_time) - _to_utc(span.start_time)).total_seconds() * 1000, 2
        )

    cost = span.cost_usd
    if cost is None:
        cost = _calculate_cost(span.model, span.tokens_in, span.tokens_out, meta)

    span_type = span.span_type or _map_kind_to_type(span.kind)

    # Severity defaults: error spans are "error", cancelled are "warning",
    # everything else is "info". Overridden by the stored span.severity if set.
    severity = span.severity
    if not severity:
        severity = "error" if span.status == "error" else ("warning" if span.status == "cancelled" else "info")

    return {
        "id": str(span.id),
        "run_id": str(span.run_id),
        "step_id": str(span.step_id) if span.step_id else None,
        "parent_span_id": str(span.parent_span_id) if span.parent_span_id else None,
        "span_type": span_type,
        "name": span.name,
        "status": span.status,
        "severity": severity,
        "started_at": span.start_time.isoformat() if span.start_time else None,
        "ended_at": span.end_time.isoformat() if span.end_time else None,
        "duration_ms": duration_ms,
        "model": span.model,
        "tokens_in": span.tokens_in,
        "tokens_out": span.tokens_out,
        "cost_usd": cost,
        "error_text": (span.error_json or {}).get("error") if span.error_json else None,
        "attributes": span.attributes or {},
        "children": [],
    }


def _map_kind_to_type(kind: str) -> str:
    """Map a legacy span `kind` to a Phase 4 `span_type`."""
    mapping = {
        "run": "system",
        "step": "system",
        "llm": "llm_step",
        "tool": "tool",
        "eval": "eval",
        "approval": "approval",
        "system": "system",
    }
    return mapping.get(kind, "system")


# ──────────────────────────────────────────────
# Trace tree reconstruction
# ──────────────────────────────────────────────


def _build_tree(spans: List[Span]) -> Dict[str, Any]:
    """
    Reconstruct a nested tree from a flat list of spans using parent_span_id.

    Orphan spans (no parent, or parent not in the set) attach to a synthetic
    root span. This handles backward compatibility with Phase 2/3 spans that
    lacked parent_span_id on step/run spans, and is forward-compatible with
    future DAG/parallel execution (multiple roots are merged under synthetic root).
    """
    if not spans:
        return {
            "id": str(uuid.uuid4()),
            "run_id": None,
            "step_id": None,
            "parent_span_id": None,
            "span_type": "system",
            "name": "root",
            "status": "ok",
            "started_at": None,
            "ended_at": None,
            "duration_ms": None,
            "model": None,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": 0.0,
            "error_text": None,
            "attributes": {},
            "children": [],
        }

    # Index spans by id and by parent
    by_id: Dict[str, Dict[str, Any]] = {}
    children_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    roots: List[Dict[str, Any]] = []

    for span in spans:
        node = _enrich_span(span)
        by_id[str(span.id)] = node

    for span in spans:
        node = by_id[str(span.id)]
        parent_id = str(span.parent_span_id) if span.parent_span_id else None
        if parent_id and parent_id in by_id:
            children_map[parent_id].append(node)
        else:
            # Orphan or root — attach to synthetic root
            roots.append(node)

    # Attach children to parents
    for parent_id, children in children_map.items():
        by_id[parent_id]["children"] = children

    # Cycle detection: a node may only appear once in the tree. Track the set
    # of visited span IDs during DFS. If a back-edge is detected (a node
    # already on the current path), we break the cycle by detaching that
    # child and re-attaching it as a root (orphan). This prevents infinite
    # recursion in `_sort_children` and ensures the tree is always a DAG.
    visited: set = set()

    def _sort_children(node):
        node_id = node["id"]
        if node_id in visited:
            # Cycle detected — this node is already reachable on the current
            # path. Detach it by removing it from its parent's children and
            # promote it to a root so it is still rendered.
            return
        visited.add(node_id)
        node["children"].sort(key=lambda c: c["started_at"] or "")
        retained = []
        for child in node["children"]:
            if child["id"] not in visited:
                _sort_children(child)
                retained.append(child)
            else:
                # Back-edge — move the cyclic child to roots as an orphan.
                roots.append(child)
        node["children"] = retained

    for root in roots:
        _sort_children(root)

    # If exactly one root, return it directly (common case: the run span)
    if len(roots) == 1:
        return roots[0]

    # Multiple roots → synthetic root span
    run_id = roots[0]["run_id"] if roots[0]["run_id"] else None
    start_values = [r["started_at"] for r in roots if r["started_at"]]
    end_values = [r["ended_at"] for r in roots if r["ended_at"]]
    synthetic: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "step_id": None,
        "parent_span_id": None,
        "span_type": "system",
        "name": "root",
        "status": "ok",
        "started_at": min(start_values) if start_values else None,
        "ended_at": max(end_values) if end_values else None,
        "duration_ms": None,
        "model": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": 0.0,
        "error_text": None,
        "attributes": {},
        "children": roots,
    }
    return synthetic


# ──────────────────────────────────────────────
# Public API functions
# ──────────────────────────────────────────────


async def get_run_trace_tree(db: AsyncSession, run_id: uuid.UUID) -> Dict[str, Any]:
    """Return the nested trace tree for a run."""
    from app.services.run_service import RunValidationError

    # Verify the run exists
    run_result = await db.execute(select(Run).where(Run.id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        raise RunValidationError(f"Run {run_id} not found", status_code=404)

    # Fetch all spans for the run, ordered by start_time (index-backed)
    result = await db.execute(
        select(Span)
        .where(Span.run_id == run_id)
        .order_by(Span.start_time.asc())
    )
    spans = list(result.scalars().all())
    return _build_tree(spans)


async def get_run_spans(
    db: AsyncSession,
    run_id: uuid.UUID,
    span_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a flat list of spans for a run, optionally filtered by type."""
    from app.services.run_service import RunValidationError

    # Verify the run exists
    run_result = await db.execute(select(Run).where(Run.id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        raise RunValidationError(f"Run {run_id} not found", status_code=404)

    query = select(Span).where(Span.run_id == run_id)
    if span_type:
        query = query.where(Span.span_type == span_type)
    query = query.order_by(Span.start_time.asc())

    result = await db.execute(query)
    spans = list(result.scalars().all())
    return [_enrich_span(s) for s in spans]


async def get_run_summary(db: AsyncSession, run_id: uuid.UUID) -> Dict[str, Any]:
    """Return an aggregated summary of a run's execution."""
    from app.services.run_service import RunValidationError

    # Fetch the run
    run_result = await db.execute(select(Run).where(Run.id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        raise RunValidationError(f"Run {run_id} not found", status_code=404)

    # Fetch all spans for the run
    result = await db.execute(
        select(Span).where(Span.run_id == run_id).order_by(Span.start_time.asc())
    )
    spans = list(result.scalars().all())

    # Fetch run_steps for authoritative per-step status + attempt counts.
    # A step that succeeds after retries has run_step.status='completed';
    # deriving status from spans alone would misreport it as failed because
    # the failed attempt's span also carries status='error'.
    rs_result = await db.execute(select(RunStep).where(RunStep.run_id == run_id))
    run_steps = rs_result.scalars().all()
    run_step_status = {str(rs.step_id): rs.status for rs in run_steps}
    run_step_attempts = {str(rs.step_id): rs.attempt_count for rs in run_steps}

    # Aggregate
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    error_count = 0
    span_count = len(spans)
    steps: Dict[str, Dict[str, Any]] = {}

    for span in spans:
        meta = span.meta_json or {}
        tokens_in = span.tokens_in
        tokens_out = span.tokens_out
        if tokens_in is None and meta:
            tokens_in = meta.get("tokens", {}).get("prompt")
        if tokens_out is None and meta:
            tokens_out = meta.get("tokens", {}).get("completion")

        if tokens_in:
            total_tokens_in += tokens_in
        if tokens_out:
            total_tokens_out += tokens_out

        cost = span.cost_usd
        if cost is None:
            cost = _calculate_cost(span.model, span.tokens_in, span.tokens_out, meta)
        total_cost += cost

        if span.status == "error":
            error_count += 1

        # Per-step aggregation (group by step_id)
        if span.step_id:
            step_key = str(span.step_id)
            if step_key not in steps:
                steps[step_key] = {
                    "step_id": step_key,
                    "step_key": span.name,  # e.g. "step:step_1"
                    "status": run_step_status.get(step_key, "completed"),
                    "attempts": run_step_attempts.get(step_key, 0),
                    "errors": 0,
                    "duration_ms": 0.0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                }
            step = steps[step_key]
            # Sum child execution time (llm/tool spans). The step span itself
            # is excluded — each retry emits a new llm/tool span, so this is
            # the accurate attempt indicator for duration.
            if span.kind in ("llm", "tool") and span.duration_ms:
                step["duration_ms"] += span.duration_ms
            if span.status == "error":
                step["errors"] += 1
            step["tokens_in"] += tokens_in or 0
            step["tokens_out"] += tokens_out or 0
            step["cost_usd"] += cost

    # Run duration
    duration_ms = None
    if run.started_at and run.ended_at:
        duration_ms = round(
            (_to_utc(run.ended_at) - _to_utc(run.started_at)).total_seconds() * 1000, 2
        )

    return {
        "run_id": str(run.id),
        "status": run.status,
        "duration_ms": duration_ms,
        "span_count": span_count,
        "error_count": error_count,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_tokens": total_tokens_in + total_tokens_out,
        "cost_usd": round(total_cost, 8),
        "steps": list(steps.values()),
    }
