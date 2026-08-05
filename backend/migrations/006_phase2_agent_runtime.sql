-- ============================================================================
-- MERIDIAN Phase 2 — Migration 006: Agent Runtime Schema Additions
-- ============================================================================
-- Adds fields needed for the Agent Runtime module:
--   - mission_id on runs (direct FK to missions for quick lookup)
--   - current_step_id on runs (pointer to the step currently executing)
--   - Indexes for Phase 2 query patterns
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Add mission_id column to runs (direct FK to missions)
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS mission_id UUID REFERENCES missions(id) ON DELETE CASCADE;

-- Backfill mission_id from mission_versions for existing runs
UPDATE runs r
SET mission_id = mv.mission_id
FROM mission_versions mv
WHERE r.mission_version_id = mv.id
  AND r.mission_id IS NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Add current_step_id column to runs (pointer to executing step)
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS current_step_id UUID REFERENCES steps(id) ON DELETE SET NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 3. Add span_id column to run_steps (link to trace span)
--    NOTE: Already exists in migration 002 — this is a safety no-op.
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE run_steps
    ADD COLUMN IF NOT EXISTS span_id UUID REFERENCES spans(id) ON DELETE SET NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 4. Indexes for Phase 2 query patterns
-- ────────────────────────────────────────────────────────────────────────────

-- Runs by mission (list runs for a mission)
CREATE INDEX IF NOT EXISTS idx_runs_mission_id
    ON runs (mission_id);

-- Runs by mission version + created_at (recent runs for a version)
CREATE INDEX IF NOT EXISTS idx_runs_mission_version_created
    ON runs (mission_version_id, created_at DESC);

-- Runs by status (watchdog / reaper queries)
CREATE INDEX IF NOT EXISTS idx_runs_status_started
    ON runs (status, started_at);

-- Run steps by run (fetch all steps for a run)
CREATE INDEX IF NOT EXISTS idx_run_steps_run_id
    ON run_steps (run_id);

-- Run steps by step (find all runs that used a step)
CREATE INDEX IF NOT EXISTS idx_run_steps_step_id
    ON run_steps (step_id);

-- Spans by run + parent (trace tree reconstruction)
CREATE INDEX IF NOT EXISTS idx_spans_run_parent
    ON spans (run_id, parent_span_id);

-- Spans by step (find all spans for a step)
CREATE INDEX IF NOT EXISTS idx_spans_step_id
    ON spans (step_id);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. Add updated_at trigger for runs (if not already covered by 004)
-- ────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_runs_updated_at'
    ) THEN
        CREATE TRIGGER trg_runs_updated_at
        BEFORE UPDATE ON runs
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
