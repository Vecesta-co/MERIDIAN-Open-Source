import asyncio
import os
from uuid import UUID

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from app.main import app

async def test():
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
        mission = resp.json()
        print(f"Mission id type: {type(mission['id'])}, value: {mission['id']}")
        print(f"Mission tags: {mission.get('tags')}")
        
        # Publish
        resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
        print(f"Publish: {resp.status_code}")
        
        # Create run
        resp = await client.post("/api/v1/runs", json={"mission_id": mission["id"], "input_context": {"topic": "AI agents"}})
        print(f"Create run: {resp.status_code}")
        run = resp.json()
        print(f"Run id type: {type(run['id'])}, value: {run['id']}")
        print(f"Run mission_id: {run.get('mission_id')}")
        
        # Execute run (this is done internally, let's just check the run)
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
        eval_def = resp.json()
        print(f"Eval id type: {type(eval_def['id'])}, value: {eval_def['id']}")
        print(f"Eval mission_id: {eval_def.get('mission_id')}")

asyncio.run(test())