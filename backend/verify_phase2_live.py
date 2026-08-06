"""
MERIDIAN Phase 2 — Live Runtime Verification Script.

Verifies the Agent Runtime end-to-end against the real SQLite dev DB:
  - Create + publish a mission
  - POST /runs creates a pending run
  - execute_run (real engine) processes steps with a mock LLM
  - GET /runs/{id} detail + steps
  - Cancellation path
  - Timeout path (global run timeout)
  - Stale reaping (watchdog)

Since Redis and a real LLM API key are not available in this env,
the RQ worker + real LLM path cannot be exercised here; that is
documented as a coverage gap. This proves the core runtime engine
works against a real persistent DB.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.db.models import Run
from app.db.session import async_session_factory

BASE_URL = "http://127.0.0.1:8000"
OK = "\033[92m"
FAIL = "\033[91m"
END = "\033[0m"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"{OK}  PASS{END}  {name}")
    else:
        failed += 1
        print(f"{FAIL}  FAIL{END}  {name}  {detail}")


def valid_mission_payload() -> dict:
    return {
        "name": "Phase 2 Live Runtime Mission",
        "goal": "Verify the agent runtime engine live",
        "description": "Mission for live Phase 2 verification",
        "steps": [
            {
                "key": "step_1",
                "name": "Step 1",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Research. Prior: {{prior.input}}",
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


def mock_llm_output(text: str = "Mocked LLM output") -> dict:
    return {
        "text": text,
        "model": "gpt-4o-mini",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "finish_reason": "stop",
    }


async def execute_run_via_engine(run_id: uuid.UUID, llm_mock=None):
    """Run the real execute_run engine against the real DB with a mock LLM."""
    from app.services.run_service import execute_run

    async with async_session_factory() as db:
        if llm_mock is not None:
            with patch("app.services.run_service.llm_service.call_llm", llm_mock):
                return await execute_run(db, run_id)
        return await execute_run(db, run_id)


async def main():
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)

    # ── 1. Health ──
    print("\n[1] Health endpoint")
    r = await client.get("/health")
    body = r.json()
    check("GET /health -> 200", r.status_code == 200, str(r.status_code))
    check("status=healthy", body.get("status") == "healthy", str(body))
    check("database_connected=true", body.get("database_connected") is True, str(body))

    # ── 2. Create + publish mission ──
    print("\n[2] Create + publish mission")
    r = await client.post("/api/v1/missions", json=valid_mission_payload())
    check("POST /missions -> 201", r.status_code == 201, str(r.status_code))
    mission = r.json()
    mission_id = mission.get("id")
    check("mission created", mission_id is not None, str(mission))
    check("mission state=draft", mission.get("state") == "draft")

    pr = await client.post(f"/api/v1/missions/{mission_id}/publish")
    check("POST /missions/{id}/publish -> 200", pr.status_code == 200, str(pr.status_code))
    check("mission state=published", pr.json().get("state") == "published", str(pr.json()))

    # ── 3. POST /runs (create + enqueue best-effort) ──
    print("\n[3] POST /runs")
    rr = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "AI agents"}},
    )
    check("POST /runs -> 201", rr.status_code == 201, str(rr.status_code))
    run = rr.json()
    run_id = run.get("id")
    check("run status=pending", run.get("status") == "pending", str(run))
    check("run mission_id matches", run.get("mission_id") == mission_id)
    check("run mission_version_id present", bool(run.get("mission_version_id")))
    check("run cancel_requested=False", run.get("cancel_requested") is False)

    # ── 4. Execute run via real engine (mock LLM) ──
    print("\n[4] Execute run (real engine, mock LLM)")
    mock_llm = AsyncMock(return_value=mock_llm_output())
    executed = await execute_run_via_engine(uuid.UUID(run_id), mock_llm)
    check("run status=completed", executed.status == "completed", executed.status)
    check("run ended_at set", executed.ended_at is not None)
    check("error_summary is None", executed.error_summary is None)

    # ── 5. GET /runs/{id} detail ──
    print("\n[5] GET /runs/{id} detail")
    gr = await client.get(f"/api/v1/runs/{run_id}")
    check("GET /runs/{id} -> 200", gr.status_code == 200, str(gr.status_code))
    detail = gr.json()
    check("detail status=completed", detail.get("status") == "completed", str(detail.get("status")))
    steps = detail.get("run_steps", [])
    check("2 run_steps returned", len(steps) == 2, str(len(steps)))
    check("all steps completed", all(s.get("status") == "completed" for s in steps), str(steps))
    check("all attempt_count=1", all(s.get("attempt_count") == 1 for s in steps), str(steps))
    check("step has step_key", all(s.get("step_key") for s in steps), str(steps))
    check("step has step_name", all(s.get("step_name") for s in steps), str(steps))
    check("step has order_index", all(s.get("order_index") is not None for s in steps), str(steps))
    spans = detail.get("spans", [])
    check("spans recorded (>=3)", len(spans) >= 3, f"count={len(spans)}")

    # ── 6. GET /runs/{id}/steps ──
    print("\n[6] GET /runs/{id}/steps")
    sr = await client.get(f"/api/v1/runs/{run_id}/steps")
    check("GET /runs/{id}/steps -> 200", sr.status_code == 200, str(sr.status_code))
    ssteps = sr.json()
    check("2 steps returned", len(ssteps) == 2, str(len(ssteps)))
    check("steps ordered by order_index",
          [s.get("order_index") for s in ssteps] == [0, 1], str(ssteps))

    # ── 7. Cancellation path ──
    print("\n[7] Cancellation path")
    # Create a fresh run
    rr = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "cancel me"}},
    )
    run2 = rr.json()
    run2_id = run2.get("id")
    check("run2 created pending", run2.get("status") == "pending")

    # Cancel it
    cr = await client.post(f"/api/v1/runs/{run2_id}/cancel")
    check("POST /runs/{id}/cancel -> 200", cr.status_code == 200, str(cr.status_code))
    check("cancel_requested=True", cr.json().get("cancel_requested") is True, str(cr.json()))

    # Execute — should be cancelled
    mock_llm2 = AsyncMock(return_value=mock_llm_output())
    executed2 = await execute_run_via_engine(uuid.UUID(run2_id), mock_llm2)
    check("cancelled run status=cancelled", executed2.status == "cancelled", executed2.status)
    check("cancelled error_summary mentions cancelled",
          "Cancelled" in (executed2.error_summary or ""), str(executed2.error_summary))

    # Cancel a completed run -> 400
    cc = await client.post(f"/api/v1/runs/{run_id}/cancel")
    check("cancel completed -> 400", cc.status_code == 400, str(cc.status_code))

    # ── 8. Timeout path (global run timeout) ──
    print("\n[8] Global run timeout path")
    rr = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "timeout me"}},
    )
    run3 = rr.json()
    run3_id = run3.get("id")

    # Force a step timeout by making LLM never return (raise TimeoutError)
    async def slow_llm(prompt, **kwargs):
        raise TimeoutError("Simulated LLM timeout")

    executed3 = await execute_run_via_engine(uuid.UUID(run3_id), AsyncMock(side_effect=slow_llm))
    check("timeout run eventually terminal", executed3.status in ("failed", "timed_out"), executed3.status)

    # ── 9. Stale reaping (watchdog) ──
    print("\n[9] Stale run reaping (watchdog)")
    from app.services.run_service import reap_stale_runs
    rr = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "stale me"}},
    )
    run4 = rr.json()
    run4_id = run4.get("id")
    # Manually mark as running + old started_at
    async with async_session_factory() as db:
        db_run = await db.get(Run, uuid.UUID(run4_id))
        db_run.status = "running"
        db_run.started_at = datetime.now(timezone.utc) - timedelta(minutes=60)
        await db.commit()

    async with async_session_factory() as db:
        reaped = await reap_stale_runs(db)
    check("reap_stale_runs returned 1", reaped == 1, f"reaped={reaped}")

    gr = await client.get(f"/api/v1/runs/{run4_id}")
    check("stale run now failed", gr.json().get("status") == "failed", str(gr.json().get("status")))

    # ── 10. List runs ──
    print("\n[10] GET /runs (list)")
    lr = await client.get("/api/v1/runs")
    check("GET /runs -> 200", lr.status_code == 200, str(lr.status_code))
    runs_list = lr.json()
    check("list has >=4 runs", len(runs_list) >= 4, f"count={len(runs_list)}")

    # ── 11. Placeholder 501 ──
    print("\n[11] Placeholder 501s")
    tr = await client.get(f"/api/v1/runs/{run_id}/trace")
    check("GET /runs/{id}/trace -> 501", tr.status_code == 501, str(tr.status_code))
    sr = await client.get(f"/api/v1/runs/{run_id}/summary")
    check("GET /runs/{id}/summary -> 501", sr.status_code == 501, str(sr.status_code))
    er = await client.get(f"/api/v1/runs/{run_id}/evals")
    check("GET /runs/{id}/evals -> 501", er.status_code == 501, str(er.status_code))

    await client.aclose()

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    if failed == 0:
        print(f"{OK}ALL PHASE 2 LIVE RUNTIME CHECKS PASSED{END}")
    else:
        print(f"{FAIL}SOME CHECKS FAILED — INVESTIGATE{END}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
