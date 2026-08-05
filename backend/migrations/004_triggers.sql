-- ============================================================================
-- MERIDIAN Phase 0 — Migration 004: Auto-update Triggers
-- ============================================================================
-- Creates triggers to automatically update `updated_at` timestamps
-- on tables that have this column.
-- Depends on: 002_tables.sql (tables must exist first)
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- Helper function: set_updated_at()
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: missions_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_missions_updated_at ON missions;
CREATE TRIGGER trg_missions_updated_at
    BEFORE UPDATE ON missions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: agents_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_agents_updated_at ON agents;
CREATE TRIGGER trg_agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: mission_versions_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_mission_versions_updated_at ON mission_versions;
CREATE TRIGGER trg_mission_versions_updated_at
    BEFORE UPDATE ON mission_versions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: steps_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_steps_updated_at ON steps;
CREATE TRIGGER trg_steps_updated_at
    BEFORE UPDATE ON steps
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: runs_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_runs_updated_at ON runs;
CREATE TRIGGER trg_runs_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: run_steps_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_run_steps_updated_at ON run_steps;
CREATE TRIGGER trg_run_steps_updated_at
    BEFORE UPDATE ON run_steps
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: spans_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_spans_updated_at ON spans;
CREATE TRIGGER trg_spans_updated_at
    BEFORE UPDATE ON spans
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: tools_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_tools_updated_at ON tools;
CREATE TRIGGER trg_tools_updated_at
    BEFORE UPDATE ON tools
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: approvals_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_approvals_updated_at ON approvals;
CREATE TRIGGER trg_approvals_updated_at
    BEFORE UPDATE ON approvals
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: eval_definitions_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_eval_definitions_updated_at ON eval_definitions;
CREATE TRIGGER trg_eval_definitions_updated_at
    BEFORE UPDATE ON eval_definitions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: eval_results_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_eval_results_updated_at ON eval_results;
CREATE TRIGGER trg_eval_results_updated_at
    BEFORE UPDATE ON eval_results
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────────────────────
-- Trigger: secrets_updated_at
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_secrets_updated_at ON secrets;
CREATE TRIGGER trg_secrets_updated_at
    BEFORE UPDATE ON secrets
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
