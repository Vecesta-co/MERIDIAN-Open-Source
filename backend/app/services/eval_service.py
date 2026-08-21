"""
MERIDIAN Eval Service — Phase 5 Eval Suite.

Evaluates run artifacts (run output, step outputs, tool spans) against
eval definitions attached to a mission (by mission_id or tag overlap).

Design constraints (Phase 5):
  - Evals NEVER block mission execution. They run post-run via the worker
    hook, and the manual rerun endpoint only evaluates runs already in a
    terminal state.
  - Eval types are limited to: rule_based, schema, llm_judge.
  - Re-running evals on historical runs appends NEW result rows; prior
    results are preserved (audit history).
"""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import EvalDefinition, EvalResult, Mission, Run, RunStep, Span, Step
from app.models.schemas import (
    EvalDefinitionCreate,
    EvalDefinitionResponse,
    EvalDefinitionUpdate,
    EvalResultResponse,
    EvalRunResponse,
    EvalScope,
    EvalType,
    EvalVerdict,
)
from app.services import llm_service

logger = get_logger(__name__)

TERMINAL_RUN_STATES = ("completed", "failed", "cancelled", "timed_out")

RULE_OPERATORS = ("contains_any", "contains_all", "not_contains")


class EvalValidationError(Exception):
    """Raised for invalid eval definitions / invalid eval requests.

    Carries an HTTP status code so the API layer can map it to the
    correct response (400 for validation, 404 for missing resources).
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ──────────────────────────────────────────────
# Artifact helpers
# ──────────────────────────────────────────────


def _resolve_field(value: Any, field: Optional[str]) -> Any:
    """Resolve a dot-notation field path against an artifact."""
    if not field:
        return value
    current = value
    for part in field.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return _json_dumps(value)
    return str(value)


async def _run_output_map(db: AsyncSession, run: Run) -> Dict[str, Any]:
    """Map step_key -> step output for a run (run-scope artifact)."""
    result = await db.execute(
        select(RunStep, Step)
        .join(Step, RunStep.step_id == Step.id)
        .where(RunStep.run_id == run.id)
    )
    outputs: Dict[str, Any] = {}
    for rs, step in result.all():
        if rs.output_json is not None:
            outputs[step.step_key] = rs.output_json
    return {
        "run_id": str(run.id),
        "status": run.status,
        "steps": outputs,
    }


async def _get_step_by_key(
    db: AsyncSession,
    step_key: Optional[str],
    mission_version_id: uuid.UUID,
) -> Optional[Step]:
    if not step_key:
        return None
    result = await db.execute(
        select(Step).where(
            Step.mission_version_id == mission_version_id,
            Step.step_key == step_key,
        )
    )
    return result.scalar_one_or_none()


async def _get_run_step(
    db: AsyncSession, run_id: uuid.UUID, step_id: uuid.UUID
) -> Optional[RunStep]:
    result = await db.execute(
        select(RunStep).where(
            RunStep.run_id == run_id,
            RunStep.step_id == step_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_tool_spans(
    db: AsyncSession, run_id: uuid.UUID, step_id: uuid.UUID
) -> List[Span]:
    result = await db.execute(
        select(Span).where(
            Span.run_id == run_id,
            Span.step_id == step_id,
            Span.kind == "tool",
        )
    )
    return list(result.scalars().all())


async def _get_run_span(db: AsyncSession, run_id: uuid.UUID) -> Optional[Span]:
    result = await db.execute(
        select(Span).where(Span.run_id == run_id, Span.kind == "run")
    )
    return result.scalar_one_or_none()


async def _select_artifacts(
    db: AsyncSession, run: Run, definition: EvalDefinition
) -> List[Dict[str, Any]]:
    """
    Return the artifacts an eval definition applies to.

    Each entry: {"artifact": ..., "step_id": ...|None, "span_id": ...|None}
    """
    if definition.scope == EvalScope.RUN:
        artifact = await _run_output_map(db, run)
        return [{"artifact": artifact, "step_id": None, "span_id": None}]

    step = await _get_step_by_key(
        db, definition.target_step_key, run.mission_version_id
    )
    if step is None:
        return []
    rs = await _get_run_step(db, run.id, step.id)
    if rs is None:
        return []

    if definition.scope == EvalScope.STEP:
        return [
            {
                "artifact": rs.output_json if rs.output_json is not None else {},
                "step_id": step.id,
                "span_id": rs.span_id,
            }
        ]

    # tool_span scope: one artifact per tool span recorded under the step.
    tool_spans = await _get_tool_spans(db, run.id, step.id)
    artifacts = []
    for sp in tool_spans:
        artifacts.append(
            {
                "artifact": {
                    "tool_name": (sp.input_json or {}).get("tool_name"),
                    "ok": sp.status == "ok",
                    "output": sp.output_json,
                },
                "step_id": step.id,
                "span_id": sp.id,
            }
        )
    return artifacts


# ──────────────────────────────────────────────
# Evaluators
# ──────────────────────────────────────────────


def evaluate_rule_based(
    definition: EvalDefinition, artifact: Any
) -> Tuple[EvalVerdict, Optional[float], Dict[str, Any]]:
    """Rule-based eval: contains_any | contains_all | not_contains.

    config:
      rule: one of contains_any|contains_all|not_contains (required)
      terms: list of strings (required)
      field: dot-notation path into the artifact (optional; defaults to
             the whole artifact)
      case_sensitive: bool (default False)
      match_mode: "substring" (default) | "exact"
    """
    config = definition.config or {}
    rule = config.get("rule")
    terms = config.get("terms") or []
    field = config.get("field")
    case_sensitive = bool(config.get("case_sensitive", False))
    match_mode = config.get("match_mode", "substring")

    target = _stringify(_resolve_field(artifact, field))
    if not case_sensitive:
        target = target.lower()
        terms = [str(t).lower() for t in terms]

    def _matches(term: str) -> bool:
        if match_mode == "exact":
            return term == target
        return term in target

    matched = [t for t in terms if _matches(t)]

    # Compute a match ratio in [0.0, 1.0] for threshold gating.
    if rule == "contains_any":
        match_ratio = len(matched) / len(terms) if terms else 0.0
    elif rule == "contains_all":
        match_ratio = len(matched) / len(terms) if terms else 1.0
    elif rule == "not_contains":
        match_ratio = 1.0 if not matched else 0.0
    else:
        match_ratio = 0.5

    # Use the definition's threshold to gate the verdict.
    # When threshold equals the default (0.5) the behaviour is identical
    # to the original binary satisfied check; otherwise the ratio is
    # compared against the configured threshold.
    threshold = definition.threshold
    if threshold == 0.5:
        # Default behaviour: satisfied decides the verdict.
        satisfied = (
            rule == "contains_any" and bool(matched)
            or rule == "contains_all" and len(matched) == len(terms) and bool(terms)
            or rule == "not_contains" and not matched
        )
        verdict = EvalVerdict.PASS if satisfied else EvalVerdict.FAIL
        score = 1.0 if satisfied else 0.0
    else:
        # Ratio-based gating.
        verdict = EvalVerdict.PASS if match_ratio >= threshold else EvalVerdict.FAIL
        score = match_ratio

    evidence = {
        "rule": rule,
        "terms": terms,
        "matched": matched,
        "field": field,
        "match_mode": match_mode,
        "case_sensitive": case_sensitive,
        "match_ratio": match_ratio,
        "threshold": threshold,
    }
    return verdict, score, evidence


def evaluate_schema(
    definition: EvalDefinition, artifact: Any
) -> Tuple[EvalVerdict, Optional[float], Dict[str, Any]]:
    """JSON Schema validation eval.

    config:
      schema: JSON Schema document (draft 7+) (required)
      field: dot-notation path into the artifact (optional)
    """
    config = definition.config or {}
    schema_doc = config.get("schema")
    if not isinstance(schema_doc, dict):
        raise EvalValidationError(
            "schema eval config must include a JSON Schema document under 'schema'"
        )

    target = _resolve_field(artifact, config.get("field"))
    errors: List[str] = []
    try:
        from jsonschema import Draft7Validator, FormatChecker

        validator = Draft7Validator(schema=schema_doc, format_checker=FormatChecker())
        for err in sorted(validator.iter_errors(target), key=lambda e: list(e.path)):
            errors.append(err.message)
    except Exception as exc:
        raise EvalValidationError(f"Schema validation failed to run: {exc}") from exc

    verdict = EvalVerdict.PASS if not errors else EvalVerdict.FAIL
    score = 1.0 if not errors else 0.0
    evidence = {
        "field": config.get("field"),
        "error_count": len(errors),
        "errors": errors[:50],
    }
    return verdict, score, evidence


async def evaluate_llm_judge(
    definition: EvalDefinition, artifact: Any
) -> Tuple[EvalVerdict, Optional[float], Dict[str, Any]]:
    """LLM-as-judge eval.

    config:
      judge_prompt_template: template containing {artifact} and/or {rubric}
                             (required)
      judge_model: model name (optional; defaults to LITELLM_MODEL)
      rubric: rubric text (optional)
      score_range: [min, max] ints (default [0, 10])

    The judge must return JSON: {"score": <int>, "rationale": "..."}.
    The raw score is normalised to 0..1 and the verdict uses the
    definition threshold.
    """
    config = definition.config or {}
    template = config.get("judge_prompt_template")
    if not template or not isinstance(template, str):
        raise EvalValidationError(
            "llm_judge config must include 'judge_prompt_template'"
        )

    rubric = config.get("rubric") or ""
    artifact_str = _stringify(artifact)
    if len(artifact_str) > settings.EVAL_MAX_ARTIFACT_CHARS:
        artifact_str = (
            artifact_str[: settings.EVAL_MAX_ARTIFACT_CHARS]
            + "\n...[truncated]"
        )

    prompt = template.replace("{artifact}", artifact_str).replace("{rubric}", rubric)

    try:
        response = await llm_service.call_llm(
            prompt=prompt,
            model=config.get("judge_model") or settings.LITELLM_MODEL,
            temperature=0.0,
            max_tokens=settings.EVAL_LLM_MAX_TOKENS,
            timeout_seconds=settings.EVAL_LLM_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        verdict = EvalVerdict.FAIL
        evidence = {"error": f"llm_judge call failed: {exc}", "prompt": prompt}
        return verdict, None, evidence

    text = (response.get("text") or "").strip()
    raw_score, rationale, parse_error = _parse_judge_response(text)
    if raw_score is None:
        return EvalVerdict.FAIL, None, {
            "error": f"Could not parse judge score: {parse_error}",
            "raw_text": text[:2000],
            "prompt": prompt,
        }

    score_min, score_max = _score_range(config)
    score = _normalise_score(raw_score, score_min, score_max)
    verdict = EvalVerdict.PASS if score >= definition.threshold else EvalVerdict.FAIL
    evidence = {
        "raw_score": raw_score,
        "score_range": [score_min, score_max],
        "rationale": rationale,
        "model": response.get("model"),
    }
    return verdict, score, evidence


def _score_range(config: Dict[str, Any]) -> Tuple[int, int]:
    rng = config.get("score_range") or [0, 10]
    try:
        lo, hi = int(rng[0]), int(rng[1])
    except (ValueError, TypeError, IndexError):
        return 0, 10
    if hi <= lo:
        return 0, 10
    return lo, hi


def _normalise_score(raw: int, lo: int, hi: int) -> float:
    raw_clamped = max(lo, min(hi, raw))
    return round((raw_clamped - lo) / (hi - lo), 4)


def _parse_judge_response(text: str) -> Tuple[Optional[int], str, Optional[str]]:
    """Parse judge output. Tolerates markdown code fences and prose."""
    if not text:
        return None, "", "empty response"
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict) and "score" in payload:
            score = int(payload["score"])
            return score, str(payload.get("rationale") or ""), None
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Fallback: extract a lone integer near the word "score"
    match = re.search(r'"score"\s*:\s*(\d+)', text)
    if match:
        return int(match.group(1)), "", None
    match = re.search(r"score[:\s]+(\d+)", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)), "", None
    return None, "", "no numeric score found in response"


# ──────────────────────────────────────────────
# Attachment matching
# ──────────────────────────────────────────────


async def _load_applicable_definitions(
    db: AsyncSession, run: Run
) -> List[EvalDefinition]:
    """Eval definitions attached to the run's mission.

    An eval applies if:
      - its mission_id equals the run's mission_id, OR
      - its tags intersect the mission's tags.
    """
    mission_tags: set = set()
    if run.mission_id is not None:
        mission = await db.get(Mission, run.mission_id)
        if mission is not None:
            mission_tags = set(mission.tags or [])

    result = await db.execute(select(EvalDefinition))
    definitions = list(result.scalars().all())

    applicable = []
    for definition in definitions:
        if (
            definition.mission_id is not None
            and run.mission_id is not None
            and definition.mission_id == run.mission_id
        ):
            applicable.append(definition)
            continue
        eval_tags = set(definition.tags or [])
        if eval_tags and eval_tags.intersection(mission_tags):
            applicable.append(definition)
    return applicable


# ──────────────────────────────────────────────
# Eval span + result recording
# ──────────────────────────────────────────────


async def _record_result(
    db: AsyncSession,
    run: Run,
    definition: EvalDefinition,
    item: Dict[str, Any],
    run_span: Optional[Span],
    verdict: EvalVerdict,
    score: Optional[float],
    evidence: Dict[str, Any],
    errored: bool = False,
) -> EvalResult:
    start = _now()
    eval_span = Span(
        run_id=run.id,
        step_id=item.get("step_id"),
        parent_span_id=run_span.id if run_span is not None else None,
        kind="eval",
        span_type="eval",
        name=f"eval:{definition.name}",
        status="error" if errored else "ok",
        start_time=start,
        end_time=start,
        duration_ms=0,
        input_json={
            "eval_id": str(definition.id),
            "eval_type": definition.eval_type,
            "scope": definition.scope,
            "target_step_key": definition.target_step_key,
            "threshold": definition.threshold,
        },
        output_json={"verdict": verdict.value, "score": score},
        error_json={"error": evidence.get("error")} if errored else None,
        meta_json={
            "eval_name": definition.name,
            "eval_type": definition.eval_type,
            "threshold": definition.threshold,
        },
    )
    db.add(eval_span)
    await db.flush()

    result = EvalResult(
        id=uuid.uuid4(),
        eval_id=definition.id,
        run_id=run.id,
        step_id=item.get("step_id"),
        span_id=eval_span.id,
        verdict=verdict,
        score=score,
        evidence=evidence,
    )
    db.add(result)
    await db.flush()

    # Fire webhook if configured (best-effort, never blocks the eval run).
    if definition.webhook_url:
        import httpx

        payload = {
            "eval_id": str(definition.id),
            "run_id": str(run.id),
            "verdict": verdict.value,
            "score": score,
            "evidence": evidence,
            "name": definition.name,
        }
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(definition.webhook_url, json=payload)
        except (httpx.ConnectError, httpx.HTTPError, httpx.ReadTimeout, Exception):
            pass  # never block the eval execution

    return result


async def _evaluate_definition(
    db: AsyncSession,
    run: Run,
    definition: EvalDefinition,
    run_span: Optional[Span],
) -> List[EvalResult]:
    artifacts = await _select_artifacts(db, run, definition)
    results: List[EvalResult] = []

    if not artifacts:
        missing = "target_step_key" if definition.scope != EvalScope.RUN else "run"
        results.append(
            await _record_result(
                db,
                run,
                definition,
                {"step_id": None, "span_id": None},
                run_span,
                EvalVerdict.FAIL,
                None,
                {
                    "error": f"No artifacts found for eval (missing {missing})",
                    "target_step_key": definition.target_step_key,
                },
                errored=False,
            )
        )
        return results

    for item in artifacts:
        try:
            if definition.eval_type == EvalType.RULE_BASED:
                verdict, score, evidence = evaluate_rule_based(
                    definition, item["artifact"]
                )
            elif definition.eval_type == EvalType.SCHEMA:
                verdict, score, evidence = evaluate_schema(
                    definition, item["artifact"]
                )
            elif definition.eval_type == EvalType.LLM_JUDGE:
                verdict, score, evidence = await evaluate_llm_judge(
                    definition, item["artifact"]
                )
            else:
                raise EvalValidationError(
                    f"Unknown eval_type: {definition.eval_type}"
                )
            results.append(
                await _record_result(
                    db,
                    run,
                    definition,
                    item,
                    run_span,
                    verdict,
                    score,
                    evidence,
                    errored=False,
                )
            )
        except EvalValidationError as exc:
            results.append(
                await _record_result(
                    db,
                    run,
                    definition,
                    item,
                    run_span,
                    EvalVerdict.FAIL,
                    None,
                    {"error": str(exc)},
                    errored=True,
                )
            )
        except Exception as exc:
            logger.exception(
                "Eval %s crashed while evaluating run %s: %s",
                definition.name,
                run.id,
                exc,
            )
            results.append(
                await _record_result(
                    db,
                    run,
                    definition,
                    item,
                    run_span,
                    EvalVerdict.FAIL,
                    None,
                    {"error": f"eval crashed: {exc}"},
                    errored=True,
                )
            )
    return results


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────


async def run_evals_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    require_terminal: bool = True,
) -> EvalRunResponse:
    """Evaluate a run against all attached eval definitions.

    Non-blocking by design: it only reads already-produced run artifacts
    and writes new eval_result rows + eval trace spans. Never re-runs the
    mission.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise EvalValidationError(f"Run {run_id} not found", status_code=404)

    if require_terminal and run.status not in TERMINAL_RUN_STATES:
        raise EvalValidationError(
            f"Run {run_id} is not in a terminal state (status={run.status}). "
            "Evals can only run against completed/failed/cancelled/timed_out runs."
        )

    definitions = await _load_applicable_definitions(db, run)
    if not definitions:
        return EvalRunResponse(
            run_id=run.id,
            triggered=True,
            skipped=True,
            reason="No eval definitions attached to this mission or its tags",
            evaluated=0,
            results=[],
        )

    run_span = await _get_run_span(db, run.id)
    all_results: List[EvalResult] = []
    for definition in definitions:
        all_results.extend(await _evaluate_definition(db, run, definition, run_span))

    await db.commit()

    # Build responses from the actual EvalResult rows produced above, so
    # each entry carries its real eval_id/run_id/verdict/evidence. The
    # placeholder path (_result_to_response(None, ...)) produced None ids
    # which failed the response schema.
    name_by_id = {d.id: d.name for d in definitions}
    responses: List[EvalResultResponse] = [
        _result_to_response(r, eval_name=name_by_id.get(r.eval_id))
        for r in all_results
    ]

    logger.info(
        "Evaluated run %s with %d eval definitions -> %d results",
        run.id,
        len(definitions),
        len(responses),
    )
    return EvalRunResponse(
        run_id=run.id,
        triggered=True,
        skipped=False,
        evaluated=len(responses),
        results=responses,
    )


async def rerun_evals_for_run(
    db: AsyncSession, run_id: uuid.UUID
) -> EvalRunResponse:
    """Manually re-run evals on a historical run (requires terminal state)."""
    return await run_evals_for_run(db, run_id, require_terminal=True)


async def get_run_eval_results(
    db: AsyncSession, run_id: uuid.UUID
) -> List[EvalResultResponse]:
    """All eval results for a run, newest first, with eval names."""
    run = await db.get(Run, run_id)
    if run is None:
        raise EvalValidationError(f"Run {run_id} not found", status_code=404)

    result = await db.execute(
        select(EvalResult)
        .where(EvalResult.run_id == run_id)
        .options(selectinload(EvalResult.eval_definition))
        .order_by(EvalResult.created_at.desc())
    )
    rows = list(result.scalars().all())
    return [_result_to_response(r) for r in rows]


def _result_to_response(
    result: Optional[EvalResult], eval_name: Optional[str] = None
) -> EvalResultResponse:
    if eval_name is None:
        eval_name = "unnamed_eval"
    if result is None:
        return EvalResultResponse(
            id=uuid.uuid4(),
            eval_id=None,
            eval_name=eval_name,
            run_id=None,
            step_id=None,
            span_id=None,
            verdict=EvalVerdict.FAIL,
            score=None,
            evidence={"error": "no result"},
            created_at=datetime.utcnow(),
        )
    if eval_name is None and result.eval_definition is not None:
        eval_name = result.eval_definition.name
    return EvalResultResponse(
        id=result.id,
        eval_id=result.eval_id,
        eval_name=eval_name,
        run_id=result.run_id,
        step_id=result.step_id,
        span_id=result.span_id,
        verdict=EvalVerdict(result.verdict),
        score=result.score,
        evidence=result.evidence,
        created_at=result.created_at,
    )


# ──────────────────────────────────────────────
# CRUD for eval definitions
# ──────────────────────────────────────────────


async def list_eval_definitions(db: AsyncSession) -> List[EvalDefinition]:
    result = await db.execute(
        select(EvalDefinition).order_by(EvalDefinition.created_at.desc())
    )
    return list(result.scalars().all())


async def get_eval_definition(
    db: AsyncSession, eval_id: uuid.UUID
) -> Optional[EvalDefinition]:
    return await db.get(EvalDefinition, eval_id)


async def create_eval_definition(
    db: AsyncSession, data: EvalDefinitionCreate
) -> EvalDefinition:
    _validate_definition(data)
    definition = EvalDefinition(
        id=uuid.uuid4(),
        name=data.name,
        scope=data.scope,
        target_step_key=data.target_step_key,
        eval_type=data.eval_type,
        config=data.config or {},
        threshold=data.threshold,
        mission_id=data.mission_id,
        tags=data.tags or [],
    )
    db.add(definition)
    await db.commit()
    await db.refresh(definition)
    logger.info("Created eval definition '%s' (id=%s)", definition.name, definition.id)
    return definition


async def update_eval_definition(
    db: AsyncSession, eval_id: uuid.UUID, data: EvalDefinitionUpdate
) -> EvalDefinition:
    definition = await db.get(EvalDefinition, eval_id)
    if definition is None:
        raise EvalValidationError(f"Eval definition {eval_id} not found", status_code=404)

    if data.name is not None:
        definition.name = data.name
    if data.config is not None:
        definition.config = data.config
    if data.threshold is not None:
        definition.threshold = data.threshold
    if data.mission_id is not None:
        definition.mission_id = data.mission_id
    if data.tags is not None:
        definition.tags = data.tags

    _validate_definition(
        EvalDefinitionCreate(
            name=definition.name,
            scope=EvalScope(definition.scope),
            target_step_key=definition.target_step_key,
            eval_type=EvalType(definition.eval_type),
            config=definition.config,
            threshold=definition.threshold,
            mission_id=definition.mission_id,
            tags=definition.tags,
        )
    )
    await db.commit()
    await db.refresh(definition)
    return definition


async def delete_eval_definition(
    db: AsyncSession, eval_id: uuid.UUID
) -> bool:
    definition = await db.get(EvalDefinition, eval_id)
    if definition is None:
        raise EvalValidationError(f"Eval definition {eval_id} not found", status_code=404)
    await db.delete(definition)
    await db.commit()
    return True


def _validate_definition(data: EvalDefinitionCreate) -> None:
    if data.scope in (EvalScope.STEP, EvalScope.TOOL_SPAN) and not data.target_step_key:
        raise EvalValidationError(
            f"target_step_key is required when scope is '{data.scope}'"
        )
    if data.scope == EvalScope.RUN and data.target_step_key:
        raise EvalValidationError(
            "target_step_key must not be set when scope is 'run'"
        )

    config = data.config or {}

    if data.eval_type == EvalType.RULE_BASED:
        rule = config.get("rule")
        if rule not in RULE_OPERATORS:
            raise EvalValidationError(
                f"rule_based config 'rule' must be one of {RULE_OPERATORS}"
            )
        if not isinstance(config.get("terms"), list) or not config.get("terms"):
            raise EvalValidationError(
                "rule_based config must include a non-empty 'terms' list"
            )
    elif data.eval_type == EvalType.SCHEMA:
        if not isinstance(config.get("schema"), dict):
            raise EvalValidationError(
                "schema config must include a JSON Schema document under 'schema'"
            )
    elif data.eval_type == EvalType.LLM_JUDGE:
        if not config.get("judge_prompt_template"):
            raise EvalValidationError(
                "llm_judge config must include 'judge_prompt_template'"
            )

    if data.mission_id is None and not (data.tags or []):
        raise EvalValidationError(
            "An eval definition must be attached to a mission (mission_id) "
            "or carry tags for attach-by-tag matching"
        )


def _definition_to_response(d: EvalDefinition) -> EvalDefinitionResponse:
    return EvalDefinitionResponse(
        id=d.id,
        name=d.name,
        scope=EvalScope(d.scope),
        target_step_key=d.target_step_key,
        eval_type=EvalType(d.eval_type),
        config=d.config,
        threshold=d.threshold,
        mission_id=d.mission_id,
        tags=d.tags,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


async def get_eval_definition_or_404(
    db: AsyncSession, eval_id: uuid.UUID
) -> EvalDefinitionResponse:
    definition = await db.get(EvalDefinition, eval_id)
    if definition is None:
        raise EvalValidationError(f"Eval definition {eval_id} not found", status_code=404)
    return _definition_to_response(definition)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

async def get_eval_history(
    db: AsyncSession, eval_id: uuid.UUID, limit: int = 30
) -> List[Dict[str, Any]]:
    """Return the last *limit* eval results for a given eval, newest first.

    Each entry is a dict with: id, run_id, verdict, score, created_at.
    """
    from sqlalchemy import select, func

    stmt = (
        select(EvalResult.id, EvalResult.run_id, EvalResult.verdict, EvalResult.score, EvalResult.created_at)
        .where(EvalResult.eval_id == eval_id)
        .order_by(EvalResult.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    result: List[Dict[str, Any]] = []
    for row in rows:
        verdict_str = row.verdict
        # row.verdict is a SQLAlchemy SAEnum; in async context it may be a str already.
        if isinstance(row.verdict, str):
            verdict_str = row.verdict
        else:
            try:
                verdict_str = row.verdict.value
            except Exception:
                verdict_str = str(row.verdict)
        result.append(
            {
                "id": str(row.id),
                "run_id": str(row.run_id),
                "verdict": verdict_str,
                "score": float(row.score) if row.score is not None else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return result
