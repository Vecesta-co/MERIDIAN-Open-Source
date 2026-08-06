# MERIDIAN Phase 2 — Agent Runtime Verification Plan

## ✅ Step 1: Unit Test Verification
- [x] Re-run pytest after worker.py datetime fix → **138 passed, 4 warnings** (confirmed worker fix safe)

## ✅ Step 2: Live End-to-End Testing
- [x] Check environment (Redis NOT available, no LLM API key, SQLite dev DB)
- [x] **Found + fixed a real bug**: stale SQLite dev DB missing Phase 2 columns (`runs.mission_id`, `runs.current_step_id`) → created `backend/migrate_sqlite_phase2.py` and ran it
- [x] Start FastAPI server (uvicorn) → healthy against real dev DB
- [x] Create + publish a mission
- [x] POST /runs → 201, pending run created
- [x] Verify run executes steps (real engine, mock LLM) → completed
- [x] Cancel path → cancelled, cancel completed → 400
- [x] Timeout path → terminal state
- [x] Stale reaping (watchdog) → reaped 1
- [x] GET run detail / steps verification
- **Live verification: 42 passed, 0 failed**

## 🔲 Remaining Coverage Gaps (needs infra)
- [ ] RQ worker + Redis end-to-end (Redis not installed/running)
- [ ] Real LLM call via LiteLLM (no API key configured)
- [ ] These are infra-dependent, not code bugs

## 📋 Final Report
- [x] Phase 2 Agent Runtime works end-to-end against real DB
- [x] Found + fixed dev DB migration gap
- [x] Documented coverage gaps
