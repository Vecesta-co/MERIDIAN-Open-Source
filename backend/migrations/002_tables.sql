 -- ============================================================================
-- MERIDIAN Phase 0 — Migration 002: Core Tables
-- ============================================================================
-- Creates all core tables with full FK integrity.
-- Depends on: 001_types.sql (enum types must exist first)
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. missions — Top-level agent mission definitions
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS missions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    state           mission_state NOT NULL DEFAULT 'draft',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 1b. agents — Agent definitions (lightweight registry)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      UUID REFERENCES missions(id) ON DELETE SET NULL,
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(255),
    system_prompt   TEXT,
    config          JSONB DEFAULT '{}'::jsonb,
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 2. mission_versions — Versioned snapshots of mission definitions
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      UUID NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    version_int     INTEGER NOT NULL CHECK (version_int >= 1),
    yaml_text       TEXT,
    compiled_json   JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    UNIQUE (mission_id, version_int)
);

-- ────────────────────────────────────────────────────────────────────────────
-- 3. steps — Individual steps within a mission version
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS steps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_version_id  UUID NOT NULL REFERENCES mission_versions(id) ON DELETE CASCADE,
    step_key            VARCHAR(100) NOT NULL,
    name                VARCHAR(255) NOT NULL,
    kind                step_kind NOT NULL,
    order_index         INTEGER NOT NULL CHECK (order_index >= 0),
    depends_on          JSONB DEFAULT '[]'::jsonb,
    config              JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,
    UNIQUE (mission_version_id, step_key)
);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. runs — Execution instances of mission versions
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_version_id  UUID NOT NULL REFERENCES mission_versions(id) ON DELETE CASCADE,
    parent_run_id       UUID REFERENCES runs(id) ON DELETE SET NULL,
    status              run_status NOT NULL DEFAULT 'pending',
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    cancel_requested    BOOLEAN NOT NULL DEFAULT FALSE,
    error_summary       TEXT,
    triggered_by        VARCHAR(50) NOT NULL DEFAULT 'manual',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. run_steps — Per-run, per-step execution tracking
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS run_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id         UUID NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    span_id         UUID REFERENCES spans(id) ON DELETE SET NULL,
    status          step_status NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    error           TEXT,
    output_json     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    UNIQUE (run_id, step_id)
);

-- ────────────────────────────────────────────────────────────────────────────
-- 6. spans — Trace/observability records (OpenTelemetry-style)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id         UUID REFERENCES steps(id) ON DELETE SET NULL,
    parent_span_id  UUID REFERENCES spans(id) ON DELETE SET NULL,
    kind            span_kind NOT NULL,
    name            VARCHAR(255) NOT NULL,
    status          span_status NOT NULL DEFAULT 'ok',
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ,
    input_json      JSONB,
    output_json     JSONB,
    error_json      JSONB,
    meta_json       JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 7. tools — Tool registry metadata
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name       VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    input_schema    JSONB DEFAULT '{}'::jsonb,
    output_schema   JSONB DEFAULT '{}'::jsonb,
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 8. approvals — Human-in-the-loop approval records
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id         UUID NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    status          approval_status NOT NULL DEFAULT 'pending',
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at      TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    decision_json   JSONB,
    reviewer_id     VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 9. eval_definitions — Automated quality check definitions
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS eval_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    target          eval_target NOT NULL,
    config          JSONB DEFAULT '{}'::jsonb,
    threshold       DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (threshold >= 0.0 AND threshold <= 1.0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 10. eval_results — Results of eval checks against runs/steps/spans
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS eval_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_definition_id  UUID NOT NULL REFERENCES eval_definitions(id) ON DELETE CASCADE,
    run_id              UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id             UUID REFERENCES steps(id) ON DELETE SET NULL,
    span_id             UUID REFERENCES spans(id) ON DELETE SET NULL,
    score               DOUBLE PRECISION NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
    verdict             BOOLEAN NOT NULL,
    evidence_json       JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────────────────────
-- 11. secrets — Secret/credential storage (NEVER plaintext in logs)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS secrets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_name        VARCHAR(255) NOT NULL UNIQUE,
    storage_type    secret_storage_type NOT NULL,
    ciphertext      TEXT,
    env_key_name    VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT chk_secret_content CHECK (
        (storage_type = 'encrypted' AND ciphertext IS NOT NULL) OR
        (storage_type = 'env_ref' AND env_key_name IS NOT NULL)
    )
);
