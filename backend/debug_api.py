import asyncio
import json
from uuid import UUID

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from app.main import app

async def test():
    # Use the test client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First create a mission
        resp = await client.post("/api/v1/missions", json={
            "name": "Test Mission",
            "goal": "Test",
            "description": "",
            "steps": [
                {"key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Research", "max_retries": 1, "timeout_seconds": 60},
                {"key": "step_2", "name": "Step 2", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Summarize", "order_index": 1, "max_retries": 0, "timeout_seconds": 60},
            ],
        })
        print(f"Create mission: {resp.status_code}")
        mission = resp.json()
        print(f"Mission: {mission}")
        
        # Publish mission
        resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
        print(f"Publish: {resp.status_code}")
        
        # Create run
        resp = await client.post("/api/v1/runs", json={"mission_id": mission["id"], "input_context": {"topic": "AI"}})
        print(f"Create run: {resp.status_code}")
        run = resp.json()
        print(f"Run: {run}")
        
        # Create eval definition with mission_id
        resp = await client.post("/api/v1/evals", json={
            "name": "Step1 has output",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        })
        print(f"Create eval: {resp.status_code}")
        eval_def = resp.json()
        print(f"Eval: {eval_def}")
        
        # Run evals
        resp = await client.post(f"/api/v1/runs/{run['id']}/evals/run")
        print(f"Run evals: {resp.status_code}")
        print(f"Response JSON: {resp.json() if resp.status_code == 200 else 'N/A'}")

asyncio.run(test())