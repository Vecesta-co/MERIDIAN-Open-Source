-- ============================================================================
-- MERIDIAN Phase 1 — Migration 005: Mission Designer Schema Additions
-- ============================================================================
-- Adds fields needed for the Mission Designer module:
--   - version field on missions table
--   - New step fields: agent_key, prompt_template, tool_refs,
--     approval_required, max_retries, timeout_seconds
--   - UNIQUE constraint on (mission_version_id, order_index)
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Add version column to missions
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE missions
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Add new columns to steps table
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE steps
    ADD COLUMN IF NOT EXISTS agent_key VARCHAR(100),
    ADD COLUMN IF NOT EXISTS prompt_template TEXT,
    ADD COLUMN IF NOT EXISTS tool_refs JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS timeout_seconds INTEGER NOT NULL DEFAULT 300;

-- ────────────────────────────────────────────────────────────────────────────
-- 3. Add UNIQUE constraint on (mission_version_id, order_index)
-- ────────────────────────────────────────────────────────────────────────────
-- First, clean up any existing duplicates (keep the one with lowest id)
DELETE FROM steps a USING steps b
WHERE a.mission_version_id = b.mission_version_id
  AND a.order_index = b.order_index
  AND a.id > b.id;

-- Now add the unique constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_steps_mission_version_order'
    ) THEN
        ALTER TABLE steps
            ADD CONSTRAINT uq_steps_mission_version_order
            UNIQUE (mission_version_id, order_index);
    END IF;
END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- 4. Add goal column to missions (if not exists from Phase 0)
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE missions
    ADD COLUMN IF NOT EXISTS goal TEXT;

-- ────────────────────────────────────────────────────────────────────────────
-- 5. Add step_type column as a human-readable alias for kind
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE steps
    ADD COLUMN IF NOT EXISTS step_type VARCHAR(50);

-- Backfill step_type from kind for existing rows
UPDATE steps SET step_type = kind::text WHERE step_type IS NULL;

-- Make step_type NOT NULL after backfill
ALTER TABLE steps
    ALTER COLUMN step_type SET NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 6. Add index on steps(mission_version_id, order_index) for ordering queries
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_steps_mission_version_order
    ON steps (mission_version_id, order_index);

-- ────────────────────────────────────────────────────────────────────────────
-- 7. Add index on missions(version) for version queries
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_missions_version
    ON missions (version);
