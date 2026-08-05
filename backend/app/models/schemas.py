#n  MERIDIAN Pydantic Schemas.
# Defines data contracts for all entities in the system.
# These are used for API request/response validation and serialization.
# No business logic is implemented here — only data structure definitions.

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict, model_validator


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────


class MissionState(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SpanKind(str, Enum):
    RUN = "run"
    STEP = "step"
    LLM = "llm"
    TOOL = "tool"
    EVAL = "eval"
    APPROVAL = "approval"
    SYSTEM = "system"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class StepKind(str, Enum):
    LLM = "llm"
    TOOL = "tool"
    APPROVAL = "approval"


class SecretStorageType(str, Enum):
    ENV_REF = "env_ref"
    ENCRYPTED = "encrypted"


class EvalTarget(str, Enum):
    RUN = "run"
    STEP = "step"
    TOOL = "tool"


# ──────────────────────────────────────────────
# Base Schema
# ──────────────────────────────────────────────


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────
# Missions
# ──────────────────────────────────────────────


class MissionCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=255, description="Mission name")
    description: Optional[str] = Field(None, description="Mission description")
    goal: Optional[str] = Field(None, description="Mission goal / objective")
    state: MissionState = Field(default=MissionState.DRAFT, description="Mission state")


class MissionUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    goal: Optional[str] = None
    state: Optional[MissionState] = None


class MissionResponse(BaseSchema):
    id: UUID
    name: str
    description: Optional[str] = None
    goal: Optional[str] = None
    state: MissionState
    version: int = Field(default=1, description="Current mission version")
    created_at: datetime
    updated_at: datetime


class MissionWithStepsResponse(MissionResponse):
    """Mission response including its steps."""
    steps: List["StepWithDetailsResponse"] = Field(default_factory=list, description="Steps in current version")
    agents: List["AgentResponse"] = Field(default_factory=list, description="Agents in current version")


# ──────────────────────────────────────────────
# Mission Versions
# ──────────────────────────────────────────────


class MissionVersionCreate(BaseSchema):
    mission_id: UUID
    version_int: int = Field(..., ge=1, description="Version number")
    yaml_text: Optional[str] = Field(None, description="YAML workflow definition")
    compiled_json: Optional[Dict[str, Any]] = Field(None, description="Compiled JSON representation")


class MissionVersionResponse(BaseSchema):
    id: UUID
    mission_id: UUID
    version_int: int
    yaml_text: Optional[str] = None
    compiled_json: Optional[Dict[str, Any]] = None
    created_at: datetime


# ──────────────────────────────────────────────
# Steps
# ──────────────────────────────────────────────


class StepCreate(BaseSchema):
    mission_version_id: UUID
    step_key: str = Field(..., min_length=1, max_length=100, description="Unique key within version")
    name: str = Field(..., min_length=1, max_length=255)
    kind: StepKind
    order_index: int = Field(..., ge=0, description="Execution order")
    depends_on: Optional[List[str]] = Field(None, description="Step keys this step depends on")
    config: Optional[Dict[str, Any]] = Field(None, description="Step configuration")


class StepResponse(BaseSchema):
    id: UUID
    mission_version_id: UUID
    step_key: str
    name: str
    kind: StepKind
    order_index: int
    depends_on: Optional[List[str]] = None
    config: Optional[Dict[str, Any]] = None
    created_at: datetime


# ──────────────────────────────────────────────
# Runs (Phase 2 — Agent Runtime)
# ──────────────────────────────────────────────


class RunCreate(BaseSchema):
    mission_version_id: UUID


class RunCreateRequest(BaseSchema):
    """Request body for POST /runs — Phase 2."""
    mission_id: UUID = Field(..., description="ID of the published mission to run")
    input_context: Optional[Dict[str, Any]] = Field(
        None, description="Optional input context passed to the run"
    )


class RunResponse(BaseSchema):
    id: UUID
    mission_id: Optional[UUID] = None
    mission_version_id: UUID
    parent_run_id: Optional[UUID] = None
    status: RunStatus
    current_step_id: Optional[UUID] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    cancel_requested: bool = False
    error_summary: Optional[str] = None
    triggered_by: str = Field(default="manual", max_length=50)
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Run Steps
# ──────────────────────────────────────────────


class RunStepResponse(BaseSchema):
    id: UUID
    run_id: UUID
    step_id: UUID
    span_id: Optional[UUID] = None
    status: StepStatus
    attempt_count: int = 0
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    error: Optional[str] = None
    output_json: Optional[Dict[str, Any]] = None
    created_at: datetime


class RunStepDetailResponse(RunStepResponse):
    """Run step with step details (name, key, kind, order_index)."""
    step_key: Optional[str] = None
    step_name: Optional[str] = None
    step_kind: Optional[str] = None
    order_index: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _extract_step_fields(cls, data):
        """Extract step details from the ORM `step` relationship before validation."""
        # The input may be an ORM object (RunStep) with a `step` relationship,
        # or a dict that may already contain a `step` key. Pull the step
        # details into the top-level fields so they populate the response.
        step = None
        if isinstance(data, dict):
            if "step" in data:
                step = data["step"]
            data = dict(data)
        else:
            step = getattr(data, "step", None)
            if step is not None:
                data = {
                    "id": data.id,
                    "run_id": data.run_id,
                    "step_id": data.step_id,
                    "span_id": data.span_id,
                    "status": data.status,
                    "attempt_count": data.attempt_count,
                    "started_at": data.started_at,
                    "ended_at": data.ended_at,
                    "error": data.error,
                    "output_json": data.output_json,
                    "created_at": data.created_at,
                }

        if step is not None:
            data["step_key"] = step.step_key
            data["step_name"] = step.name
            data["step_kind"] = step.kind
            data["order_index"] = step.order_index
        return data


class RunDetailResponse(RunResponse):
    """Run detail including its steps and spans."""
    run_steps: List[RunStepDetailResponse] = Field(default_factory=list)
    spans: List[SpanResponse] = Field(default_factory=list)


# ──────────────────────────────────────────────
# Spans (Trace Records)
# ──────────────────────────────────────────────


class SpanCreate(BaseSchema):
    run_id: UUID
    step_id: Optional[UUID] = None
    parent_span_id: Optional[UUID] = None
    kind: SpanKind
    name: str
    status: SpanStatus = SpanStatus.OK
    start_time: datetime
    end_time: Optional[datetime] = None
    input_json: Optional[Dict[str, Any]] = None
    output_json: Optional[Dict[str, Any]] = None
    error_json: Optional[Dict[str, Any]] = None
    meta_json: Optional[Dict[str, Any]] = Field(
        None, description="Model info, token counts, cost"
    )


class SpanResponse(BaseSchema):
    id: UUID
    run_id: UUID
    step_id: Optional[UUID] = None
    parent_span_id: Optional[UUID] = None
    kind: SpanKind
    name: str
    status: SpanStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    input_json: Optional[Dict[str, Any]] = None
    output_json: Optional[Dict[str, Any]] = None
    error_json: Optional[Dict[str, Any]] = None
    meta_json: Optional[Dict[str, Any]] = None
    created_at: datetime


# ──────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────


class ToolCreate(BaseSchema):
    tool_name: str = Field(..., min_length=1, max_length=100, description="Unique tool name")
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    is_enabled: bool = True


class ToolResponse(BaseSchema):
    id: UUID
    tool_name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Approvals
# ──────────────────────────────────────────────


class ApprovalDecision(BaseSchema):
    decision: ApprovalStatus = Field(..., description="approved or rejected")
    decision_json: Optional[Dict[str, Any]] = Field(
        None, description="Decision context or modified output"
    )
    reviewer_id: Optional[str] = Field(None, description="Reviewer identifier")


class ApprovalResponse(BaseSchema):
    id: UUID
    run_id: UUID
    step_id: UUID
    status: ApprovalStatus
    requested_at: datetime
    decided_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    decision_json: Optional[Dict[str, Any]] = None
    reviewer_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Eval Definitions
# ──────────────────────────────────────────────


class EvalDefinitionCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=255)
    target: EvalTarget
    config: Optional[Dict[str, Any]] = Field(None, description="Eval configuration")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class EvalDefinitionResponse(BaseSchema):
    id: UUID
    name: str
    target: EvalTarget
    config: Optional[Dict[str, Any]] = None
    threshold: float
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Eval Results
# ──────────────────────────────────────────────


class EvalResultResponse(BaseSchema):
    id: UUID
    eval_definition_id: UUID
    run_id: UUID
    step_id: Optional[UUID] = None
    span_id: Optional[UUID] = None
    score: float
    verdict: bool
    evidence_json: Optional[Dict[str, Any]] = None
    created_at: datetime


# ──────────────────────────────────────────────
# Secrets
# ──────────────────────────────────────────────


class SecretCreate(BaseSchema):
    key_name: str = Field(..., min_length=1, max_length=255, description="Unique secret key")
    storage_type: SecretStorageType
    ciphertext: Optional[str] = Field(None, description="Encrypted payload")
    env_key_name: Optional[str] = Field(None, description="Environment variable name")


class SecretResponse(BaseSchema):
    id: UUID
    key_name: str
    storage_type: SecretStorageType
    env_key_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────


class HealthResponse(BaseSchema):
    status: str
    version: str
    timestamp: datetime
    database_connected: Optional[bool] = None


# ──────────────────────────────────────────────
# Generic Error / Not Implemented
# ──────────────────────────────────────────────


class ErrorResponse(BaseSchema):
    detail: str
    path: str
    method: str


class NotImplementedResponse(BaseSchema):
    detail: str = "Not Implemented"
    path: str
    method: str


# ──────────────────────────────────────────────
# Phase 1 — YAML / Validation Schemas
# ──────────────────────────────────────────────


class YamlStepDef(BaseSchema):
    """A step definition as it appears in YAML."""
    key: str = Field(..., min_length=1, max_length=100, description="Unique step key within mission")
    name: str = Field(..., min_length=1, max_length=255, description="Step display name")
    agent_key: Optional[str] = Field(None, description="Reference to an agent key")
    step_type: str = Field(..., description="Step type: llm, tool, or approval")
    prompt_template: Optional[str] = Field(None, description="LLM prompt template")
    tool_refs: Optional[List[Dict[str, Any]]] = Field(None, description="Tool references: [{tool_name, input?}]")
    approval_required: bool = Field(default=False, description="Requires human approval")
    max_retries: int = Field(default=3, ge=0, description="Max retry attempts")
    timeout_seconds: int = Field(default=300, ge=1, description="Step timeout in seconds")
    order_index: Optional[int] = Field(None, ge=0, description="Execution order (auto-assigned if omitted)")


class YamlAgentDef(BaseSchema):
    """An agent definition as it appears in YAML."""
    key: str = Field(..., min_length=1, max_length=100, description="Unique agent key")
    name: Optional[str] = Field(None, description="Agent display name")
    model: Optional[str] = Field(None, description="LLM model identifier")
    system_prompt: Optional[str] = Field(None, description="System prompt for the agent")


class YamlMissionDef(BaseSchema):
    """The mission section of a YAML workflow."""
    name: str = Field(..., min_length=1, max_length=255, description="Mission name")
    goal: str = Field(..., min_length=1, description="Mission goal / objective")
    version: Optional[int] = Field(None, ge=1, description="Mission version")
    status: Optional[str] = Field(None, description="Mission status (draft/published)")


class YamlWorkflow(BaseSchema):
    """Top-level YAML workflow structure."""
    version: str = Field(default="1.0", description="YAML format version")
    mission: YamlMissionDef
    agents: Optional[List[YamlAgentDef]] = Field(None, description="Agent definitions")
    steps: List[YamlStepDef] = Field(..., min_length=1, description="Step definitions")


class YamlExportResponse(BaseSchema):
    """Response for YAML export endpoint."""
    yaml_text: str = Field(..., description="YAML workflow definition")


class ValidationError(BaseSchema):
    """A single validation error."""
    field: str = Field(..., description="Field path that failed validation")
    message: str = Field(..., description="Human-readable error message")
    code: str = Field(default="validation_error", description="Error code")


class ValidationResult(BaseSchema):
    """Result of a validation request."""
    valid: bool = Field(..., description="Whether the payload is valid")
    errors: List[ValidationError] = Field(default_factory=list, description="List of validation errors")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking warnings")


class AgentResponse(BaseSchema):
    """Agent response schema."""
    id: UUID
    key: str
    name: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class StepWithDetailsResponse(StepResponse):
    """Step response with Phase 1 fields."""
    agent_key: Optional[str] = None
    step_type: str
    prompt_template: Optional[str] = None
    tool_refs: Optional[List[Dict[str, Any]]] = None
    approval_required: bool = False
    max_retries: int = 3
    timeout_seconds: int = 300


class MissionCreateYaml(BaseSchema):
    """Request schema for creating a mission from YAML string."""
    yaml_text: str = Field(..., description="YAML workflow definition")


class MissionCloneResponse(BaseSchema):
    """Response for clone operation."""
    mission: MissionResponse = Field(..., description="The cloned mission")
    message: str = Field(default="Mission cloned successfully", description="Status message")
