"""
MERIDIAN Phase 2 — Agent Runtime Tests.

Tests the run lifecycle:
  - POST /runs creates a pending run from a published mission
  - execute_run processes steps sequentially (pending → running → completed)
  - LLM calls are mocked (never hit external APIs)
  - Cancellation sets cancel_requested and stops the run
  - Retry attempts increment on transient failures
  - Tool steps are stubs (return tool_not_implemented error)
  - Timeouts and stale runs are reaped
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.db.models import Run
from app.services.run_service import execute_run, reap_stale_runs
from tests.conftest import TestSessionFactory


# ──────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_redis_enqueue():
    """
    Prevent the POST /runs endpoint from attempting a real Redis
    connection. The worker/queue is not part of the test scope —
    we execute runs directly via execute_run() instead.
    """
    with patch("app.api.v1.runs.enqueue_run", return_value="test-job-id"):
        yield


def valid_mission_payload() -> dict:
    """A valid mission JSON payload with 2 LLM steps."""
    return {
        "name": "Runtime Test Mission",
        "goal": "Test the agent runtime engine",
        "description": "A mission for testing Phase 2 runtime",
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
    """Return a mock LLM response dict."""
    return {
        "text": text,
        "model": "gpt-4o-mini",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "finish_reason": "stop",
    }


# ──────────────────────────────────────────────
# 1. Run Creation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_run_published_mission(async_client: AsyncClient):
    """POST /runs from a published mission creates a pending run."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    assert run["status"] == "pending"
    assert run["mission_id"] == mission["id"]
    assert run["mission_version_id"]
    assert run["cancel_requested"] is False
    assert run["triggered_by"] == "manual"


@pytest.mark.asyncio
async def test_create_run_draft_mission_returns_400(async_client: AsyncClient):
    """POST /runs from a draft mission returns 400."""
    # Create a draft mission (not published)
    response = await async_client.post("/api/v1/missions", json=valid_mission_payload())
    assert response.status_code == 201
    mission_id = response.json()["id"]

    run_resp = await async_client.post(
        "/api/v1/runs", json={"mission_id": mission_id}
    )
    assert run_resp.status_code == 400
    assert "published" in run_resp.text.lower()


@pytest.mark.asyncio
async def test_create_run_nonexistent_mission_returns_404(async_client: AsyncClient):
    """POST /runs with a nonexistent mission returns 404."""
    response = await async_client.post(
        "/api/v1/runs", json={"mission_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


# ──────────────────────────────────────────────
# 2. Run Execution (mocked LLM)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_run_completes_successfully(async_client: AsyncClient):
    """A run with mocked LLM completes with all steps successful."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())

    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run["id"]))

    assert executed.status == "completed"
    assert executed.ended_at is not None
    assert executed.error_summary is None

    # Verify run steps are completed
    get_resp = await async_client.get(f"/api/v1/runs/{run['id']}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["status"] == "completed"
    assert len(detail["run_steps"]) == 2
    assert all(rs["status"] == "completed" for rs in detail["run_steps"])
    assert all(rs["attempt_count"] == 1 for rs in detail["run_steps"])

    # Verify spans were recorded
    assert len(detail["spans"]) >= 3  # run span + 2 step spans


@pytest.mark.asyncio
async def test_execute_run_with_input_context_renders_template(async_client: AsyncClient):
    """LLM prompt is rendered with prior step outputs."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    captured_prompts = []

    async def fake_llm(prompt, **kwargs):
        captured_prompts.append(prompt)
        return mock_llm_output(text=f"Output for: {prompt}")

    with patch("app.services.run_service.llm_service.call_llm", fake_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    # Step 1 prompt should include the input context
    assert any("AI agents" in p for p in captured_prompts)
    # Step 2 prompt should include step_1 output
    assert any("Output for" in p for p in captured_prompts)


# ──────────────────────────────────────────────
# 3. Cancellation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_run_sets_cancel_requested(async_client: AsyncClient):
    """POST /runs/{id}/cancel sets cancel_requested=True on a pending run."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    cancel_resp = await async_client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancel_resp.status_code == 200
    data = cancel_resp.json()
    assert data["cancel_requested"] is True


@pytest.mark.asyncio
async def test_cancel_run_between_steps(async_client: AsyncClient):
    """Cancellation between steps marks the run as cancelled."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())

    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        from app.services.run_service import cancel_run

        # Cancel the run before execution
        async with TestSessionFactory() as db:
            await cancel_run(db, uuid.UUID(run["id"]))

        # Execute — should detect cancellation and stop
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run["id"]))

    assert executed.status == "cancelled"
    assert executed.error_summary == "Cancelled by user"


@pytest.mark.asyncio
async def test_cancel_run_completed_returns_400(async_client: AsyncClient):
    """Cancelling a completed run returns 400."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # Execute to completion
    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    # Attempt to cancel a completed run
    cancel_resp = await async_client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancel_resp.status_code == 400


# ──────────────────────────────────────────────
# 4. Retry Logic
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_attempts_increment_on_transient_failure(async_client: AsyncClient):
    """A step that fails transiently retries and increments attempt_count."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # First call fails, second succeeds
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
    assert call_count >= 2  # at least one retry

    # Verify attempt_count reflects the retry on step 1
    get_resp = await async_client.get(f"/api/v1/runs/{run['id']}")
    assert get_resp.status_code == 200
    detail = get_resp.json()

    # Step 1 should have attempt_count >= 2 (first failed, retried)
    step_1 = detail["run_steps"][0]
    assert step_1["attempt_count"] >= 2


@pytest.mark.asyncio
async def test_retry_exhausted_marks_run_failed(async_client: AsyncClient):
    """A step that fails all retries marks the run as failed."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    async def always_fail(prompt, **kwargs):
        raise RuntimeError("Persistent LLM failure")

    with patch("app.services.run_service.llm_service.call_llm", always_fail):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run["id"]))

    assert executed.status == "failed"
    assert executed.error_summary is not None
    assert "Persistent LLM failure" in executed.error_summary


# ──────────────────────────────────────────────
# 5. Tool Steps (Stub)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_step_returns_not_implemented(async_client: AsyncClient):
    """A tool step fails with tool_not_implemented (Phase 2 stub)."""
    # Create a mission with a tool step
    payload = valid_mission_payload()
    payload["steps"].append(
        {
            "key": "tool_step",
            "name": "Tool Step",
            "step_type": "tool",
            "tool_refs": [{"tool_name": "web_search"}],
            "order_index": 2,
        }
    )
    response = await async_client.post("/api/v1/missions", json=payload)
    assert response.status_code == 201
    mission_id = response.json()["id"]

    publish_resp = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert publish_resp.status_code == 200

    run = await create_run_via_api(async_client, mission_id)

    # Mock LLM to succeed for the llm steps
    mock_llm = AsyncMock(return_value=mock_llm_output())

    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            executed = await execute_run(db, uuid.UUID(run["id"]))

    # The tool step fails → run fails
    assert executed.status == "failed"
    assert "not implemented" in executed.error_summary.lower()


# ──────────────────────────────────────────────
# 6. Timeouts & Stale Runs
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reap_stale_runs(async_client: AsyncClient):
    """Watchdog marks stale running runs as failed."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    # Manually set a run to 'running' with an old started_at
    async with TestSessionFactory() as db:
        db_run = await db.get(Run, uuid.UUID(run["id"]))
        db_run.status = "running"
        db_run.started_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        await db.commit()

    async with TestSessionFactory() as db:
        reaped = await reap_stale_runs(db)

    assert reaped == 1

    # Verify the run is now failed
    get_resp = await async_client.get(f"/api/v1/runs/{run['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "failed"
    assert "stale" in get_resp.json()["error_summary"].lower()


# ──────────────────────────────────────────────
# 7. Run Detail & Steps Endpoints
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_run_detail_includes_steps_and_spans(async_client: AsyncClient):
    """GET /runs/{id} returns run with steps and spans."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    get_resp = await async_client.get(f"/api/v1/runs/{run['id']}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["id"] == run["id"]
    assert detail["status"] == "completed"
    assert len(detail["run_steps"]) == 2
    assert len(detail["spans"]) >= 3

    # Each run_step has step details
    for rs in detail["run_steps"]:
        assert rs["step_key"] is not None
        assert rs["step_name"] is not None
        assert rs["step_kind"] is not None
        assert rs["order_index"] is not None


@pytest.mark.asyncio
async def test_get_run_steps_endpoint(async_client: AsyncClient):
    """GET /runs/{id}/steps returns the run steps ordered by order_index."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))

    steps_resp = await async_client.get(f"/api/v1/runs/{run['id']}/steps")
    assert steps_resp.status_code == 200
    steps = steps_resp.json()
    assert len(steps) == 2
    assert steps[0]["step_key"] == "step_1"
    assert steps[1]["step_key"] == "step_2"
    assert steps[0]["order_index"] == 0
    assert steps[1]["order_index"] == 1


@pytest.mark.asyncio
async def test_get_run_nonexistent_returns_404(async_client: AsyncClient):
    """GET /runs/{nonexistent_id} returns 404."""
    response = await async_client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert response.status_code == 404


# ──────────────────────────────────────────────
# 8. List Runs
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_runs_endpoint(async_client: AsyncClient):
    """GET /runs returns a list of runs."""
    mission = await create_published_mission(async_client)
    await create_run_via_api(async_client, mission["id"])

    response = await async_client.get("/api/v1/runs")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["status"] == "pending"


# ──────────────────────────────────────────────
# 9. Placeholder Endpoints (501)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_trace_placeholder_returns_501(async_client: AsyncClient):
    """GET /runs/{id}/trace returns 501 (Phase 4 placeholder)."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/trace")
    assert response.status_code == 501
    assert response.json()["detail"] == "Not Implemented"


@pytest.mark.asyncio
async def test_run_summary_placeholder_returns_501(async_client: AsyncClient):
    """GET /runs/{id}/summary returns 501 (Phase 4 placeholder)."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/summary")
    assert response.status_code == 501
    assert response.json()["detail"] == "Not Implemented"


@pytest.mark.asyncio
async def test_run_evals_placeholder_returns_501(async_client: AsyncClient):
    """GET /runs/{id}/evals returns 501 (Phase 5 placeholder)."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    response = await async_client.get(f"/api/v1/runs/{run['id']}/evals")
    assert response.status_code == 501
    assert response.json()["detail"] == "Not Implemented"
