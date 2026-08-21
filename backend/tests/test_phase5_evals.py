"""
MERIDIAN Phase 5 — Eval Suite Tests (core).

Tests eval definition CRUD, the three evaluators (rule_based, schema,
llm_judge), and eval execution against run artifacts.

Design constraints verified:
  - Evals never re-run the mission (only read run artifacts).
  - Re-running evals on a terminal run appends NEW result rows.
  - Non-terminal runs are rejected (400) by the manual rerun endpoint.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import EvalDefinition, EvalResult, Span
from app.services.eval_service import evaluate_rule_based, evaluate_schema
from app.services.run_service import execute_run
from tests.conftest import TestSessionFactory


# ──────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_redis_enqueue():
    """Prevent the POST /runs endpoint from attempting a real Redis connection."""
    with patch("app.api.v1.runs.enqueue_run", return_value="test-job-id"):
        yield


def llm_mission_payload(tags=None) -> dict:
    """A valid mission with two LLM steps, optionally tagged."""
    payload = {
        "name": "Eval Test Mission",
        "goal": "Test the eval suite",
        "description": "A mission for testing Phase 5 evals",
        "steps": [
            {
                "key": "step_1",
                "name": "Step 1",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Research the topic.",
                "max_retries": 1,
                "timeout_seconds": 60,
            },
            {
                "key": "step_2",
                "name": "Step 2",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Summarize: {{prior.step_1}}",
                "order_index": 1,
                "max_retries": 0,
                "timeout_seconds": 60,
            },
        ],
    }
    if tags:
        payload["tags"] = tags
    return payload


async def create_published_mission(client: AsyncClient, tags=None) -> dict:
    response = await client.post("/api/v1/missions", json=llm_mission_payload(tags))
    assert response.status_code == 201, f"Failed to create mission: {response.text}"
    mission = response.json()
    publish_resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200, f"Failed to publish: {publish_resp.text}"
    return publish_resp.json()


async def create_run_via_api(client: AsyncClient, mission_id: str) -> dict:
    response = await client.post(
        "/api/v1/runs",
        json={"mission_id": mission_id, "input_context": {"topic": "AI agents"}},
    )
    assert response.status_code == 201, f"Failed to create run: {response.text}"
    return response.json()


def mock_llm_output(text: str = "Mocked LLM output") -> dict:
    return {
        "text": text,
        "model": "gpt-4o-mini",
        "prompt_tokens": 15,
        "completion_tokens": 8,
        "total_tokens": 23,
        "finish_reason": "stop",
    }


async def execute_mock_run(client: AsyncClient, mission_id: str) -> dict:
    """Create and execute a run with mocked LLM, returning the run dict."""
    run = await create_run_via_api(client, mission_id)
    mock_llm = AsyncMock(return_value=mock_llm_output())
    with patch("app.services.run_service.llm_service.call_llm", mock_llm):
        async with TestSessionFactory() as db:
            await execute_run(db, uuid.UUID(run["id"]))
    return run


async def create_eval_via_api(client: AsyncClient, payload: dict) -> dict:
    response = await client.post("/api/v1/evals", json=payload)
    assert response.status_code == 201, f"Failed to create eval: {response.text}"
    return response.json()


# ──────────────────────────────────────────────
# 1. Eval Definition CRUD + validation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_eval_definition(async_client: AsyncClient):
    """POST /evals + GET /evals/{id} round-trips the definition."""
    created = await create_eval_via_api(
        async_client,
        {
            "name": "Contains keyword",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["keyword"]},
            "threshold": 0.5,
            "tags": ["qa"],
        },
    )
    assert created["name"] == "Contains keyword"
    assert created["scope"] == "step"
    assert created["eval_type"] == "rule_based"
    assert created["target_step_key"] == "step_1"
    assert created["tags"] == ["qa"]

    resp = await async_client.get(f"/api/v1/evals/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_create_eval_requires_attachment(async_client: AsyncClient):
    """An eval with neither mission_id nor tags is rejected (400)."""
    response = await async_client.post(
        "/api/v1/evals",
        json={
            "name": "No attachment",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["x"]},
        },
    )
    assert response.status_code == 400
    assert "attach" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_eval_step_scope_requires_target(async_client: AsyncClient):
    """step/tool_span scope requires target_step_key (400)."""
    response = await async_client.post(
        "/api/v1/evals",
        json={
            "name": "No target",
            "scope": "step",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["x"]},
            "tags": ["qa"],
        },
    )
    assert response.status_code == 400
    assert "target_step_key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_eval_rule_based_requires_rule_and_terms(async_client: AsyncClient):
    """rule_based needs a valid rule and a non-empty terms list."""
    response = await async_client.post(
        "/api/v1/evals",
        json={
            "name": "Bad rule",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "banana", "terms": ["x"]},
            "tags": ["qa"],
        },
    )
    assert response.status_code == 400

    response = await async_client.post(
        "/api/v1/evals",
        json={
            "name": "No terms",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any"},
            "tags": ["qa"],
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_and_delete_eval_definition(async_client: AsyncClient):
    """PUT updates fields; DELETE removes the definition."""
    created = await create_eval_via_api(
        async_client,
        {
            "name": "Original",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["a"]},
            "tags": ["qa"],
        },
    )
    upd = await async_client.put(
        f"/api/v1/evals/{created['id']}",
        json={"name": "Renamed", "threshold": 0.8},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "Renamed"
    assert upd.json()["threshold"] == 0.8

    del_resp = await async_client.delete(f"/api/v1/evals/{created['id']}")
    assert del_resp.status_code == 204

    get_resp = await async_client.get(f"/api/v1/evals/{created['id']}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_eval_definitions(async_client: AsyncClient):
    await create_eval_via_api(
        async_client,
        {
            "name": "Eval A",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["a"]},
            "tags": ["qa"],
        },
    )
    resp = await async_client.get("/api/v1/evals")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "Eval A"


# ──────────────────────────────────────────────
# 2. Evaluators (unit)
# ──────────────────────────────────────────────


def _rule_def(rule: str, terms, **extra):
    return type(
        "Def",
        (),
        {"config": {"rule": rule, "terms": terms, **extra}, "threshold": 0.5},
    )()


def _schema_def(schema_doc, **extra):
    return type("Def", (), {"config": {"schema": schema_doc, **extra}, "threshold": 0.5})()


class TestRuleBased:
    def test_contains_any_match(self):
        verdict, score, evidence = evaluate_rule_based(
            _rule_def("contains_any", ["foo", "bar"]), {"text": "something foo here"}
        )
        assert verdict.value == "pass"
        assert score == 1.0
        assert evidence["matched"] == ["foo"]

    def test_contains_any_no_match(self):
        verdict, score, evidence = evaluate_rule_based(
            _rule_def("contains_any", ["foo", "bar"]), {"text": "nothing here"}
        )
        assert verdict.value == "fail"
        assert score == 0.0
        assert evidence["matched"] == []

    def test_contains_all_requires_all_terms(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_all", ["foo", "bar"]), {"text": "foo only"}
        )
        assert verdict.value == "fail"

        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_all", ["foo", "bar"]), {"text": "foo and bar"}
        )
        assert verdict.value == "pass"

    def test_not_contains(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("not_contains", ["forbidden"]), {"text": "all clear"}
        )
        assert verdict.value == "pass"

        verdict, _, _ = evaluate_rule_based(
            _rule_def("not_contains", ["forbidden"]), {"text": "forbidden word"}
        )
        assert verdict.value == "fail"

    def test_case_insensitive_by_default(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_any", ["HELLO"]), {"text": "say hello world"}
        )
        assert verdict.value == "pass"

    def test_case_sensitive(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_any", ["HELLO"], case_sensitive=True),
            {"text": "say hello world"},
        )
        assert verdict.value == "fail"

    def test_exact_match_mode(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_any", ["hello"], match_mode="exact", field="text"),
            {"text": "hello world"},
        )
        assert verdict.value == "fail"

        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_any", ["hello"], match_mode="exact", field="text"),
            {"text": "hello"},
        )
        assert verdict.value == "pass"

    def test_field_resolution(self):
        verdict, _, _ = evaluate_rule_based(
            _rule_def("contains_any", ["needle"], field="result.message"),
            {"result": {"message": "found needle here"}},
        )
        assert verdict.value == "pass"


class TestSchema:
    def test_valid_artifact_passes(self):
        schema_doc = {
            "type": "object",
            "required": ["title", "score"],
            "properties": {
                "title": {"type": "string"},
                "score": {"type": "number", "minimum": 0},
            },
        }
        verdict, score, evidence = evaluate_schema(
            _schema_def(schema_doc), {"title": "OK", "score": 5}
        )
        assert verdict.value == "pass"
        assert score == 1.0
        assert evidence["error_count"] == 0

    def test_invalid_artifact_fails_with_errors(self):
        schema_doc = {
            "type": "object",
            "required": ["title", "score"],
            "properties": {
                "title": {"type": "string"},
                "score": {"type": "number", "minimum": 0},
            },
        }
        verdict, score, evidence = evaluate_schema(
            _schema_def(schema_doc), {"title": 123, "score": -5}
        )
        assert verdict.value == "fail"
        assert score == 0.0
        assert evidence["error_count"] >= 2

    def test_missing_schema_config_raises(self):
        with pytest.raises(Exception):
            evaluate_schema(_schema_def(None), {"a": 1})


# ──────────────────────────────────────────────
# 3. End-to-end eval execution against a run
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_evals_by_mission_id(async_client: AsyncClient):
    """Rule-based eval attached by mission_id evaluates run output."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Step1 has output",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["triggered"] is True
    assert data["skipped"] is False
    assert data["evaluated"] == 1
    assert data["results"][0]["verdict"] == "pass"
    assert data["results"][0]["eval_name"] == "Step1 has output"

    results = await async_client.get(f"/api/v1/runs/{run['id']}/evals")
    assert results.status_code == 200
    assert len(results.json()) == 1


@pytest.mark.asyncio
async def test_run_evals_by_tag_attachment(async_client: AsyncClient):
    """An eval attached by tag applies to runs of tagged missions."""
    mission = await create_published_mission(async_client, tags=["critical"])
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Tag-scoped eval",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "tags": ["critical"],
        },
    )

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    assert resp.json()["evaluated"] == 1
    assert resp.json()["results"][0]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_run_evals_no_definitions_skips(async_client: AsyncClient):
    """No attached evals -> 200 with skipped=True and zero results."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] is True
    assert data["evaluated"] == 0
    assert data["reason"] is not None


@pytest.mark.asyncio
async def test_run_evals_requires_terminal_state(async_client: AsyncClient):
    """POST evals/run on a pending run -> 400."""
    mission = await create_published_mission(async_client)
    run = await create_run_via_api(async_client, mission["id"])

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 400
    assert "terminal" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_run_evals_creates_eval_spans(async_client: AsyncClient):
    """Each eval application creates a kind='eval' span under the run span."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Span check",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )
    await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")

    async with TestSessionFactory() as db:
        run_span = (
            await db.execute(
                select(Span).where(Span.run_id == uuid.UUID(run["id"]), Span.kind == "run")
            )
        ).scalar_one()
        eval_spans = (
            await db.execute(
                select(Span).where(
                    Span.run_id == uuid.UUID(run["id"]),
                    Span.kind == "eval",
                    Span.parent_span_id == run_span.id,
                )
            )
        ).scalars().all()
        assert len(eval_spans) == 1
        assert eval_spans[0].span_type == "eval"
        assert eval_spans[0].output_json["verdict"] == "pass"


@pytest.mark.asyncio
async def test_run_evals_missing_target_step_fails_with_evidence(async_client: AsyncClient):
    """Eval targeting a step key that does not exist -> fail result + evidence."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Ghost step",
            "scope": "step",
            "target_step_key": "does_not_exist",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["x"]},
            "mission_id": mission["id"],
        },
    )

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["verdict"] == "fail"
    assert "No artifacts found" in result["evidence"]["error"]


@pytest.mark.asyncio
async def test_run_evals_schema_type_end_to_end(async_client: AsyncClient):
    """A schema eval passes for a conforming step output and fails otherwise."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    schema_doc = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    await create_eval_via_api(
        async_client,
        {
            "name": "Output is object with text",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "schema",
            "config": {"schema": schema_doc},
            "mission_id": mission["id"],
        },
    )

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    assert resp.json()["results"][0]["verdict"] == "pass"

    await async_client.post(
        "/api/v1/evals",
        json={
            "name": "Requires missing field",
            "scope": "step",
            "target_step_key": "step_1",
            "eval_type": "schema",
            "config": {
                "schema": {
                    "type": "object",
                    "properties": {"impossible": {"type": "string"}},
                    "required": ["impossible"],
                }
            },
            "mission_id": mission["id"],
        },
    )
    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    results = resp.json()["results"]
    failing = [r for r in results if r["eval_name"] == "Requires missing field"]
    assert failing and failing[0]["verdict"] == "fail"
    assert failing[0]["evidence"]["error_count"] >= 1


@pytest.mark.asyncio
async def test_run_evals_rerun_appends_history(async_client: AsyncClient):
    """Re-running evals appends new rows; the run itself is never re-executed."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "History eval",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )

    first = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    second = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert first.json()["evaluated"] == 1
    assert second.json()["evaluated"] == 1

    results = await async_client.get(f"/api/v1/runs/{run['id']}/evals")
    assert len(results.json()) == 2


@pytest.mark.asyncio
async def test_delete_eval_cascades_to_results(async_client: AsyncClient):
    """Deleting an eval definition removes its result rows."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    eval_def = await create_eval_via_api(
        async_client,
        {
            "name": "Cascade eval",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )
    await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")

    async with TestSessionFactory() as db:
        before = (
            await db.execute(
                select(EvalResult).where(EvalResult.eval_id == uuid.UUID(eval_def["id"]))
            )
        ).scalars().all()
        assert len(before) == 1

    await async_client.delete(f"/api/v1/evals/{eval_def['id']}")

    async with TestSessionFactory() as db:
        remaining = (
            await db.execute(
                select(EvalResult).where(EvalResult.eval_id == uuid.UUID(eval_def["id"]))
            )
        ).scalars().all()
        assert len(remaining) == 0
        assert await db.get(EvalDefinition, uuid.UUID(eval_def["id"])) is None
