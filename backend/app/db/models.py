"""
MERIDIAN SQLAlchemy ORM Model Stubs.

These are the database model definitions that map to the Postgres schema.
Used for type-safe queries in Phase 1+.
NOTE: This is a stub — relationships and business logic methods will be added in later phases.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float,
    DateTime, ForeignKey, Enum as SAEnum, UniqueConstraint,
    Index, JSON, Uuid
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ──────────────────────────────────────────────
# Dialect-agnostic type aliases
#
# These map to native PostgreSQL types when the
# application is running against Postgres, but
# degrade to generic SQLAlchemy types on other
# dialects (e.g. SQLite used by the test suite):
#   - UUID  → native UUID on Postgres / CHAR(32) on SQLite
#   - JSONB → native JSONB on Postgres / JSON on SQLite
# ──────────────────────────────────────────────
UUID = Uuid
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# Mission
# ──────────────────────────────────────────────

class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(SAEnum("draft", "published", "archived", name="mission_state"), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    versions = relationship("MissionVersion", back_populates="mission", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# Mission Version
# ──────────────────────────────────────────────

class MissionVersion(Base):
    __tablename__ = "mission_versions"
    __table_args__ = (
        UniqueConstraint("mission_id", "version_int"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    version_int: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    compiled_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    mission = relationship("Mission", back_populates="versions")
    steps = relationship("Step", back_populates="mission_version", cascade="all, delete-orphan")
    runs = relationship("Run", back_populates="mission_version", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# Step
# ──────────────────────────────────────────────

class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (
        UniqueConstraint("mission_version_id", "step_key"),
        UniqueConstraint("mission_version_id", "order_index", name="uq_steps_mission_version_order"),
        Index("idx_steps_mission_version_order", "mission_version_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mission_versions.id", ondelete="CASCADE"), nullable=False)
    step_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(SAEnum("llm", "tool", "approval", name="step_kind"), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False, default="llm")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_refs: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    depends_on: Mapped[Optional[dict]] = mapped_column(JSONB, default=list)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    mission_version = relationship("MissionVersion", back_populates="steps")
    run_steps = relationship("RunStep", back_populates="step", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    mission_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mission_versions.id", ondelete="CASCADE"), nullable=False)
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "running", "awaiting_approval", "paused", "completed", "failed", "cancelled", "timed_out", name="run_status"),
        default="pending"
    )
    current_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(50), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    mission = relationship("Mission")
    mission_version = relationship("MissionVersion", back_populates="runs")
    run_steps = relationship("RunStep", back_populates="run", cascade="all, delete-orphan")
    spans = relationship("Span", back_populates="run", cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="run", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# Run Step
# ──────────────────────────────────────────────

class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False)
    span_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("spans.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "running", "completed", "failed", "skipped", "cancelled", "timed_out", name="step_status"),
        default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run = relationship("Run", back_populates="run_steps")
    step = relationship("Step", back_populates="run_steps")


# ──────────────────────────────────────────────
# Span
# ──────────────────────────────────────────────

class Span(Base):
    __tablename__ = "spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="SET NULL"), nullable=True)
    parent_span_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("spans.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(
        SAEnum("run", "step", "llm", "tool", "eval", "approval", "system", name="span_kind"),
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(SAEnum("ok", "error", "cancelled", name="span_status"), default="ok")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    input_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    meta_json: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run = relationship("Run", back_populates="spans")


# ──────────────────────────────────────────────
# Tool
# ──────────────────────────────────────────────

class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_schema: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    output_schema: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ──────────────────────────────────────────────
# Approval
# ──────────────────────────────────────────────

class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "approved", "rejected", "expired", name="approval_status"),
        default="pending"
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    run = relationship("Run", back_populates="approvals")


# ──────────────────────────────────────────────
# Eval Definition
# ──────────────────────────────────────────────

class EvalDefinition(Base):
    __tablename__ = "eval_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(SAEnum("run", "step", "tool", name="eval_target"), nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    threshold: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ──────────────────────────────────────────────
# Eval Result
# ──────────────────────────────────────────────

class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eval_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_definitions.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("steps.id", ondelete="SET NULL"), nullable=True)
    span_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("spans.id", ondelete="SET NULL"), nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ──────────────────────────────────────────────
# Secret
# ──────────────────────────────────────────────

class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    storage_type: Mapped[str] = mapped_column(SAEnum("env_ref", "encrypted", name="secret_storage_type"), nullable=False)
    ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    env_key_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
