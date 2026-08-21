"""Seed script for MERIDIAN Phase 9 demo.

Creates a demo mission with 3 steps via the API, plus an eval definition.
Run: python seed_demo.py

Uses the FastAPI test client with in-memory SQLite database (same as tests/conftest.py).
"""
import asyncio

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.db.models import Base
from tests.conftest import TestSessionFactory, test_engine


async def seed_demo() -> dict:
    """Create the demo mission and return mission/run info."""

    # Create tables in the test database (same as test Engine)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override the get_db_session dependency using the same pattern as conftest.py
    from app.db.session import get_db_session

    async def override_get_db_session():
        async with TestSessionFactory() as session:
            return session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create mission with 3 steps
        mission_payload = {
            "name": "Demo Mission: Content Review",
            "goal": "Review and approve generated content",
            "description": "Demo mission for Phase 9 integration",
            "steps": [
                {
                    "key": "step1",
                    "name": "Fetch Source Content",
                    "step_type": "tool",
                    "tool_refs": [
                        {"tool_name": "http_request", "input": {"method": "GET", "url": "https://example.com"}}
                    ],
                    "approval_required": False,
                    "order_index": 0,
                },
                {
                    "key": "step2",
                    "name": "Fetch Secondary Content",
                    "step_type": "tool",
                    "tool_refs": [
                        {"tool_name": "http_request", "input": {"method": "GET", "url": "https://example.com/contact"}}
                    ],
                    "approval_required": False,
                    "order_index": 1,
                },
                {
                    "key": "step3",
                    "name": "Approve Content",
                    "step_type": "approval",
                    "approval_required": True,
                    "order_index": 2,
                },
            ],
        }

        response = await client.post("/api/v1/missions", json=mission_payload)
        if response.status_code != 201:
            raise RuntimeError(f"Failed to create mission: {response.status_code}")

        mission = response.json()
        mission_id = mission["id"]
        print("[OK] Created mission: " + mission_id)

        # 2. Create eval definition with rule_based config (rule + terms format required by validator)
        eval_response = await client.post("/api/v1/evals", json={
            "name": "Content Quality Check",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_all", "terms": ["word_count"]},
            "threshold": 0.8,
            "tags": ["demo"],
        })
        if eval_response.status_code != 201:
            raise RuntimeError(f"Failed to create eval: {eval_response.status_code}")

        eval_def = eval_response.json()
        print("[OK] Created eval definition: " + eval_def["name"] + " (id=" + str(eval_def["id"]) + ")")

        # 3. Publish the mission
        pub_response = await client.post("/api/v1/missions/" + mission_id + "/publish")
        if pub_response.status_code != 200:
            raise RuntimeError(f"Failed to publish mission: {pub_response.status_code}")

        print("[OK] Published mission")

        # 4. Create a run
        run_response = await client.post("/api/v1/runs", json={
            "mission_id": mission_id,
            "input_context": {"demo": True},
        })
        if run_response.status_code != 201:
            raise RuntimeError(f"Failed to create run: {run_response.status_code}")

        run = run_response.json()
        run_id = run["id"]
        print("[OK] Created run: " + run_id)
        print("  Status: " + str(run.get("status", "pending")))

        # 5. Execute the run in-process (no Redis/worker required for the demo).
        #    The mission uses only tool + approval steps, so no LLM API key is
        #    needed. The run executes the tool steps and pauses for approval.
        from uuid import UUID

        from app.services.run_service import execute_run

        async with TestSessionFactory() as db:
            executed = await execute_run(db, UUID(run_id))
            print("[OK] Executed run in-process: " + str(executed.status))
            if executed.status == "awaiting_approval":
                from app.db.models import Approval
                from sqlalchemy import select

                approval_result = await db.execute(
                    select(Approval)
                    .where(Approval.run_id == UUID(run_id), Approval.status == "pending")
                    .order_by(Approval.requested_at.desc())
                    .limit(1)
                )
                approval = approval_result.scalar_one_or_none()
                if approval is not None:
                    print("  Awaiting human approval (approval id=" + str(approval.id) + ")")
                    print("  Approve it via: POST /api/v1/approvals/" + str(approval.id) + "/decide")

        # Clean up dependency overrides
        app.dependency_overrides.clear()

        return {
            "mission_id": mission_id,
            "mission_name": mission_payload["name"],
            "run_id": run_id,
            "eval_definition_id": eval_def["id"],
            "run_status": str(executed.status),
            "steps": [
                {"key": "step1", "type": "tool", "tool": "http_request", "approval_required": False},
                {"key": "step2", "type": "tool", "tool": "http_request", "approval_required": False},
                {"key": "step3", "type": "approval", "approval_required": True},
            ],
        }


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

async def main():
    result = await seed_demo()
    print("\n--- Demo Seed Complete ---")
    print("Mission: " + result["mission_name"] + " (ID: " + str(result["mission_id"]) + ")")
    print("Run: " + str(result["run_id"]) + " (status: " + str(result["run_status"]) + ")")
    print("Eval: " + str(result["eval_definition_id"]))
    print("\nSteps:")
    for step in result["steps"]:
        print("  - " + step["key"] + ": " + step["type"] + " (approval_required=" + str(step["approval_required"]) + ")")



if __name__ == "__main__":
    asyncio.run(main())