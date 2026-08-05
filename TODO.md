# MERIDIAN Phase 1 — Implementation Progress

## ✅ Completed: Audit Fix Implementation

### Critical Fix 1: YAML Schema Enforcement
- [x] `backend/app/services/yaml_service.py` — Added `validate_step_dependencies`, `validate_tool_step_refs`, `validate_agent_references`
- [x] Wired into `validate_yaml_workflow` before agent section validation

### Critical Fix 2: Version Immutability
- [x] Service-layer guard confirmed (published missions return 403 on update)
- [x] DB UniqueConstraint `(mission_id, version_int)` exists in migration + ORM

### Critical Fix 3: Clone Independence
- [x] Verified — clone produces independent draft v1 with independent steps (no action needed)

### Critical Fix 4: order_index DB Constraint
- [x] `backend/app/db/models.py` — Added `UniqueConstraint("mission_version_id", "order_index", name="uq_steps_mission_version_order")` + `Index("idx_steps_mission_version_order", ...)` to `Step.__table_args__`
- [x] Synced with `backend/migrations/005_phase1_mission_designer.sql`

### Critical Fix 5: depends_on Validation
- [x] `backend/app/services/mission_service.py` — Added `_validate_step_dependencies` (type/existence/cycle via DFS)
- [x] `backend/app/services/yaml_service.py` — Added `validate_step_dependencies` (same rules)
- [x] Wired into JSON create, JSON update, and YAML validate paths

## ✅ Completed: Tests Added (111 total, all passing)
- [x] Create-path validation parity tests (missing goal, llm w/o agent_key, malformed tool_refs, tool step w/o tool_refs)
- [x] depends_on validation tests (unknown key, self-ref, circular, non-list, valid DAG)
- [x] agent_key cross-reference tests (JSON + YAML paths)
- [x] YAML path validation parity tests
- [x] order_index duplicate rejection test
- [x] `backend/tests/test_phase1_missions.py` — 26 original + 19 new tests = 45 tests

## ✅ Completed: Edge-Case Verification (13 New Tests)

### Update-Path Validation Parity
- [x] PUT with replacement steps lacking `goal` → 400 (full-replacement contract)
- [x] PUT with llm step lacking `agent_key` → 400
- [x] PUT with tool step lacking `tool_refs` → 400
- [x] PUT with circular `depends_on` → 400
- [x] PUT with `depends_on` unknown key → 400
- [x] PUT with undefined agent reference → 400

### PUT with Duplicate order_index
- [x] Duplicate `order_index` in replacement steps → rejected (clean 400 with "order"/"index")
- [x] Unique `order_index` → 200, version increments to 2

### YAML-Created Mission Flows
- [x] GET YAML-created mission → correct steps (research first)
- [x] Update YAML-created mission → 200, version 2
- [x] Clone YAML-created mission → independent draft v1, "(Copy)" name
- [x] YAML export roundtrip → contains "YAML Mission", "research", "summarize"

### Full-Replacement Update Contract
- [x] PUT payload validated as-is (no silent merge of existing mission fields)

## ✅ Verification
- [x] Full pytest suite: **123 passed, 0 failed** (2.04s)
- [x] Live API verification: **50 passed, 0 failed**
- [x] Files modified: `mission_service.py` (full-replacement validation, `_validate_unique_order_indices`), `yaml_service.py` (duplicate order_index ValidationError), `test_phase1_missions.py` (13 new tests + payload fixes)

## ✅ Phase 1: COMPLETE
