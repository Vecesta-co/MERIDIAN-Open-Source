 -- ============================================================================
-- MERIDIAN Phase 0 — Migration 003: Performance Indexes
-- ============================================================================
-- Creates indexes for query performance on frequently accessed columns.
-- Depends on: 002_tables.sql (tables must exist first)
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- missions indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state);
CREATE INDEX IF NOT EXISTS idx_missions_created_at ON missions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_missions_deleted_at ON missions(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- agents indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_agents_mission_id ON agents(mission_id);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
CREATE INDEX IF NOT EXISTS idx_agents_is_enabled ON agents(is_enabled);
CREATE INDEX IF NOT EXISTS idx_agents_deleted_at ON agents(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- mission_versions indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mission_versions_mission_id ON mission_versions(mission_id);
CREATE INDEX IF NOT EXISTS idx_mission_versions_mission_id_version ON mission_versions(mission_id, version_int DESC);
CREATE INDEX IF NOT EXISTS idx_mission_versions_deleted_at ON mission_versions(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- steps indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_steps_mission_version_id ON steps(mission_version_id);
CREATE INDEX IF NOT EXISTS idx_steps_mission_version_order ON steps(mission_version_id, order_index);
CREATE INDEX IF NOT EXISTS idx_steps_deleted_at ON steps(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- runs indexes (primary query pattern: list runs by mission version, sorted by date)
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_runs_mission_version_created ON runs(mission_version_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_parent_run_id ON runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_triggered_by ON runs(triggered_by);
CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_deleted_at ON runs(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- run_steps indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_run_steps_run_id ON run_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_step_id ON run_steps(step_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_status ON run_steps(status);
CREATE INDEX IF NOT EXISTS idx_run_steps_span_id ON run_steps(span_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_created_at ON run_steps(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_steps_deleted_at ON run_steps(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- spans indexes (primary query pattern: get all spans for a run, build tree)
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent_span_id ON spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_run_id_parent ON spans(run_id, parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);
CREATE INDEX IF NOT EXISTS idx_spans_start_time ON spans(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_spans_deleted_at ON spans(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- tools indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tools_tool_name ON tools(tool_name);
CREATE INDEX IF NOT EXISTS idx_tools_is_enabled ON tools(is_enabled);
CREATE INDEX IF NOT EXISTS idx_tools_created_at ON tools(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tools_updated_at ON tools(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tools_deleted_at ON tools(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- approvals indexes (primary query pattern: find pending/expired approvals)
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_approvals_status_expires ON approvals(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_requested_at ON approvals(requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_updated_at ON approvals(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_deleted_at ON approvals(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- eval_definitions indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_eval_definitions_target ON eval_definitions(target);
CREATE INDEX IF NOT EXISTS idx_eval_definitions_updated_at ON eval_definitions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_definitions_deleted_at ON eval_definitions(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- eval_results indexes (primary query pattern: get all eval results for a run)
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_eval_definition_id ON eval_results(eval_definition_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_eval ON eval_results(run_id, eval_definition_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_verdict ON eval_results(verdict);
CREATE INDEX IF NOT EXISTS idx_eval_results_created_at ON eval_results(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_results_deleted_at ON eval_results(deleted_at) WHERE deleted_at IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- secrets indexes
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_secrets_key_name ON secrets(key_name);
CREATE INDEX IF NOT EXISTS idx_secrets_storage_type ON secrets(storage_type);
CREATE INDEX IF NOT EXISTS idx_secrets_deleted_at ON secrets(deleted_at) WHERE deleted_at IS NOT NULL;
