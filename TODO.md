# MERIDIAN Phase 5 — Eval Suite Implementation Progress

## ✅ Phase 4 Complete (all critical fixes verified — 234 tests)

## Phase 5 Scope
- [x] Data model: `eval_definitions` (scope run|step|tool_span, target_step_key,
      eval_type rule_based|schema|llm_judge, config, threshold, mission_id, tags)
      + `eval_results` (verdict pass|fail, nullable score, evidence) + `missions.tags`
      (`models.py`, `migrations/008_phase5_eval_suite.sql`)
- [x] API: POST/GET/PUT/DELETE `/evals`, GET `/runs/{id}/evals`,
      POST `/runs/{id}/evals/run` (manual rerun) (`evals.py`, `runs.py`)
- [x] Eval execution engine: non-blocking, reads run artifacts only, never
      re-runs the mission (`eval_service.py`)
- [x] Evaluators: rule_based (contains_any/all, not_contains), schema (jsonschema),
      llm_judge (prompt template, score_range, threshold verdict)
- [x] Attachment: by `mission_id` or tag overlap with the mission's tags
- [x] Post-run hook enqueues eval job when Redis is configured; no-op otherwise
      (evals never block execution) (`run_service.py`, `worker.py`)
- [x] Eval trace spans: `kind="eval"` linked to run_id + parent run span
- [x] Tests: 37 new (rule-based, schema, mocked llm_judge, CRUD, rerun,
      eval spans, 8 edge cases) (`test_phase5_evals.py`, `test_phase5_evals_edge.py`)
- [x] Full suite green (234 passed), mypy clean (37 files), ruff clean

## Phase 5+ (out of scope)
- [ ] Global eval results listing: GET `/evals/results`, GET `/evals/results/{id}`
- [ ] Approval Gate (Phase 6), Mission Dashboard (Phase 7), Integration Bus (Phase 8)
