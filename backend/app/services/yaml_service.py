"""
MERIDIAN YAML Service — Phase 1.

Handles YAML <-> JSON conversion for mission workflow definitions.
Supports parsing, validation, and export of the MERIDIAN YAML format.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.core.logging import get_logger
from app.models.schemas import (
    ValidationError,
    ValidationResult,
    YamlAgentDef,
    YamlMissionDef,
    YamlStepDef,
    YamlWorkflow,
)

logger = get_logger(__name__)

# Valid step types
VALID_STEP_TYPES = {"llm", "tool", "approval"}


# ──────────────────────────────────────────────
# Shared Validation Helpers (used by both YAML and JSON paths)
# ──────────────────────────────────────────────


def validate_step_dependencies(steps_data: List[Dict[str, Any]]) -> List[ValidationError]:
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


def validate_tool_step_refs(steps_data: List[Dict[str, Any]]) -> List[ValidationError]:
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


def validate_agent_references(
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


def parse_yaml(yaml_text: str) -> Optional[Dict[str, Any]]:
    """
    Parse a YAML string into a Python dictionary.

    Args:
        yaml_text: Raw YAML string.

    Returns:
        Parsed dictionary or None if parsing fails.
    """
    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            logger.warning("YAML parsed but is not a dictionary")
            return None
        return data
    except yaml.YAMLError as exc:
        logger.error("YAML parsing error: %s", str(exc))
        return None


def dict_to_yaml(data: Dict[str, Any]) -> Optional[str]:
    """
    Convert a Python dictionary to a YAML string.

    Args:
        data: Dictionary to convert.

    Returns:
        YAML string or None if conversion fails.
    """
    try:
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception as exc:
        logger.error("YAML serialization error: %s", str(exc))
        return None


def validate_yaml_workflow(data: Dict[str, Any]) -> ValidationResult:
    """
    Validate a parsed YAML workflow definition.

    Args:
        data: Parsed YAML dictionary.

    Returns:
        ValidationResult with errors and warnings.
    """
    errors: List[ValidationError] = []
    warnings: List[str] = []

    # Check top-level structure
    if "mission" not in data:
        errors.append(ValidationError(
            field="mission",
            message="'mission' section is required",
            code="missing_field",
        ))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    mission = data.get("mission", {})

    # Validate mission name
    if not mission.get("name") or not str(mission.get("name", "")).strip():
        errors.append(ValidationError(
            field="mission.name",
            message="Mission name is required and cannot be empty",
            code="missing_field",
        ))

    # Validate mission goal
    if not mission.get("goal") or not str(mission.get("goal", "")).strip():
        errors.append(ValidationError(
            field="mission.goal",
            message="Mission goal is required and cannot be empty",
            code="missing_field",
        ))

    # Validate steps
    steps = data.get("steps", [])
    if not steps or not isinstance(steps, list):
        errors.append(ValidationError(
            field="steps",
            message="Steps must contain at least 1 step",
            code="empty_list",
        ))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Check for duplicate step keys
    step_keys = []
    step_order_indices = []
    for i, step in enumerate(steps):
        step_key = step.get("key", "")
        if not step_key:
            errors.append(ValidationError(
                field=f"steps[{i}].key",
                message=f"Step at index {i} is missing required 'key' field",
                code="missing_field",
            ))
            continue

        if step_key in step_keys:
            errors.append(ValidationError(
                field=f"steps[{i}].key",
                message=f"Duplicate step key: '{step_key}'",
                code="duplicate_key",
            ))
        step_keys.append(step_key)

        # Validate step_type
        step_type = step.get("step_type", "")
        if step_type and step_type not in VALID_STEP_TYPES:
            errors.append(ValidationError(
                field=f"steps[{i}].step_type",
                message=f"Step '{step_key}' has invalid step_type '{step_type}'. Must be one of: {', '.join(sorted(VALID_STEP_TYPES))}",
                code="invalid_value",
            ))

        # Validate agent_key for llm steps
        if step_type == "llm" and not step.get("agent_key"):
            errors.append(ValidationError(
                field=f"steps[{i}].agent_key",
                message=f"Step '{step_key}' of type 'llm' requires agent_key",
                code="missing_field",
            ))

        # Validate tool_refs structure
        tool_refs = step.get("tool_refs")
        if tool_refs is not None:
            if not isinstance(tool_refs, list):
                errors.append(ValidationError(
                    field=f"steps[{i}].tool_refs",
                    message=f"Step '{step_key}' tool_refs must be a list",
                    code="invalid_type",
                ))
            else:
                for j, ref in enumerate(tool_refs):
                    if not isinstance(ref, dict) or "tool_name" not in ref:
                        errors.append(ValidationError(
                            field=f"steps[{i}].tool_refs[{j}]",
                            message=f"Step '{step_key}' tool_refs[{j}] must be an object with 'tool_name' field",
                            code="invalid_structure",
                        ))

        # Track order_index for duplicate check
        order_idx = step.get("order_index")
        if order_idx is not None:
            if order_idx in step_order_indices:
                warnings.append(f"Duplicate order_index {order_idx} in steps (step '{step_key}')")
                errors.append(ValidationError(
                    field=f"steps[{i}].order_index",
                    message=(
                        f"Step '{step_key}' has duplicate order_index {order_idx} "
                        f"(also used by another step)"
                    ),
                    code="duplicate_order_index",
                ))
            step_order_indices.append(order_idx)

    # Extra Phase 1 audit checks (shared helpers)
    errors.extend(validate_step_dependencies(steps if isinstance(steps, list) else []))
    errors.extend(validate_tool_step_refs(steps if isinstance(steps, list) else []))
    errors.extend(validate_agent_references(
        steps if isinstance(steps, list) else [],
        data.get("agents") if isinstance(data.get("agents"), list) else None,
    ))

    # Validate agents if present
    agents = data.get("agents", [])
    if agents and isinstance(agents, list):
        agent_keys = []
        for i, agent in enumerate(agents):
            agent_key = agent.get("key", "")
            if not agent_key:
                errors.append(ValidationError(
                    field=f"agents[{i}].key",
                    message=f"Agent at index {i} is missing required 'key' field",
                    code="missing_field",
                ))
            elif agent_key in agent_keys:
                errors.append(ValidationError(
                    field=f"agents[{i}].key",
                    message=f"Duplicate agent key: '{agent_key}'",
                    code="duplicate_key",
                ))
            agent_keys.append(agent_key)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def yaml_to_workflow(yaml_text: str) -> Tuple[Optional[YamlWorkflow], ValidationResult]:
    """
    Parse and validate a YAML string into a YamlWorkflow object.

    Args:
        yaml_text: Raw YAML string.

    Returns:
        Tuple of (YamlWorkflow or None, ValidationResult).
    """
    data = parse_yaml(yaml_text)
    if data is None:
        return None, ValidationResult(
            valid=False,
            errors=[ValidationError(
                field="yaml",
                message="Failed to parse YAML. Check syntax.",
                code="parse_error",
            )],
        )

    # Validate
    validation = validate_yaml_workflow(data)
    if not validation.valid:
        return None, validation

    # Convert to Pydantic model
    try:
        workflow = YamlWorkflow(**data)
        return workflow, validation
    except Exception as exc:
        return None, ValidationResult(
            valid=False,
            errors=[ValidationError(
                field="yaml",
                message=f"Schema validation error: {str(exc)}",
                code="schema_error",
            )],
        )


def workflow_to_dict(workflow: YamlWorkflow) -> Dict[str, Any]:
    """
    Convert a YamlWorkflow object to a dictionary for YAML export.

    Args:
        workflow: The YamlWorkflow object.

    Returns:
        Dictionary suitable for YAML serialization.
    """
    result = {
        "version": workflow.version,
        "mission": workflow.mission.model_dump(exclude_none=True),
        "steps": [step.model_dump(exclude_none=True) for step in workflow.steps],
    }

    if workflow.agents:
        result["agents"] = [agent.model_dump(exclude_none=True) for agent in workflow.agents]

    return result


def mission_to_yaml_dict(
    mission_name: str,
    mission_goal: str,
    steps: List[Dict[str, Any]],
    agents: Optional[List[Dict[str, Any]]] = None,
    version: int = 1,
    status: str = "draft",
) -> Dict[str, Any]:
    """
    Build a YAML-compatible dictionary from mission data.

    Args:
        mission_name: Name of the mission.
        mission_goal: Goal/objective of the mission.
        steps: List of step dictionaries.
        agents: Optional list of agent dictionaries.
        version: Mission version number.
        status: Mission status.

    Returns:
        Dictionary ready for YAML serialization.
    """
    result = {
        "version": "1.0",
        "mission": {
            "name": mission_name,
            "goal": mission_goal,
            "version": version,
            "status": status,
        },
        "steps": [],
    }

    if agents:
        result["agents"] = []
        for agent in agents:
            agent_entry = {"key": agent.get("key", "")}
            if agent.get("name"):
                agent_entry["name"] = agent["name"]
            if agent.get("model"):
                agent_entry["model"] = agent["model"]
            if agent.get("system_prompt"):
                agent_entry["system_prompt"] = agent["system_prompt"]
            result["agents"].append(agent_entry)

    for step in steps:
        step_entry = {
            "key": step.get("step_key", step.get("key", "")),
            "name": step.get("name", ""),
            "step_type": step.get("step_type", step.get("kind", "llm")),
        }

        if step.get("agent_key"):
            step_entry["agent_key"] = step["agent_key"]
        if step.get("prompt_template"):
            step_entry["prompt_template"] = step["prompt_template"]
        if step.get("tool_refs"):
            step_entry["tool_refs"] = step["tool_refs"]
        if step.get("approval_required"):
            step_entry["approval_required"] = True
        if step.get("max_retries") is not None and step["max_retries"] != 3:
            step_entry["max_retries"] = step["max_retries"]
        if step.get("timeout_seconds") is not None and step["timeout_seconds"] != 300:
            step_entry["timeout_seconds"] = step["timeout_seconds"]
        if step.get("order_index") is not None:
            step_entry["order_index"] = step["order_index"]

        result["steps"].append(step_entry)

    return result
