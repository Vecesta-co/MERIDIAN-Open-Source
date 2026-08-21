import asyncio
import sys
sys.path.insert(0, '.')

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from app.main import app
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from uuid import UUID

from app.db.models import Base, EvalDefinition, Mission, Run

test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestSessionFactory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def main():
    # Use the test client approach
    from app.main import app as fastapi_app
    from contextlib import asynccontextmanager
    
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create mission
        resp = await client.post("/api/v1/missions", json={
            "name": "Eval Test Mission",
            "goal": "Test",
            "description": "",
            "steps": [
                {"key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Research", "max_retries": 1, "timeout_seconds": 60},
                {"key": "step_2", "name": "Step 2", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Summarize", "order_index": 1, "max_retries": 0, "timeout_seconds": 60},
            ],
        })
        print(f"Create mission: {resp.status_code}")
        mission = resp.json()
        
        # Publish
        resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
        print(f"Publish: {resp.status_code}")
        
        # Create run
        resp = await client.post("/api/v1/runs", json={"mission_id": mission["id"], "input_context": {"topic": "AI"}})
        print(f"Create run: {resp.status_code}")
        run = resp.json()
        print(f"Run: {run}")
        
        # Check run status via API
        resp = await client.get(f"/api/v1/runs/{run['id']}")
        print(f"Get run: {resp.status_code}")
        if resp.status_code == 200:
            run_detail = resp.json()
            print(f"Run detail: mission_id={run_detail.get('mission_id')}, status={run_detail.get('status')}")
        
        # Execute run (mock)
        from unittest.mock import AsyncMock, patch
        from app.services.run_service import execute_run
        import uuid
        
        mock_llm = AsyncMock(return_value={"text": "Mocked LLM output", "model": "gpt-4o-mini", "prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23, "finish_reason": "stop"})
        with patch("app.services.run_service.llm_service.call_llm", mock_llm):
            async with TestSessionFactory() as db:
                await execute_run(db, UUID(run["id"]))
        print(f"Run executed")
        
        # Check run status after execution
        resp = await client.get(f"/api/v1/runs/{run['id']}")
        print(f"Get run after exec: {resp.status_code}")
        if resp.status_code == 200:
            run_detail = resp.json()
            print(f"Run detail after exec: mission_id={run_detail.get('mission_id')}, status={run_detail.get('status')}")
        
        # Create eval
        resp = await client.post("/api/v1/evals", json={
            "name": "Step1 has output",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        })
        print(f"Create eval: {resp.status_code}")
        
        # Run evals
        resp = await client.post(f"/api/v1/runs/{run['id']}/evals/run")
        print(f"Run evals: {resp.status_code}")
        print(f"Response: {resp.text if resp.status_code != 200 else resp.json()}")


asyncio.run(main())