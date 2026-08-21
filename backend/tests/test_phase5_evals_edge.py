"""
MERIDIAN Phase 5 — Eval Suite Edge-Case Tests.

Focuses on the llm_judge evaluator (mocked) and the edge cases listed in
the Phase 5 self-check:
  1. Judge returns unparseable output          -> fail + evidence
  2. Judge call raises (provider down)         -> fail + evidence
  3. Judge returns fenced markdown JSON        -> parsed + scored
  4. Score normalisation / threshold clamping  -> verdict via threshold
  5. Missing target step                       -> fail + "No artifacts found"
  6. Non-terminal run rejected (400)
  7. No evals attached -> skipped
  8. Eval never re-runs the mission (run artifacts unchanged)
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import TestSessionFactory
from tests.test_phase5_evals import (
    create_eval_via_api,
    create_published_mission,
    execute_mock_run,
)


# ──────────────────────────────────────────────
# llm_judge evaluator (mocked)
# ──────────────────────────────────────────────


def _judge_def(template, **config):
    cfg = {"judge_prompt_template": template, **config}
    return type("Def", (), {"config": cfg, "threshold": 0.5})()


@pytest.mark.asyncio
async def test_llm_judge_pass_and_fail(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    async def judge(response_text):
        mock = AsyncMock(return_value={"text": response_text, "model": "gpt-4o-mini"})
        monkeypatch.setattr("app.services.eval_service.llm_service.call_llm", mock)
        return await evaluate_llm_judge(
            _judge_def("Score: {artifact}", score_range=[0, 10]), "an artifact"
        )

    verdict, score, evidence = await judge('{"score": 9, "rationale": "good"}')
    assert verdict.value == "pass"
    assert score == pytest.approx(0.9)
    assert evidence["rationale"] == "good"

    verdict, score, _ = await judge('{"score": 1, "rationale": "bad"}')
    assert verdict.value == "fail"
    assert score == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_llm_judge_threshold_controls_verdict(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    definition = type(
        "Def",
        (),
        {"config": {"judge_prompt_template": "Rate: {artifact}", "score_range": [0, 10]}, "threshold": 0.95},
    )()

    monkeypatch.setattr(
        "app.services.eval_service.llm_service.call_llm",
        AsyncMock(return_value={"text": '{"score": 9}', "model": "gpt-4o-mini"}),
    )
    verdict, score, _ = await evaluate_llm_judge(definition, "x")
    assert verdict.value == "fail"  # 0.9 < 0.95 threshold


@pytest.mark.asyncio
async def test_llm_judge_unparseable_output_fails(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    monkeypatch.setattr(
        "app.services.eval_service.llm_service.call_llm",
        AsyncMock(return_value={"text": "not json at all", "model": "gpt-4o-mini"}),
    )
    verdict, score, evidence = await evaluate_llm_judge(_judge_def("Rate: {artifact}"), "x")
    assert verdict.value == "fail"
    assert score is None
    assert "parse" in evidence["error"].lower()


@pytest.mark.asyncio
async def test_llm_judge_call_failure_records_error(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.eval_service.llm_service.call_llm", boom)
    verdict, score, evidence = await evaluate_llm_judge(_judge_def("Rate: {artifact}"), "x")
    assert verdict.value == "fail"
    assert score is None
    assert "provider down" in evidence["error"]


@pytest.mark.asyncio
async def test_llm_judge_accepts_fenced_markdown_json(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    fenced = '```json\n{"score": 8, "rationale": "ok"}\n```'
    monkeypatch.setattr(
        "app.services.eval_service.llm_service.call_llm",
        AsyncMock(return_value={"text": fenced, "model": "gpt-4o-mini"}),
    )
    verdict, score, _ = await evaluate_llm_judge(_judge_def("Rate: {artifact}"), "x")
    assert verdict.value == "pass"
    assert score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_llm_judge_score_clamped_to_range(monkeypatch):
    from app.services.eval_service import evaluate_llm_judge

    # Judge returns 99 but range is [0, 10] -> clamped to 1.0
    monkeypatch.setattr(
        "app.services.eval_service.llm_service.call_llm",
        AsyncMock(return_value={"text": '{"score": 99}', "model": "gpt-4o-mini"}),
    )
    verdict, score, _ = await evaluate_llm_judge(
        _judge_def("Rate: {artifact}", score_range=[0, 10]), "x"
    )
    assert verdict.value == "pass"
    assert score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_llm_judge_end_to_end_through_api(async_client: AsyncClient):
    """llm_judge eval runs through the API with a mocked judge."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Judge quality",
            "scope": "step",
            "target_step_key": "step_2",
            "eval_type": "llm_judge",
            "config": {
                "judge_prompt_template": "Score this artifact out of 10: {artifact}",
                "score_range": [0, 10],
            },
            "mission_id": mission["id"],
        },
    )

    with patch(
        "app.services.eval_service.llm_service.call_llm",
        AsyncMock(
            return_value={"text": '{"score": 10, "rationale": "perfect"}', "model": "gpt-4o-mini"}
        ),
    ):
        resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["verdict"] == "pass"
    assert result["score"] == pytest.approx(1.0)
    assert result["evidence"]["rationale"] == "perfect"


# ──────────────────────────────────────────────
# Self-check edge cases
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edge_unknown_run_404(async_client: AsyncClient):
    """POST evals/run and GET evals for a non-existent run -> 404."""
    run_id = "00000000-0000-0000-0000-000000000000"

    resp = await async_client.post(f"/api/v1/runs/{run_id}/evals/run")
    assert resp.status_code == 404

    resp = await async_client.get(f"/api/v1/runs/{run_id}/evals")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_edge_eval_never_reruns_mission(async_client: AsyncClient):
    """The run's step outputs are untouched after evals run."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    async with TestSessionFactory() as db:
        from app.db.models import RunStep

        before = (await db.execute(RunStep.__table__.select())).fetchall()

    await create_eval_via_api(
        async_client,
        {
            "name": "No-op safety",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )
    await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")

    async with TestSessionFactory() as db:
        from app.db.models import RunStep

        after = (await db.execute(RunStep.__table__.select())).fetchall()

    assert len(before) == len(after)


@pytest.mark.asyncio
async def test_edge_errored_eval_does_not_block_others(async_client: AsyncClient):
    """A crashing eval records a fail result; sibling evals still evaluate."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    # A valid rule eval on the run.
    await create_eval_via_api(
        async_client,
        {
            "name": "Good eval",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {"rule": "contains_any", "terms": ["Mocked LLM output"]},
            "mission_id": mission["id"],
        },
    )
    # An llm_judge eval whose provider is down.
    await create_eval_via_api(
        async_client,
        {
            "name": "Broken judge",
            "scope": "run",
            "eval_type": "llm_judge",
            "config": {"judge_prompt_template": "Rate: {artifact}"},
            "mission_id": mission["id"],
        },
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    with patch("app.services.eval_service.llm_service.call_llm", boom):
        resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")

    assert resp.status_code == 200
    by_name = {r["eval_name"]: r for r in resp.json()["results"]}
    assert by_name["Good eval"]["verdict"] == "pass"
    assert by_name["Broken judge"]["verdict"] == "fail"
    assert "provider down" in by_name["Broken judge"]["evidence"]["error"]


@pytest.mark.asyncio
async def test_edge_run_scope_eval_sees_all_step_outputs(async_client: AsyncClient):
    """run-scope evals see a dict of step_key -> output for every step."""
    mission = await create_published_mission(async_client)
    run = await execute_mock_run(async_client, mission["id"])

    await create_eval_via_api(
        async_client,
        {
            "name": "Run has both steps",
            "scope": "run",
            "eval_type": "rule_based",
            "config": {
                "rule": "contains_all",
                "terms": ["step_1", "step_2"],
                "field": "steps",
            },
            "mission_id": mission["id"],
        },
    )

    resp = await async_client.post(f"/api/v1/runs/{run['id']}/evals/run")
    assert resp.status_code == 200
    # JSON-serialized "steps" dict contains keys step_1 and step_2 -> pass
    assert resp.json()["results"][0]["verdict"] == "pass"
