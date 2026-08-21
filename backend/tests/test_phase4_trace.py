"""
MERIDIAN Phase 4 — Trace Engine Tests.

Tests the trace tree reconstruction, summary aggregation, and
span filtering endpoints:
  - GET /runs/{id}/trace   → nested trace tree
  - GET /runs/{id}/summary → duration, tokens, cost, errors
  - GET /runs/{id}/spans   → flat filtered span list
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.db.models import RunStep, Span, Step
from app.services.run_service import (
    _execute_step_with_retry,
    execute_run,
    purge_old_spans,
)
from tests.conftest import TestSessionFactory


# ──────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_redis_enqueue():
    """Prevent the POST /runs endpoint from attempting a real Redis connection."""
    with patch("app.api.v1.runs.enqueue_run", return_value="test-job-id"):
        yield


def valid_mission_payload() -> dict:
    """A valid mission JSON payload with 2 LLM steps."""
    return {
        "name": "Trace Test Mission",
        "goal": "Test the trace engine",
        "description": "A mission for testing Phase 4 trace engine",
        "steps": [
            {
                "key": "step_1",
                "name": "Step 1",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Research the topic. Prior: {{prior.input}}",
                "max_retries": 2,
                "timeout_seconds": 120,
            },
            {
                "key": "step_2",
                "name": "Step 2",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Summarize: {{prior.step_1}}",
                "order_index": 1,
                "max_retries": 1,
                "timeout_seconds": 60,
            },
        ],
    }


async def create_published_mission(client: AsyncClient) -> dict:
    """Helper: create a mission, publish it, and return the mission JSON."""
    response = await client.post("/api/v1/missions", json=valid_mission_payload())
    assert response.status_code == 201, f"Failed to create mission: {response.text}"
    mission = response.json()

    publish_resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200, f"Failed to publish: {publish_resp.text}"
    return publish_resp.json()


async def create_run_via_api(client: AsyncClient, mission_id: str) -> dict:
    """Helper: POST /runs to create a pending run."""
    response = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "AI agents"}},
    )
    assert response.status_code == 201, f"Failed to create run: {response.text}"
    return response.json()


def mock_llm_output(text: str = "Mocked LLM output") -> dict:
    """Return a mock LLM response dict with token info."""
    return {
        "text": text,
        "model": "gpt-4o-mini",
        "prompt_tokens": 15,
        "completion_tokens": 8,
        "total_tokens": 23,
        "finish_reason": "stop",
    }


def failing_mission_payload() -> dict:
    """A mission with a single step that fails immediately (no retries)."""
    return {
        "name": "Failing Step Mission",
        "goal": "Test the cross-run step failure query",
        "description": "A mission for testing Phase 4 failing-steps",
        "steps": [
            {
                "key": "unstable_step",
                "name": "Unstable Step",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Do the thing",
                "max_retries": 0,
                "timeout_seconds": 60,
            }
        ],
    }


async def create_failing_run(client: AsyncClient, mission_id: str) -> dict:
    """Create a run whose only step fails, leaving a failed RunStep."""
    run = await create_run_via_api(client, mission_id)

    async def always_fail(prompt, **kwargs):
        raise RuntimeError("Persistent step failure")

    with patch("app.services.run_service.llm_service.call_llm", always_fail):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))
    return run


async def execute_mock_run(client: AsyncClient, mission_id: str) -> dict:
    """Create and execute a run with mocked LLM, returning the run dict."""
    run = await create_run_via_api(client, mission_id)
    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))
    return run


# ──────────────────────────────────────────────
# 1. Trace Tree Reconstruction
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trace_tree_structure(async_client: AsyncClient):
    """GET /runs/{id}/trace returns a nested tree with run span at root."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()

    # Root is the run span
    assert tree["id"]
    assert tree["span_type"] == "system"
    assert tree["name"].startswith("Run")

    # Children are the step spans
    assert len(tree["children"]) == 2
    child_types = {c["span_type"] for c in tree["children"]}
    assert child_types == {"system"}  # step spans → system type

    # Each step span has LLM children
    for child in tree["children"]:
        assert child["children"], "Step span should have LLM child spans"
        llm_types = {gc["span_type"] for gc in child["children"]}
        assert llm_types == {"llm_step"}


@pytest.mark.asyncio
async def test_trace_tree_retries_multiple_llm_spans(async_client: AsyncClient):
    """A retried step produces multiple LLM spans under the step span."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # First call fails, second succeeds → 2 LLM spans for step 1
    call_count = 0

    async def flaky_llm(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Rate limit exceeded")
        return mock_llm_output(text="Success after retry")

    with patch("app.services.run_service.llm_service.call_llm", flaky_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()

    # Step 1 should have 2 LLM children (failed attempt + retry)
    step_1 = tree["children"][0]
    llm_spans = [c for c in step_1["children"] if c["span_type"] == "llm_step"]
    assert len(llm_spans) == 2
    # First LLM span errored, second succeeded
    assert llm_spans[0]["status"] == "error"
    assert llm_spans[1]["status"] == "ok"


@pytest.mark.asyncio
async def test_trace_tree_orphan_spans_attach_to_synthetic_root(async_client: AsyncClient):
    """Spans with missing parent_span_id attach to a synthetic root."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # Manually insert an orphan span (no parent_span_id)
    async with TestSessionFactory() as db:
        orphan = Span(
            run_id=uuid.UUID(run["id"]),
            kind="system",
            span_type="system",
            name="orphan-span",
            status="ok",
            start_time=datetime.now(timezone.utc),
            meta_json={},
        )
        db.add(orphan)
        await db.commit()

    # Execute the run (mocked) to add normal spans
    await execute_mock_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()

    # Collect all span names in the tree
    def collect_names(node):
        names = [node.get("name")]
        for child in node.get("children", []):
            names.extend(collect_names(child))
        return names

    names = collect_names(tree)
    assert "orphan-span" in names


@pytest.mark.asyncio
async def test_trace_tree_nonexistent_run_returns_404(async_client: AsyncClient):
    """GET /runs/{id}/trace with a nonexistent run returns 404."""
    response = await async_client.get(f"/api/v1/runs/{uuid.uuid4()}/trace")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_run_span_finalized_on_completed_run(async_client: AsyncClient):
    """The run span (trace root) is finalised on a completed run."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()
    assert tree["status"] == "ok"
    assert tree["ended_at"] is not None
    assert tree["duration_ms"] is not None


@pytest.mark.asyncio
async def test_run_span_error_on_failed_run(async_client: AsyncClient):
    """The run span reflects an error outcome on a failed run."""
    resp = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert resp.status_code == 201
    mission = resp.json()
    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    run = await create_failing_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()
    assert tree["status"] == "error"
    assert tree["ended_at"] is not None
    assert tree["error_text"] is not None


@pytest.mark.asyncio
async def test_run_span_cancelled_on_cancelled_run(async_client: AsyncClient):
    """The run span reflects cancellation when a run is cancelled."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    async with TestSessionFactory() as db:
        from app.db.models import Run as RunModel

        run_obj = await db.get(RunModel, uuid.UUID(run["id"]))
        assert run_obj is not None
        run_obj.cancel_requested = True
        await db.commit()
        executed = await execute_run(db, uuid.UUID(run["id"]))

    assert executed.status == "cancelled"

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 200
    tree = response.json()
    assert tree["status"] == "cancelled"
    assert tree["ended_at"] is not None


@pytest.mark.asyncio
async def test_step_span_finalized_on_cancel(async_client: AsyncClient):
    """Cancelling during a step leaves its span finalised as cancelled."""
    from sqlalchemy import select as sa_select

    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    detail = (await async_client.get(f"/api/v1/missions/{mission['id']}")).json()
    step_id = detail["steps"][0]["id"]

    async with TestSessionFactory() as db:
        from app.db.models import Run as RunModel

        run_obj = await db.get(RunModel, uuid.UUID(run["id"]))
        assert run_obj is not None
        run_obj.cancel_requested = True
        step = await db.get(Step, uuid.UUID(step_id))
        assert step is not None
        rs = RunStep(run_id=run_obj.id, step_id=step.id, status="running")
        db.add(rs)
        await db.flush()

        span_result = await db.execute(
            sa_select(Span).where(Span.run_id == run_obj.id, Span.kind == "run")
        )
        run_span = span_result.scalar_one()
        assert run_span is not None

        outcome = await _execute_step_with_retry(
            db, run_obj, step, rs, {}, run_span.id
        )

        assert outcome == {"cancelled": True}
        assert rs.status == "cancelled"
        assert rs.span_id is not None

        step_span = await db.get(Span, rs.span_id)
        assert step_span is not None
        assert step_span.status == "cancelled"
        assert step_span.end_time is not None
        assert step_span.parent_span_id == run_span.id


# ──────────────────────────────────────────────
# 2. Summary Aggregation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_aggregates_tokens_and_cost(async_client: AsyncClient):
    """GET /runs/{id}/summary aggregates tokens, cost, and step stats."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/summary")
    assert response.status_code == 200
    summary = response.json()

    assert summary["status"] == "completed"
    assert summary["span_count"] >= 5  # run + 2 steps + 2 llm
    assert summary["error_count"] == 0
    assert summary["total_tokens_in"] >= 30  # 2 steps × 15 tokens
    assert summary["total_tokens_out"] >= 16  # 2 steps × 8 tokens
    assert summary["total_tokens"] == summary["total_tokens_in"] + summary["total_tokens_out"]
    assert summary["cost_usd"] > 0  # gpt-4o-mini has non-zero pricing
    assert summary["duration_ms"] > 0


@pytest.mark.asyncio
async def test_summary_with_retries_aggregates_multiple_spans(async_client: AsyncClient):
    """Summary aggregates correctly when a step has multiple spans (retries)."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # First call fails, second succeeds → 2 LLM spans for step 1
    call_count = 0

    async def flaky_llm(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Rate limit exceeded")
        return mock_llm_output(text="Success after retry")

    with patch("app.services.run_service.llm_service.call_llm", flaky_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    response = await async_client.get(f"/api/v1/runs/{run['id']}/summary")
    assert response.status_code == 200
    summary = response.json()

    # Step 1 had 2 spans (failed + retry). Verify error_count accounts for it.
    assert summary["error_count"] >= 1  # the failed LLM attempt
    # Token counts should include BOTH attempts
    assert summary["total_tokens_in"] >= 30  # 15 (failed) + 15 (retry) + 15 (step2)
    assert summary["total_tokens_out"] >= 16


@pytest.mark.asyncio
async def test_summary_step_status_completed_after_retry(async_client: AsyncClient):
    """A step that succeeds after retries reports 'completed', not 'failed'."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    call_count = 0

    async def flaky_llm(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Rate limit exceeded")
        return mock_llm_output(text="Success after retry")

    with patch("app.services.run_service.llm_service.call_llm", flaky_llm):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run["id"]))

    assert executed.status == "completed"

    response = await async_client.get(f"/api/v1/runs/{run['id']}/summary")
    assert response.status_code == 200
    summary = response.json()

    step_1 = next(s for s in summary["steps"] if s["step_key"].endswith("step_1"))
    # Retried-but-successful step must NOT be reported as failed.
    assert step_1["status"] == "completed"
    assert step_1["attempts"] == 2  # from run_step.attempt_count
    assert step_1["errors"] == 1  # the failed attempt span


@pytest.mark.asyncio
async def test_summary_step_status_failed_for_failed_run(async_client: AsyncClient):
    """A step that ultimately fails reports status 'failed'."""
    resp = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert resp.status_code == 201
    mission = resp.json()
    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    run = await create_failing_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/summary")
    assert response.status_code == 200
    summary = response.json()
    assert summary["status"] == "failed"

    step_1 = next(s for s in summary["steps"] if s["step_key"].endswith("unstable_step"))
    assert step_1["status"] == "failed"


@pytest.mark.asyncio
async def test_summary_nonexistent_run_returns_404(async_client: AsyncClient):
    """GET /runs/{id}/summary with a nonexistent run returns 404."""
    response = await async_client.get(f"/api/v1/runs/{uuid.uuid4()}/summary")
    assert response.status_code == 404


# ──────────────────────────────────────────────
# 3. Span Filtering
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spans_list_and_filter_by_type(async_client: AsyncClient):
    """GET /runs/{id}/spans returns a flat list, filterable by type."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    # All spans
    response = await async_client.get(f"/api/v1/runs/{run['id']}/spans")
    assert response.status_code == 200
    spans = response.json()
    assert len(spans) >= 5

    # Filter by llm_step
    llm_resp = await async_client.get(f"/api/v1/runs/{run['id']}/spans?type=llm_step")
    assert llm_resp.status_code == 200
    llm_spans = llm_resp.json()
    assert len(llm_spans) == 2
    assert all(s["span_type"] == "llm_step" for s in llm_spans)

    # Filter by system (run + step spans)
    sys_resp = await async_client.get(f"/api/v1/runs/{run['id']}/spans?type=system")
    assert sys_resp.status_code == 200
    sys_spans = sys_resp.json()
    assert len(sys_spans) == 3  # run + 2 step spans
    assert all(s["span_type"] == "system" for s in sys_spans)


@pytest.mark.asyncio
async def test_spans_have_tokens_and_cost_fields(async_client: AsyncClient):
    """Span detail includes model, tokens, cost, and duration."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/spans?type=llm_step")
    assert response.status_code == 200
    llm_spans = response.json()

    for span in llm_spans:
        assert span["model"] == "gpt-4o-mini"
        assert span["tokens_in"] == 15
        assert span["tokens_out"] == 8
        assert span["cost_usd"] > 0
        assert span["duration_ms"] is not None


# ──────────────────────────────────────────────
# 4. Cost Calculation (unit)
# ──────────────────────────────────────────────


def test_cost_calculation_known_model():
    """Cost is computed correctly for a known model."""
    from app.services.trace_service import _calculate_cost

    cost = _calculate_cost("gpt-4o-mini", 1000, 500)
    # 1000/1000 * 0.00015 + 500/1000 * 0.0006 = 0.00015 + 0.0003 = 0.00045
    assert round(cost, 6) == 0.00045


def test_cost_calculation_unknown_model_falls_back_to_default():
    """Unknown models fall back to the default pricing entry."""
    from app.services.trace_service import _calculate_cost

    cost = _calculate_cost("some-unknown-model", 1000, 1000)
    # default: 0.0001/1k in + 0.0001/1k out = 0.0002
    assert round(cost, 6) == 0.0002


def test_cost_calculation_no_tokens_returns_zero():
    """No token counts → cost 0.0."""
    from app.services.trace_service import _calculate_cost

    assert _calculate_cost("gpt-4o-mini", None, None) == 0.0


# ──────────────────────────────────────────────
# 5. Cross-Run Step Failure Query (Fix 3)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failing_steps_aggregates_across_runs(async_client: AsyncClient):
    """GET /runs/failing-steps groups failures by step across multiple runs."""
    response = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert response.status_code == 201, f"Failed to create mission: {response.text}"
    mission = response.json()

    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    # Two runs both fail on the same step
    await create_failing_run(async_client, mission["id"])
    await create_failing_run(async_client, mission["id"])

    resp = await async_client.get("/api/v1/runs/failing-steps")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data, list)
    assert len(data) == 1
    entry = data[0]
    assert entry["step_key"] == "unstable_step"
    assert entry["step_name"] == "Unstable Step"
    assert entry["failure_count"] == 2
    assert entry["last_error"] == "Persistent step failure"
    assert entry["last_failed_at"] is not None


@pytest.mark.asyncio
async def test_failing_steps_step_id_filter(async_client: AsyncClient):
    """GET /runs/failing-steps?step_id=... filters to a single step."""
    response = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert response.status_code == 201
    mission = response.json()

    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    await create_failing_run(async_client, mission["id"])

    # Resolve the step UUID from mission detail
    detail_resp = await async_client.get(f"/api/v1/missions/{mission['id']}")
    step_id = detail_resp.json()["steps"][0]["id"]

    resp = await async_client.get(f"/api/v1/runs/failing-steps?step_id={step_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["step_id"] == step_id
    assert data[0]["failure_count"] == 1

    # A random step id that never failed → empty list
    resp2 = await async_client.get(f"/api/v1/runs/failing-steps?step_id={uuid.uuid4()}")
    assert resp2.status_code == 200
    assert resp2.json() == []


@pytest.mark.asyncio
async def test_failing_steps_min_failures_filter(async_client: AsyncClient):
    """GET /runs/failing-steps?min_failures=2 excludes single-occurrence failures."""
    response = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert response.status_code == 201
    mission = response.json()

    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    # Only one failing run → failure_count = 1
    await create_failing_run(async_client, mission["id"])

    resp = await async_client.get("/api/v1/runs/failing-steps?min_failures=2")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_failing_steps_invalid_step_id_returns_400(async_client: AsyncClient):
    """GET /runs/failing-steps?step_id=<non-uuid> returns 400."""
    resp = await async_client.get("/api/v1/runs/failing-steps?step_id=not-a-uuid")
    assert resp.status_code == 400
    assert "Invalid step_id" in resp.text


@pytest.mark.asyncio
async def test_failing_runs_lists_runs_for_step(async_client: AsyncClient):
    """GET /runs/failing-steps/{step_id}/runs returns the concrete failing runs."""
    resp = await async_client.post("/api/v1/missions", json=failing_mission_payload())
    assert resp.status_code == 201
    mission = resp.json()
    publish_resp = await async_client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200

    run_a = await create_failing_run(async_client, mission["id"])
    run_b = await create_failing_run(async_client, mission["id"])

    detail = (await async_client.get(f"/api/v1/missions/{mission['id']}")).json()
    step_id = detail["steps"][0]["id"]

    response = await async_client.get(f"/api/v1/runs/failing-steps/{step_id}/runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert {row["run_id"] for row in data} == {run_a["id"], run_b["id"]}
    assert all(row["error"] == "Persistent step failure" for row in data)
    assert all(row["failed_at"] is not None for row in data)


@pytest.mark.asyncio
async def test_failing_runs_invalid_step_id_returns_400(async_client: AsyncClient):
    """GET /runs/failing-steps/<non-uuid>/runs returns 400."""
    resp = await async_client.get("/api/v1/runs/failing-steps/not-a-uuid/runs")
    assert resp.status_code == 400
    assert "Invalid step_id" in resp.text


# ──────────────────────────────────────────────
# 6. Trace Retention Policy (Fix 4)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_purge_old_spans_deletes_only_expired(async_client: AsyncClient):
    """purge_old_spans deletes spans past the cutoff but keeps the run."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    # Spans exist before the purge
    before = await async_client.get(f"/api/v1/runs/{run['id']}/spans")
    assert len(before.json()) >= 3

    # Backdate every span for this run beyond the retention window
    async with TestSessionFactory() as db:
        old_ts = datetime.now(timezone.utc) - timedelta(days=10)
        await db.execute(
            update(Span)
            .where(Span.run_id == uuid.UUID(run["id"]))
            .values(created_at=old_ts)
        )
        await db.commit()
        purged = await purge_old_spans(db, retention_days=1)

    assert purged >= 3

    # Run record is preserved (source of truth for lifecycle)
    get_resp = await async_client.get(f"/api/v1/runs/{run['id']}")
    assert get_resp.status_code == 200

    # Trace data for the run is gone
    after = await async_client.get(f"/api/v1/runs/{run['id']}/spans")
    assert after.json() == []


@pytest.mark.asyncio
async def test_purge_old_spans_keeps_recent_spans(async_client: AsyncClient):
    """purge_old_spans leaves spans inside the retention window untouched."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))
            purged = await purge_old_spans(db, retention_days=30)

    assert purged == 0

    resp = await async_client.get(f"/api/v1/runs/{run['id']}/spans")
    assert len(resp.json()) >= 3
