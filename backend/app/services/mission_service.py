"""
MERIDIAN Mission Service — Phase 1.

Handles business logic for mission CRUD operations:
- Create missions from JSON or YAML
- Retrieve missions with steps
- Update draft missions (increments version)
- Publish missions (locks edits)
- Clone missions (creates independent v1 draft)
- Export missions as YAML
- Validate mission payloads without saving
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Mission, MissionVersion, Step
from app.models.schemas import (
    MissionResponse,
    StepWithDetailsResponse,
    ValidationError,
    ValidationResult,
    YamlWorkflow,
)
from app.services.yaml_service import (
    dict_to_yaml,
    mission_to_yaml_dict,
    parse_yaml,
    validate_yaml_workflow,
    yaml_to_workflow,
)

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# Query Helpers
# ──────────────────────────────────────────────


async def get_mission_or_404(session: AsyncSession, mission_id: uuid.UUID) -> Mission:
    """Fetch a mission by ID or raise an error."""
    result = await session.execute(
        select(Mission).where(Mission.id == mission_id, Mission.deleted_at.is_(None))
    )
    mission = result.scalar_one_or_none()
    if mission is None:
        raise ValueError(f"Mission {mission_id} not found")
    return mission


async def get_latest_version(session: AsyncSession, mission_id: uuid.UUID) -> Optional[MissionVersion]:
    """Fetch the latest version for a mission."""
    result = await session.execute(
        select(MissionVersion)
        .where(MissionVersion.mission_id == mission_id)
        .order_by(MissionVersion.version_int.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_steps_for_mission_version(session: AsyncSession, version_id: uuid.UUID) -> List[Step]:
    """Fetch all steps for a mission version, ordered by order_index."""
    result = await session.execute(
        select(Step)
        .where(Step.mission_version_id == version_id)
        .order_by(Step.order_index)
    )
    return list(result.scalars().all())


def mission_to_response(mission: Mission) -> MissionResponse:
    """Convert a Mission ORM object to a MissionResponse schema."""
    return MissionResponse.model_validate(mission)


def step_to_response(step: Step) -> StepWithDetailsResponse:
    """Convert a Step ORM object to a StepWithDetailsResponse schema."""
    return StepWithDetailsResponse.model_validate(step)


def step_to_yaml_dict(step: Step) -> Dict[str, Any]:
    """Convert a Step ORM object to a YAML-compatible dictionary."""
    result: Dict[str, Any] = {
        "key": step.step_key,
        "name": step.name,
        "step_type": getattr(step, "step_type", None) or step.kind,
    }
    if getattr(step, "agent_key", None):
        result["agent_key"] = step.agent_key
    if getattr(step, "prompt_template", None):
        result["prompt_template"] = step.prompt_template
    if getattr(step, "tool_refs", None):
        result["tool_refs"] = step.tool_refs
    if getattr(step, "approval_required", False):
        result["approval_required"] = True
    if getattr(step, "max_retries", 3) != 3:
        result["max_retries"] = step.max_retries
    if getattr(step, "timeout_seconds", 300) != 300:
        result["timeout_seconds"] = step.timeout_seconds
    return result


def mission_to_yaml_text(
    mission: Mission,
    steps: List[Step],
) -> Optional[str]:
    """Convert a mission and its steps to a YAML string."""
    data = mission_to_yaml_dict(
        mission_name=mission.name,
        mission_goal=mission.goal or "",
        steps=[step_to_yaml_dict(s) for s in steps],
        version=mission.version,
        status=mission.state,
    )
    return dict_to_yaml(data)


def build_mission_with_steps(mission: Mission, steps: List[Step]) -> Dict[str, Any]:
    """Build a MissionWithStepsResponse-compatible dictionary."""
    return {
        "id": str(mission.id),
        "name": mission.name,
        "description": mission.description,
        "goal": mission.goal,
        "state": mission.state,
        "version": mission.version,
        "created_at": mission.created_at,
        "updated_at": mission.updated_at,
        "steps": [step_to_response(s).model_dump() for s in steps],
    }


# ──────────────────────────────────────────────
# Create Mission
# ──────────────────────────────────────────────


async def create_mission_from_json(
    session: AsyncSession,
    mission_data: Dict[str, Any],
) -> Tuple[Mission, List[Step]]:
    """
    Create a mission and its steps from a JSON request body.

    Expected format:
    {
        "name": "Mission name",
        "goal": "Mission goal",
        "description": "Optional description",
        "steps": [
            {
                "key": "step_1",
                "name": "Step 1",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "...",
                "tool_refs": [{"tool_name": "http_request"}],
                "approval_required": false,
                "max_retries": 3,
                "timeout_seconds": 300,
                "order_index": 0
            }
        ]
    }

    Returns:
        Tuple of (Mission, List[Step]).
    """
    # Run full validation first so creation enforces the same contract as /validate
    validation = validate_mission_payload(mission_data)
    if not validation.valid:
        first = validation.errors[0] if validation.errors else None
        raise ValueError(
            f"Validation failed: {first.field}: {first.message}" if first else "Validation failed"
        )

    steps_data = mission_data.get("steps", [])
    if not steps_data:
        raise ValueError("Mission must contain at least 1 step")

    # Create mission
    mission = Mission(
        id=uuid.uuid4(),
        name=mission_data["name"],
        description=mission_data.get("description"),
        goal=mission_data.get("goal"),
        state=mission_data.get("state", "draft"),
        version=1,
        tags=mission_data.get("tags", []),
    )
    session.add(mission)
    await session.flush()  # Get mission.id

    # Create mission_version
    mv = MissionVersion(
        id=uuid.uuid4(),
        mission_id=mission.id,
        version_int=1,
        compiled_json=mission_data,
    )
    session.add(mv)
    await session.flush()  # Get mv.id

    # Create steps
    steps: List[Step] = []
    for i, step_data in enumerate(steps_data):
        order_idx = step_data.get("order_index", i)
        step = Step(
            id=uuid.uuid4(),
            mission_version_id=mv.id,
            step_key=step_data["key"],
            name=step_data.get("name", step_data["key"]),
            kind=step_data.get("step_type", "llm"),
            step_type=step_data.get("step_type", "llm"),
            order_index=order_idx,
            agent_key=step_data.get("agent_key"),
            prompt_template=step_data.get("prompt_template"),
            tool_refs=step_data.get("tool_refs", []),
            approval_required=step_data.get("approval_required", False),
            max_retries=step_data.get("max_retries", 3),
            timeout_seconds=step_data.get("timeout_seconds", 300),
            depends_on=step_data.get("depends_on", []),
            config=step_data.get("config", {}),
        )
        session.add(step)
        steps.append(step)

    await session.commit()
    await session.refresh(mission)
    logger.info("Created mission '%s' (id=%s) with %d steps", mission.name, mission.id, len(steps))
    return mission, steps


async def create_mission_from_yaml(
    session: AsyncSession,
    yaml_text: str,
) -> Tuple[Mission, List[Step], YamlWorkflow]:
    """
    Create a mission and steps from YAML text.

    Parses the YAML, validates it, and creates the mission.

    Returns:
        Tuple of (Mission, List[Step], YamlWorkflow).
    """
    workflow, validation = yaml_to_workflow(yaml_text)
    if workflow is None:
        raise ValueError(
            f"Invalid YAML: {validation.errors[0].message if validation.errors else 'unknown error'}"
        )

    mission_data = {
        "name": workflow.mission.name,
        "goal": workflow.mission.goal,
        "description": None,
        "steps": [s.model_dump(exclude_none=True) for s in workflow.steps],
    }

    mission, steps = await create_mission_from_json(session, mission_data)
    return mission, steps, workflow


# ──────────────────────────────────────────────
# Get Mission
# ──────────────────────────────────────────────


async def get_mission_detail(session: AsyncSession, mission_id: uuid.UUID) -> Dict[str, Any]:
    """Get a mission with its steps."""
    mission = await get_mission_or_404(session, mission_id)
    mv = await get_latest_version(session, mission.id)

    steps: List[Step] = []
    if mv is not None:
        steps = await get_steps_for_mission_version(session, mv.id)

    return build_mission_with_steps(mission, steps)


async def list_missions(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
    state: Optional[str] = None,
) -> Tuple[List[MissionResponse], int]:
    """List missions with pagination and optional state filter."""
    query = select(Mission).where(Mission.deleted_at.is_(None))

    if state:
        query = query.where(Mission.state == state)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_query)).scalar_one()

    # Apply pagination
    query = (
        query.order_by(Mission.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(query)
    missions = list(result.scalars().all())

    return [mission_to_response(m) for m in missions], total


# ──────────────────────────────────────────────
# Update Mission
# ──────────────────────────────────────────────


async def update_mission(
    session: AsyncSession,
    mission_id: uuid.UUID,
    update_data: Dict[str, Any],
) -> Tuple[Mission, List[Step]]:
    """
    Update a draft mission. Increments version on every save.

    Publishing locks the mission from edits (403 on attempt to edit published).
    """
    mission = await get_mission_or_404(session, mission_id)

    if mission.state == "published":
        raise PermissionError(
            "Cannot update a published mission. Clone it to create a new version."
        )

    # Apply field updates
    if "name" in update_data:
        mission.name = update_data["name"]
    if "description" in update_data:
        mission.description = update_data["description"]
    if "goal" in update_data:
        mission.goal = update_data["goal"]
    if "tags" in update_data:
        mission.tags = update_data["tags"]

    # Full-replacement validation: when steps are provided, the update payload
    # must be valid on its own (same contract as POST /missions and /validate).
    # Avoids partial updates silently inheriting existing mission fields.
    if "steps" in update_data:
        validation = validate_mission_payload(update_data)
        if not validation.valid:
            first = validation.errors[0] if validation.errors else None
            raise ValueError(
                f"Validation failed: {first.field}: {first.message}" if first else "Validation failed"
            )
        if not update_data.get("steps"):
            raise ValueError("Mission must contain at least 1 step")

    # Handle steps replacement (if provided)
    steps: List[Step] = []
    if "steps" in update_data:
        # Delete existing version and create new one
        current_mv = await get_latest_version(session, mission.id)
        if current_mv is not None:
            existing_steps = await get_steps_for_mission_version(session, current_mv.id)
            for s in existing_steps:
                await session.delete(s)
            await session.flush()

        # Increment mission version
        mission.version += 1

        # Create new version
        new_mv = MissionVersion(
            id=uuid.uuid4(),
            mission_id=mission.id,
            version_int=mission.version,
            compiled_json=update_data,
        )
        session.add(new_mv)
        await session.flush()

        steps_data = update_data["steps"]

        # Create new steps
        for i, step_data in enumerate(steps_data):
            order_idx = step_data.get("order_index", i)
            step = Step(
                id=uuid.uuid4(),
                mission_version_id=new_mv.id,
                step_key=step_data["key"],
                name=step_data.get("name", step_data["key"]),
                kind=step_data.get("step_type", "llm"),
                step_type=step_data.get("step_type", "llm"),
                order_index=order_idx,
                agent_key=step_data.get("agent_key"),
                prompt_template=step_data.get("prompt_template"),
                tool_refs=step_data.get("tool_refs", []),
                approval_required=step_data.get("approval_required", False),
                max_retries=step_data.get("max_retries", 3),
                timeout_seconds=step_data.get("timeout_seconds", 300),
                depends_on=step_data.get("depends_on", []),
                config=step_data.get("config", {}),
            )
            session.add(step)
            steps.append(step)
    else:
        # No steps change — still increment version per spec
        mission.version += 1

    mission.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(mission)

    # Fetch steps for response
    if not steps:
        mv = await get_latest_version(session, mission.id)
        if mv is not None:
            steps = await get_steps_for_mission_version(session, mv.id)

    logger.info("Updated mission '%s' to version %d", mission.name, mission.version)
    return mission, steps


# ──────────────────────────────────────────────
# Publish Mission
# ──────────────────────────────────────────────


async def publish_mission(session: AsyncSession, mission_id: uuid.UUID) -> Mission:
    """Publish a mission. Sets state to 'published' (idempotent)."""
    mission = await get_mission_or_404(session, mission_id)
    mission.state = "published"
    mission.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(mission)
    logger.info("Published mission '%s'", mission.name)
    return mission


# ──────────────────────────────────────────────
# Clone Mission
# ──────────────────────────────────────────────


async def clone_mission(session: AsyncSession, mission_id: uuid.UUID) -> Mission:
    """
    Clone a mission. Creates an independent copy:
    - name: "{original name} (Copy)"
    - version: 1
    - state: draft
    - steps: independent copies
    """
    original = await get_mission_or_404(session, mission_id)

    # Get original steps
    mv = await get_latest_version(session, original.id)
    original_steps: List[Step] = []
    if mv is not None:
        original_steps = await get_steps_for_mission_version(session, mv.id)

    # Create cloned mission
    new_mission = Mission(
        id=uuid.uuid4(),
        name=f"{original.name} (Copy)",
        description=original.description,
        goal=original.goal,
        state="draft",
        version=1,
        tags=original.tags,
    )
    session.add(new_mission)
    await session.flush()

    # Create version 1
    new_mv = MissionVersion(
        id=uuid.uuid4(),
        mission_id=new_mission.id,
        version_int=1,
    )
    session.add(new_mv)
    await session.flush()

    # Clone steps
    for step in original_steps:
        new_step = Step(
            id=uuid.uuid4(),
            mission_version_id=new_mv.id,
            step_key=step.step_key,
            name=step.name,
            kind=step.kind,
            step_type=getattr(step, "step_type", None) or step.kind,
            order_index=step.order_index,
            agent_key=getattr(step, "agent_key", None),
            prompt_template=getattr(step, "prompt_template", None),
            tool_refs=getattr(step, "tool_refs", []),
            approval_required=getattr(step, "approval_required", False),
            max_retries=getattr(step, "max_retries", 3),
            timeout_seconds=getattr(step, "timeout_seconds", 300),
            depends_on=step.depends_on,
            config=step.config,
        )
        session.add(new_step)

    await session.commit()
    await session.refresh(new_mission)
    logger.info("Cloned mission '%s' -> '%s'", original.name, new_mission.name)
    return new_mission


# ──────────────────────────────────────────────
# Validate Mission (no save)
# ──────────────────────────────────────────────


def _validate_step_dependencies(steps_data: List[Dict[str, Any]]) -> List[ValidationError]:
    """
    Validate the depends_on graph of a step list.

    Rules enforced:
      - depends_on must be a list (when present)
      - each entry must be a string step key
      - each referenced key must exist in the step list
      - the dependency graph must be acyclic (no circular references)
    """
    errors: List[ValidationError] = []
    step_keys = [s.get("key", "") for s in steps_data]
    key_set = set(step_keys)

    for i, step in enumerate(steps_data):
        step_key = step.get("key", "")
        deps = step.get("depends_on")
        if deps is None:
            continue

        if not isinstance(deps, list):
            errors.append(ValidationError(
                field=f"steps[{i}].depends_on",
                message=f"Step '{step_key}' depends_on must be a list of step keys",
                code="invalid_type",
            ))
            continue

        for j, dep in enumerate(deps):
            if not isinstance(dep, str):
                errors.append(ValidationError(
                    field=f"steps[{i}].depends_on[{j}]",
                    message=f"Step '{step_key}' depends_on[{j}] must be a string step key",
                    code="invalid_type",
                ))
            elif dep == step_key:
                errors.append(ValidationError(
                    field=f"steps[{i}].depends_on[{j}]",
                    message=f"Step '{step_key}' cannot depend on itself",
                    code="circular_dependency",
                ))
            elif dep not in key_set:
                errors.append(ValidationError(
                    field=f"steps[{i}].depends_on[{j}]",
                    message=f"Step '{step_key}' depends_on references unknown step key '{dep}'",
                    code="unknown_reference",
                ))

    # Detect cycles in the dependency graph (only if no structural errors yet)
    if not errors:
        graph: Dict[str, List[str]] = {key: [] for key in step_keys}
        for step in steps_data:
            deps = step.get("depends_on") or []
            key = step.get("key", "")
            if isinstance(deps, list):
                for dep in deps:
                    if isinstance(dep, str) and dep in graph:
                        graph[key].append(dep)

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {key: WHITE for key in graph}

        def _dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in graph[node]:
                if color[neighbor] == GRAY:
                    return True  # back edge → cycle
                if color[neighbor] == WHITE and _dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for key in graph:
            if color[key] == WHITE and _dfs(key):
                errors.append(ValidationError(
                    field="steps",
                    message=f"Circular step dependency detected (involving step '{key}')",
                    code="circular_dependency",
                ))
                break

    return errors


def _validate_tool_step_refs(steps_data: List[Dict[str, Any]]) -> List[ValidationError]:
    """Tool steps must declare at least one tool_ref entry."""
    errors: List[ValidationError] = []
    for i, step in enumerate(steps_data):
        step_type = step.get("step_type", step.get("kind", ""))
        if step_type != "tool":
            continue
        tool_refs = step.get("tool_refs")
        if not tool_refs or not isinstance(tool_refs, list):
            errors.append(ValidationError(
                field=f"steps[{i}].tool_refs",
                message=f"Step '{step.get('key', '')}' of type 'tool' requires at least one tool_ref",
                code="missing_field",
            ))
    return errors


def _validate_agent_references(
    steps_data: List[Dict[str, Any]],
    agents_data: Optional[List[Any]],
) -> List[ValidationError]:
    """When an agents section is provided, llm steps must reference a defined agent key."""
    errors: List[ValidationError] = []
    if not agents_data:
        return errors  # agents section absent — can't cross-check (deferred)

    agent_keys = {
        a.get("key", "")
        for a in agents_data
        if isinstance(a, dict) and a.get("key")
    }
    for i, step in enumerate(steps_data):
        step_type = step.get("step_type", step.get("kind", ""))
        agent_key = step.get("agent_key")
        if step_type == "llm" and agent_key and agent_key not in agent_keys:
            errors.append(ValidationError(
                field=f"steps[{i}].agent_key",
                message=f"Step '{step.get('key', '')}' references undefined agent '{agent_key}'",
                code="unknown_reference",
            ))
    return errors


def _validate_unique_order_indices(steps_data: List[Dict[str, Any]]) -> List[ValidationError]:
    """
    Validate that order_index values within a step list are unique.

    The DB enforces a UniqueConstraint on (mission_version_id, order_index),
    so this check runs in the service layer to produce a clean 400 response
    instead of a raw IntegrityError.
    """
    errors: List[ValidationError] = []
    seen_indices: Dict[int, str] = {}  # order_index -> step key

    for i, step in enumerate(steps_data):
        order_idx = step.get("order_index")
        if order_idx is None:
            continue
        if not isinstance(order_idx, int):
            errors.append(ValidationError(
                field=f"steps[{i}].order_index",
                message=f"Step '{step.get('key', '')}' order_index must be an integer",
                code="invalid_type",
            ))
            continue
        if order_idx in seen_indices:
            errors.append(ValidationError(
                field=f"steps[{i}].order_index",
                message=(
                    f"Step '{step.get('key', '')}' has duplicate order_index {order_idx} "
                    f"(also used by step '{seen_indices[order_idx]}')"
                ),
                code="duplicate_order_index",
            ))
        else:
            seen_indices[order_idx] = step.get("key", "")

    return errors


def validate_mission_payload(payload: Dict[str, Any]) -> ValidationResult:
    """
    Validate a mission payload without saving.

    Supports both JSON and YAML formats:
    - JSON: {name, goal, steps: [...]}
    - YAML: {yaml_text: "..."}
    """
    if "yaml_text" in payload:
        # YAML format
        yaml_text = payload["yaml_text"]
        data = parse_yaml(yaml_text)
        if data is None:
            return ValidationResult(
                valid=False,
                errors=[
                    ValidationError(
                        field="yaml_text",
                        message="Failed to parse YAML",
                        code="parse_error",
                    )
                ],
            )
        return validate_yaml_workflow(data)

    # JSON format
    errors: List[ValidationError] = []

    # Mission name
    if not payload.get("name") or not str(payload.get("name", "")).strip():
        errors.append(
            ValidationError(
                field="name",
                message="Mission name is required and cannot be empty",
                code="missing_field",
            )
        )

    # Mission goal
    if not payload.get("goal") or not str(payload.get("goal", "")).strip():
        errors.append(
            ValidationError(
                field="goal",
                message="Mission goal is required and cannot be empty",
                code="missing_field",
            )
        )

    # Steps
    steps = payload.get("steps", [])
    if not steps or not isinstance(steps, list):
        errors.append(
            ValidationError(
                field="steps",
                message="Steps must contain at least 1 step",
                code="empty_list",
            )
        )
        return ValidationResult(valid=False, errors=errors)

    # Duplicate step keys
    step_keys = [s.get("key", "") for s in steps]
    seen_keys = set()
    for key in step_keys:
        if key in seen_keys:
            errors.append(
                ValidationError(
                    field="steps",
                    message=f"Duplicate step key: '{key}'",
                    code="duplicate_key",
                )
            )
        seen_keys.add(key)

    # Validate each step
    for i, step in enumerate(steps):
        step_key = step.get("key", "")
        step_type = step.get("step_type", "")

        if not step_key:
            errors.append(
                ValidationError(
                    field=f"steps[{i}].key",
                    message="Step key is required",
                    code="missing_field",
                )
            )

        if step_type == "llm" and not step.get("agent_key"):
            errors.append(
                ValidationError(
                    field=f"steps[{i}].agent_key",
                    message=f"Step '{step_key}' of type 'llm' requires agent_key",
                    code="missing_field",
                )
            )

        tool_refs = step.get("tool_refs")
        if tool_refs is not None:
            if not isinstance(tool_refs, list):
                errors.append(
                    ValidationError(
                        field=f"steps[{i}].tool_refs",
                        message="tool_refs must be a list",
                        code="invalid_type",
                    )
                )
            else:
                for j, ref in enumerate(tool_refs):
                    if not isinstance(ref, dict) or "tool_name" not in ref:
                        errors.append(
                            ValidationError(
                                field=f"steps[{i}].tool_refs[{j}]",
                                message="tool_refs entries must contain 'tool_name'",
                                code="invalid_structure",
                            )
                        )

    # New audit fixes: depends_on graph, tool_refs on tool steps, agent cross-ref,
    # order_index uniqueness
    errors.extend(_validate_step_dependencies(steps))
    errors.extend(_validate_tool_step_refs(steps))
    errors.extend(_validate_agent_references(steps, payload.get("agents")))
    errors.extend(_validate_unique_order_indices(steps))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
