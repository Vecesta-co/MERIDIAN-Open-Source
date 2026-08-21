-- ============================================================================
-- MERIDIAN Phase 4 — Migration 007: Trace Engine Schema Additions
-- ============================================================================
-- Adds fields needed for the Trace Engine module:
--   - span_type (human-readable span category)
--   - duration_ms (computed or stored)
--   - model, tokens_in/out, cost_usd (LLM cost tracking)
--   - attributes (JSONB for extra info)
--   - Indexes for Phase 4 query patterns
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Add Phase 4 columns to spans
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS span_type VARCHAR(50);

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS severity VARCHAR(20) DEFAULT 'info';

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION;

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS model VARCHAR(255);

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS tokens_in INTEGER;

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS tokens_out INTEGER;

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION;

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS attributes JSONB DEFAULT '{}'::jsonb;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Backfill span_type from existing data
--    - Run spans (kind='run') → 'system'
--    - Step spans (kind='step') → 'system'
--    - LLM spans (kind='llm') → 'llm_step'
--    - Tool spans (kind='tool') → 'tool'
--    - Eval spans (kind='eval') → 'eval'
--    - Approval spans (kind='approval') → 'approval'
-- ────────────────────────────────────────────────────────────────────────────
UPDATE spans
SET span_type = CASE
    WHEN kind = 'run' THEN 'system'
    WHEN kind = 'step' THEN 'system'
    WHEN kind = 'llm' THEN 'llm_step'
    WHEN kind = 'tool' THEN 'tool'
    WHEN kind = 'eval' THEN 'eval'
    WHEN kind = 'approval' THEN 'approval'
    WHEN kind = 'system' THEN 'system'
    ELSE 'system'
END
WHERE span_type IS NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 3. Backfill duration_ms from start_time/end_time
-- ────────────────────────────────────────────────────────────────────────────
UPDATE spans
SET duration_ms = EXTRACT(EPOCH FROM (end_time - start_time)) * 1000
WHERE duration_ms IS NULL
  AND start_time IS NOT NULL
  AND end_time IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 4. Backfill model + token counts from meta_json (backward compat with
--    Phase 2/3 spans that stored this info in meta_json)
-- ────────────────────────────────────────────────────────────────────────────
UPDATE spans
SET model = meta_json->>'model'
WHERE model IS NULL
  AND meta_json ? 'model';

UPDATE spans
SET tokens_in = (meta_json->'tokens'->>'prompt')::INTEGER
WHERE tokens_in IS NULL
  AND meta_json ? 'tokens'
  AND meta_json->'tokens' ? 'prompt';

UPDATE spans
SET tokens_out = (meta_json->'tokens'->>'completion')::INTEGER
WHERE tokens_out IS NULL
  AND meta_json ? 'tokens'
  AND meta_json->'tokens' ? 'completion';

-- ────────────────────────────────────────────────────────────────────────────
-- 5. Indexes for Phase 4 query patterns
-- ────────────────────────────────────────────────────────────────────────────

-- Spans by run + start_time (trace tree / summary — stable ordering)
CREATE INDEX IF NOT EXISTS idx_spans_run_started
    ON spans (run_id, start_time ASC);

-- Spans by parent (tree reconstruction via parent_span_id)
CREATE INDEX IF NOT EXISTS idx_spans_parent
    ON spans (parent_span_id);

-- Spans by run + span_type (filtered span listing)
CREATE INDEX IF NOT EXISTS idx_spans_run_type
    ON spans (run_id, span_type);

-- Run steps by step + status + attempt_count (cross-run step failure query)
CREATE INDEX IF NOT EXISTS idx_run_steps_step_status
    ON run_steps (step_id, status, attempt_count);
