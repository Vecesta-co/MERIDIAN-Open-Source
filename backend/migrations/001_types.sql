-- ============================================================================
-- MERIDIAN Phase 0 — Migration 001: Enum Types
-- ============================================================================
-- Creates all PostgreSQL enum types used across the MERIDIAN schema.
-- These must be created before any table that references them.
-- ============================================================================

-- Mission lifecycle states
DO $$ BEGIN
    CREATE TYPE mission_state AS ENUM (
        'draft',
        'published',
        'archived'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Run execution statuses
DO $$ BEGIN
    CREATE TYPE run_status AS ENUM (
        'pending',
        'running',
        'awaiting_approval',
        'paused',
        'completed',
        'failed',
        'cancelled',
        'timed_out'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Individual step execution statuses
DO $$ BEGIN
    CREATE TYPE step_status AS ENUM (
        'pending',
        'running',
        'completed',
        'failed',
        'skipped',
        'cancelled',
        'timed_out'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Human approval statuses
DO $$ BEGIN
    CREATE TYPE approval_status AS ENUM (
        'pending',
        'approved',
        'rejected',
        'expired'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Span types for trace observability
DO $$ BEGIN
    CREATE TYPE span_kind AS ENUM (
        'run',
        'step',
        'llm',
        'tool',
        'eval',
        'approval',
        'system'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Span execution outcomes
DO $$ BEGIN
    CREATE TYPE span_status AS ENUM (
        'ok',
        'error',
        'cancelled'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Step kinds (what type of action a step performs)
DO $$ BEGIN
    CREATE TYPE step_kind AS ENUM (
        'llm',
        'tool',
        'approval'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Secret storage methods
DO $$ BEGIN
    CREATE TYPE secret_storage_type AS ENUM (
        'env_ref',
        'encrypted'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Eval target types
DO $$ BEGIN
    CREATE TYPE eval_target AS ENUM (
        'run',
        'step',
        'tool'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
