-- ============================================================================
-- MERIDIAN Phase 5 — Migration 008: Eval Suite Schema
-- ============================================================================
-- Replaces the Phase 1 eval stub tables with the Phase 5 Eval Suite schema:
--   - eval_definitions: scope (run|step|tool_span), eval_type
--     (rule_based|schema|llm_judge), target_step_key, threshold,
--     mission_id (optional attach-by-mission), tags (attach-by-tag)
--   - eval_results: verdict as pass|fail enum, nullable score,
--     evidence JSONB, span_id pointing at the eval trace span
--   - missions.tags: JSONB array for attach-by-tag matching
--
-- Safe to run on a fresh/dev database (eval tables carry no data).
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. New enum types
-- ────────────────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE eval_scope AS ENUM (
        'run',
        'step',
        'tool_span'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE eval_type AS ENUM (
        'rule_based',
        'schema',
        'llm_judge'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE eval_verdict AS ENUM (
        'pass',
        'fail'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Drop the Phase 1 eval stub tables (no data to preserve)
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_eval_results_updated_at ON eval_results;
DROP TRIGGER IF EXISTS trg_eval_definitions_updated_at ON eval_definitions;

DROP INDEX IF EXISTS idx_eval_results_run_id;
DROP INDEX IF EXISTS idx_eval_results_eval_definition_id;
DROP INDEX IF EXISTS idx_eval_results_run_eval;
DROP INDEX IF EXISTS idx_eval_results_verdict;
DROP INDEX IF EXISTS idx_eval_results_created_at;
DROP INDEX IF EXISTS idx_eval_results_deleted_at;
DROP INDEX IF EXISTS idx_eval_definitions_target;
DROP INDEX IF EXISTS idx_eval_definitions_updated_at;
DROP INDEX IF EXISTS idx_eval_definitions_deleted_at;

DROP TABLE IF EXISTS eval_results;
DROP TABLE IF EXISTS eval_definitions;

-- ────────────────────────────────────────────────────────────────────────────
-- 3. eval_definitions — Phase 5 shape
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE eval_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    scope           eval_scope NOT NULL,
    target_step_key VARCHAR(100),
    eval_type       eval_type NOT NULL,
    config          JSONB DEFAULT '{}'::jsonb,
    threshold       DOUBLE PRECISION NOT NULL DEFAULT 0.5
                    CHECK (threshold >= 0.0 AND threshold <= 1.0),
    mission_id      UUID REFERENCES missions(id) ON DELETE SET NULL,
    tags            JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_definitions_mission
    ON eval_definitions (mission_id);

CREATE INDEX IF NOT EXISTS idx_eval_definitions_updated_at
    ON eval_definitions (updated_at DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. eval_results — Phase 5 shape
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE eval_results (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_id     UUID NOT NULL REFERENCES eval_definitions(id) ON DELETE CASCADE,
    run_id      UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id     UUID REFERENCES steps(id) ON DELETE SET NULL,
    span_id     UUID REFERENCES spans(id) ON DELETE SET NULL,
    verdict     eval_verdict NOT NULL,
    score       DOUBLE PRECISION CHECK (score >= 0.0 AND score <= 1.0),
    evidence    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run
    ON eval_results (run_id);

CREATE INDEX IF NOT EXISTS idx_eval_results_eval
    ON eval_results (eval_id);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. missions.tags — attach-by-tag matching for evals
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE missions
    ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb;

-- ────────────────────────────────────────────────────────────────────────────
-- 6. updated_at trigger for eval_definitions
-- ────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_eval_definitions_updated_at ON eval_definitions;
CREATE TRIGGER trg_eval_definitions_updated_at
    BEFORE UPDATE ON eval_definitions
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
