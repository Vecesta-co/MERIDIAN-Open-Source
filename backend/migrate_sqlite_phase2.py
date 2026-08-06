"""
MERIDIAN Phase 2 — SQLite Dev DB Migration Helper.

The persistent dev DB (meridian_dev.db) was created before Phase 2
migration 006, so it lacks the runs.mission_id / runs.current_step_id
columns that the ORM model now requires. This script adds the missing
columns + indexes to the existing SQLite dev DB, preserving data.

This is a dev-only helper. Production uses Postgres migrations
(backend/migrations/006_phase2_agent_runtime.sql).
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "meridian_dev.db"


def get_columns(conn: sqlite3.Connection, table: str) -> set:
    """Return the set of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    cols = get_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        print(f"  + Added {table}.{column}")
        return True
    print(f"  = {table}.{column} already exists")
    return False


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        print(f"Migrating {DB_PATH}...")

        # Migration 006: runs.mission_id (FK to missions)
        add_column_if_missing(
            conn, "runs", "mission_id",
            "CHAR(32) REFERENCES missions(id) ON DELETE CASCADE",
        )

        # Migration 006: runs.current_step_id (FK to steps)
        add_column_if_missing(
            conn, "runs", "current_step_id",
            "CHAR(32) REFERENCES steps(id) ON DELETE SET NULL",
        )

        # Migration 006: run_steps.span_id (FK to spans) — safety no-op
        add_column_if_missing(
            conn, "run_steps", "span_id",
            "CHAR(32) REFERENCES spans(id) ON DELETE SET NULL",
        )

        # Indexes for Phase 2 query patterns
        indexes = {
            "idx_runs_mission_id": "CREATE INDEX IF NOT EXISTS idx_runs_mission_id ON runs(mission_id)",
            "idx_runs_mission_version_created": "CREATE INDEX IF NOT EXISTS idx_runs_mission_version_created ON runs(mission_version_id, created_at DESC)",
            "idx_runs_status_started": "CREATE INDEX IF NOT EXISTS idx_runs_status_started ON runs(status, started_at)",
            "idx_run_steps_run_id": "CREATE INDEX IF NOT EXISTS idx_run_steps_run_id ON run_steps(run_id)",
            "idx_run_steps_step_id": "CREATE INDEX IF NOT EXISTS idx_run_steps_step_id ON run_steps(step_id)",
            "idx_spans_run_parent": "CREATE INDEX IF NOT EXISTS idx_spans_run_parent ON spans(run_id, parent_span_id)",
            "idx_spans_step_id": "CREATE INDEX IF NOT EXISTS idx_spans_step_id ON spans(step_id)",
        }
        for name, ddl in indexes.items():
            conn.execute(ddl)
            print(f"  + Ensured index {name}")

        conn.commit()
        print("Migration complete.")
        return 0
    except Exception as exc:
        print(f"Migration failed: {exc}")
        conn.rollback()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
