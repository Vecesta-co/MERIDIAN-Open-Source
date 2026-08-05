"""
MERIDIAN Phase 1 — Unit Tests for Service Layer.

Pure unit tests for YAML <-> JSON conversion and mission payload validation.
These tests do NOT require a database or an HTTP client — they test
the service-layer logic in isolation.
"""

import pytest

from app.services import mission_service
from app.services.yaml_service import (
    dict_to_yaml,
    mission_to_yaml_dict,
    parse_yaml,
    validate_yaml_workflow,
    workflow_to_dict,
    yaml_to_workflow,
)
from app.models.schemas import ValidationResult


# ══════════════════════════════════════════════════════════════
# YAML Service — parse_yaml
# ══════════════════════════════════════════════════════════════


class TestParseYaml:
    def test_parse_valid_yaml(self):
        """Valid YAML should parse to a dict."""
        yaml_text = "mission:\n  name: Test\n"
        result = parse_yaml(yaml_text)
        assert result is not None
        assert result["mission"]["name"] == "Test"

    def test_parse_invalid_yaml(self):
        """Malformed YAML should return None."""
        result = parse_yaml("{{{{ not valid yaml :::")
        assert result is None

    def test_parse_non_dict_yaml(self):
        """YAML that is not a mapping should return None."""
        result = parse_yaml("- item1\n- item2")
        assert result is None

    def test_parse_empty_string(self):
        """Empty YAML string should return None (safe_load returns None)."""
        result = parse_yaml("")
        assert result is None


# ══════════════════════════════════════════════════════════════
# YAML Service — dict_to_yaml
# ══════════════════════════════════════════════════════════════


class TestDictToYaml:
    def test_dict_to_yaml_roundtrip(self):
        """A dict should convert to YAML and back losslessly."""
        data = {"mission": {"name": "Test", "goal": "Do things"}, "steps": []}
        yaml_text = dict_to_yaml(data)
        assert yaml_text is not None
        parsed = parse_yaml(yaml_text)
        assert parsed == data

    def test_dict_to_yaml_preserves_field_order(self):
        """sort_keys=False should preserve insertion order in YAML output."""
        data = {"version": "1.0", "mission": {"name": "A"}}
        yaml_text = dict_to_yaml(data)
        assert yaml_text is not None
        # The output should have "version" first
        assert yaml_text.index("version") < yaml_text.index("mission")


# ══════════════════════════════════════════════════════════════
# YAML Service — validate_yaml_workflow
# ══════════════════════════════════════════════════════════════


class TestValidateYamlWorkflow:
    def test_valid_workflow(self):
        """A structurally valid workflow should pass."""
        data = {
            "version": "1.0",
            "mission": {"name": "Test", "goal": "Test goal"},
            "steps": [
                {"key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1"},
            ],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is True
        assert result.errors == []

    def test_missing_mission_section(self):
        """Workflow without 'mission' is invalid."""
        result = validate_yaml_workflow({"steps": []})
        assert result.valid is False
        assert any(e.field == "mission" for e in result.errors)

    def test_empty_steps(self):
        """Workflow with no steps is invalid."""
        data = {"mission": {"name": "Test", "goal": "Goal"}}
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.field == "steps" for e in result.errors)

    def test_duplicate_step_keys(self):
        """Duplicate step keys are invalid."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [
                {"key": "dup", "step_type": "llm", "agent_key": "a"},
                {"key": "dup", "step_type": "tool"},
            ],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.code == "duplicate_key" for e in result.errors)

    def test_llm_step_requires_agent_key(self):
        """An llm step without agent_key is invalid."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [{"key": "s1", "step_type": "llm"}],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.code == "missing_field" and "agent_key" in e.field for e in result.errors)

    def test_invalid_step_type(self):
        """Unknown step_type is invalid."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [{"key": "s1", "step_type": "unknown"}],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.code == "invalid_value" for e in result.errors)

    def test_tool_refs_must_have_tool_name(self):
        """tool_refs entries missing 'tool_name' are invalid."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [
                {"key": "s1", "step_type": "tool", "tool_refs": [{"name": "x"}]},
            ],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.code == "invalid_structure" for e in result.errors)

    def test_duplicate_order_index_generates_warning(self):
        """Duplicate order_index should produce a non-blocking warning."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [
                {"key": "s1", "step_type": "llm", "agent_key": "a", "order_index": 1},
                {"key": "s2", "step_type": "llm", "agent_key": "a", "order_index": 1},
            ],
        }
        result = validate_yaml_workflow(data)
        assert len(result.warnings) >= 1
        assert any("order_index" in w for w in result.warnings)

    def test_agent_validation(self):
        """Duplicate agent keys are invalid."""
        data = {
            "mission": {"name": "Test", "goal": "Goal"},
            "steps": [{"key": "s1", "step_type": "llm", "agent_key": "agent_1"}],
            "agents": [
                {"key": "agent_1", "name": "A"},
                {"key": "agent_1", "name": "B"},
            ],
        }
        result = validate_yaml_workflow(data)
        assert result.valid is False
        assert any(e.code == "duplicate_key" for e in result.errors)


# ══════════════════════════════════════════════════════════════
# YAML Service — yaml_to_workflow / workflow_to_dict
# ══════════════════════════════════════════════════════════════


class TestYamlToWorkflow:
    def test_yaml_to_workflow_valid(self):
        """Valid YAML text should produce a YamlWorkflow object."""
        yaml_text = (
            "version: '1.0'\n"
            "mission:\n"
            "  name: Test\n"
            "  goal: Test goal\n"
            "steps:\n"
            "  - key: s1\n"
            "    name: Step 1\n"
            "    step_type: llm\n"
            "    agent_key: agent_1\n"
        )
        workflow, validation = yaml_to_workflow(yaml_text)
        assert workflow is not None
        assert validation.valid is True
        assert workflow.mission.name == "Test"
        assert workflow.steps[0].key == "s1"

    def test_yaml_to_workflow_invalid(self):
        """Invalid YAML should return None workflow and errors."""
        yaml_text = "mission:\n  name: ''\nsteps: []\n"
        workflow, validation = yaml_to_workflow(yaml_text)
        assert workflow is None
        assert validation.valid is False

    def test_yaml_to_workflow_malformed_yaml(self):
        """Malformed YAML should return None workflow with parse_error."""
        workflow, validation = yaml_to_workflow("::: not yaml :::")
        assert workflow is None
        assert not validation.valid

    def test_workflow_to_dict_roundtrip(self):
        """workflow_to_dict should maintain all workflow fields."""
        yaml_text = (
            "version: '1.0'\n"
            "mission:\n"
            "  name: Roundtrip\n"
            "  goal: Goal\n"
            "agents:\n"
            "  - key: agent_1\n"
            "    name: Agent One\n"
            "steps:\n"
            "  - key: s1\n"
            "    name: Step 1\n"
            "    step_type: llm\n"
            "    agent_key: agent_1\n"
        )
        workflow, _ = yaml_to_workflow(yaml_text)
        assert workflow is not None
        data = workflow_to_dict(workflow)
        assert data["mission"]["name"] == "Roundtrip"
        assert data["agents"][0]["key"] == "agent_1"
        assert data["steps"][0]["step_type"] == "llm"


# ══════════════════════════════════════════════════════════════
# YAML Service — mission_to_yaml_dict
# ══════════════════════════════════════════════════════════════


class TestMissionToYamlDict:
    def test_builds_full_workflow_dict(self):
        """Should build a complete YAML-compatible workflow dictionary."""
        result = mission_to_yaml_dict(
            mission_name="Test Mission",
            mission_goal="Test goal",
            steps=[
                {
                    "step_key": "s1",
                    "name": "Step 1",
                    "step_type": "llm",
                    "agent_key": "agent_1",
                    "prompt_template": "Do X",
                }
            ],
        )
        assert result["version"] == "1.0"
        assert result["mission"]["name"] == "Test Mission"
        assert result["mission"]["goal"] == "Test goal"
        assert result["steps"][0]["key"] == "s1"
        assert result["steps"][0]["agent_key"] == "agent_1"
        assert result["steps"][0]["prompt_template"] == "Do X"

    def test_omits_defaults(self):
        """Default values (max_retries=3, timeout=300) should be omitted."""
        result = mission_to_yaml_dict(
            mission_name="M",
            mission_goal="G",
            steps=[{"step_key": "s1", "name": "S", "step_type": "tool"}],
        )
        step = result["steps"][0]
        assert "max_retries" not in step
        assert "timeout_seconds" not in step

    def test_includes_non_default_values(self):
        """Non-default max_retries/timeout should be included."""
        result = mission_to_yaml_dict(
            mission_name="M",
            mission_goal="G",
            steps=[
                {
                    "step_key": "s1",
                    "name": "S",
                    "step_type": "llm",
                    "agent_key": "a",
                    "max_retries": 5,
                    "timeout_seconds": 600,
                }
            ],
        )
        step = result["steps"][0]
        assert step["max_retries"] == 5
        assert step["timeout_seconds"] == 600


# ══════════════════════════════════════════════════════════════
# Mission Service — validate_mission_payload
# ══════════════════════════════════════════════════════════════


class TestValidateMissionPayload:
    def test_valid_json_payload(self):
        """A valid JSON mission payload should validate."""
        payload = {
            "name": "Test",
            "goal": "Goal",
            "steps": [
                {"key": "s1", "step_type": "llm", "agent_key": "a", "name": "S1"},
            ],
        }
        result = mission_service.validate_mission_payload(payload)
        assert isinstance(result, ValidationResult)
        assert result.valid is True

    def test_missing_name_and_goal(self):
        """Missing name and goal should produce missing_field errors."""
        result = mission_service.validate_mission_payload({"steps": []})
        assert result.valid is False
        fields = {e.field for e in result.errors}
        assert "name" in fields
        assert "goal" in fields

    def test_empty_steps(self):
        """Empty steps list should produce empty_list error."""
        result = mission_service.validate_mission_payload(
            {"name": "X", "goal": "Y", "steps": []}
        )
        assert result.valid is False
        assert any(e.code == "empty_list" for e in result.errors)

    def test_duplicate_step_keys_json(self):
        """Duplicate step keys in JSON payload are invalid."""
        payload = {
            "name": "X",
            "goal": "Y",
            "steps": [
                {"key": "dup", "step_type": "llm", "agent_key": "a"},
                {"key": "dup", "step_type": "tool"},
            ],
        }
        result = mission_service.validate_mission_payload(payload)
        assert result.valid is False
        assert any(e.code == "duplicate_key" for e in result.errors)

    def test_llm_requires_agent_key_json(self):
        """LLM step without agent_key is invalid in JSON payload."""
        payload = {
            "name": "X",
            "goal": "Y",
            "steps": [{"key": "s1", "step_type": "llm"}],
        }
        result = mission_service.validate_mission_payload(payload)
        assert result.valid is False
        assert any("agent_key" in e.field for e in result.errors)

    def test_invalid_tool_refs_json(self):
        """tool_refs with missing tool_name is invalid in JSON payload."""
        payload = {
            "name": "X",
            "goal": "Y",
            "steps": [
                {"key": "s1", "step_type": "tool", "tool_refs": [{"name": "foo"}]},
            ],
        }
        result = mission_service.validate_mission_payload(payload)
        assert result.valid is False
        assert any(e.code == "invalid_structure" for e in result.errors)

    def test_yaml_payload_delegates_to_yaml_validation(self):
        """Payload containing yaml_text should be validated via YAML rules."""
        yaml_text = (
            "mission:\n"
            "  name: Test\n"
            "  goal: Goal\n"
            "steps:\n"
            "  - key: s1\n"
            "    step_type: llm\n"
            "    agent_key: agent_1\n"
        )
        result = mission_service.validate_mission_payload({"yaml_text": yaml_text})
        assert result.valid is True

    def test_yaml_payload_invalid(self):
        """Invalid YAML payload should return parse_error."""
        result = mission_service.validate_mission_payload({"yaml_text": "::: bad yaml :::"})
        assert result.valid is False
        assert any(e.code == "parse_error" for e in result.errors)
