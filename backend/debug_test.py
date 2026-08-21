import asyncio
import sys
sys.path.insert(0, '.')

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from app.main import app

async def run_test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create mission
        resp = await client.post("/api/v1/missions", json={
            "name": "Eval Test Mission",
            "goal": "Test the eval suite",
            "description": "A mission for testing Phase 5 evals",
            "steps": [
                {"key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Research the topic.", "max_retries": 1, "timeout_seconds": 60},
                {"key": "step_2", "name": "Step 2", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Summarize: {{prior.step_1}}", "order_index": 1, "max_retries": 0, "timeout_seconds": 60},
            ],
        })
        print(f"Create mission: {resp.status_code}")
        if resp.status_code != 201:
            print(f"Response: {resp.text}")
            return
        mission = resp.json()
        
        # Publish
        resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
        print(f"Publish: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Response: {resp.text}")
            return
        
        # Create run
        resp = await client.post("/api/v1/runs", json={"mission_id": mission["id"], "input_context": {"topic": "AI agents"}})
        print(f"Create run: {resp.status_code}")
        if resp.status_code != 201:
            print(f"Response: {resp.text}")
            return
        run = resp.json()
        
        # Execute run (mock)
        from unittest.mock import AsyncMock, patch
        from app.services.run_service import execute_run
        from app.db.session import TestSessionFactory
        import uuid
        
        mock_llm = AsyncMock(return_value={"text": "Mocked LLM output", "model": "gpt-4o-mini", "prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23, "finish_reason": "stop"})
        with patch("app.services.run_service.llm_service.call_llm", mock_llm):
            async with TestSessionFactory() as db:
                await execute_run(db, uuid.UUID(run["id"]))
        print(f"Run executed")
        
        # Create eval definition
        resp = await client.post("/api/v1/evals", json={
            "name": "Step1 has output",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        })
        print(f"Create eval: {resp.status_code}")
        if resp.status_code != 201:
            print(f"Response: {resp.text}")
            return
        eval_def = resp.json()
        
        # Run evals
        resp = await client.post(f"/api/v1/runs/{run['id']}/evals/run")
        print(f"Run evals: {resp.status_code}")
        print(f"Response: {resp.text if resp.status_code != 200 else resp.json()}")

asyncio.run(run_test())