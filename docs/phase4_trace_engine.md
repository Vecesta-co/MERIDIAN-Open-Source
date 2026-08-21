# MERIDIAN Phase 4 — Trace Engine

## Overview

Phase 4 delivers the **Trace Engine**: a queryable observability layer that reconstructs a run's execution as a nested span tree, computes cost from token usage via a configurable model pricing table, and provides run-level summaries (duration, tokens, cost, errors).

This phase is **backend-only** — no UI is added. The Trace Engine is exposed via three API endpoints under `/runs/{id}/`.

---

## 1. API Endpoints

### `GET /runs/{id}/trace` — Nested trace tree

Reconstructs the run's spans into a nested tree using `parent_span_id` relationships. Spans without a resolvable parent attach to a synthetic root span for the run (backward compatibility).

**Response shape:**

```
json
{
  "id": "1a2b3c4d-...",
  "run_id": "9f8e7d6c-...",
  "step_id": null,
  "parent_span_id": null,
  "span_type": "system",
  "name": "Run 9f8e7d6c-...",
  "status": "ok",
  "started_at": "2025-01-01T00:00:00Z",
  "ended_at": "2025-01-01T00:00:12Z",
  "duration_ms": 12000.0,
  "model": null,
  "tokens_in": null,
  "tokens_out": null,
  "cost_usd": 0.0,
  "error_text": null,
  "attributes": {
    "mission_id": "abc-...",
    "mission_name": "Research Mission"
  },
  "children": [
    {
      "id": "2b3c4d5e-...",
      "run_id": "9f8e7d6c-...",
      "step_id": "step-1-...",
      "parent_span_id": "1a2b3c4d-...",
      "span_type": "system",
      "name": "step:step_1",
      "status": "ok",
      "started_at": "2025-01-01T00:00:01Z",
      "ended_at": "2025-01-01T00:00:06Z",
      "duration_ms": 5000.0,
      "model": null,
      "tokens_in": null,
      "tokens_out": null,
      "cost_usd": 0.0,
      "error_text": null,
      "attributes": {},
      "children": [
        {
          "id": "3c4d5e6f-...",
          "run_id": "9f8e7d6c-...",
          "step_id": "step-1-...",
          "parent_span_id": "2b3c4d5e-...",
          "span_type": "llm_step",
          "name": "llm:step_1",
          "status": "ok",
          "started_at": "2025-01-01T00:00:01.5Z",
          "ended_at": "2025-01-01T00:00:05Z",
          "duration_ms": 3500.0,
          "model": "gpt-4o-mini",
          "tokens_in": 15,
          "tokens_out": 8,
          "cost_usd": 0.00007,
          "error_text": null,
          "attributes": {},
          "children": []
        }
      ]
    }
  ]
}
```

### `GET /runs/{id}/summary` — Run summary

Aggregates duration, tokens, cost, error counts, and per-step success across all spans in the run.

**Response shape:**

```json
{
  "run_id": "9f8e7d6c-...",
  "status": "completed",
  "duration_ms": 12000.0,
  "span_count": 5,
  "error_count": 0,
  "total_tokens_in": 30,
  "total_tokens_out": 16,
  "total_tokens": 46,
  "cost_usd": 0.00014,
  "steps": [
    {
      "step_id": "step-1-...",
      "step_key": "step_1",
      "status": "completed",
      "attempts": 1,
      "errors": 0,
      "duration_ms": 5000.0,
      "tokens_in": 15,
      "tokens_out": 8,
      "cost_usd": 0.00007
    }
  ]
}
```

### `GET /runs/{id}/spans?type=tool|llm_step` — Flat filtered span list

Returns a flat list of spans for the run, optionally filtered by `span_type`. Ordered by `started_at ASC` for stable pagination.

**Response shape:**

```
json
[
  {
    "id": "3c4d5e6f-...",
    "run_id": "9f8e7d6c-...",
    "step_id": "step-1-...",
    "parent_span_id": "2b3c4d5e-...",
    "span_type": "llm_step",
    "name": "llm:step_1",
    "status": "ok",
    "started_at": "2025-01-01T00:00:01.5Z",
    "ended_at": "2025-01-01T00:00:05Z",
    "duration_ms": 3500.0,
    "model": "gpt-4o-mini",
    "tokens_in": 15,
    "tokens_out": 8,
    "cost_usd": 0.00007,
    "error_text": null,
    "attributes": {}
  }
]
```

---

## 2. Cost Calculation

### Approach: Compute on request (documented)

Cost is **computed on summary/trace request**, not stored at write time. Rationale:

- **Pricing changes don't require rewriting spans** — model prices change frequently; recomputing on read keeps historical data accurate to current pricing.
- **No write-path overhead** — span writes stay fast; cost is only paid when a consumer requests it.
- **Backward compatible** — spans recorded before Phase 4 (without `cost_usd` or token columns) are still charged correctly because cost is derived from `meta_json` token data.

### Pricing table (`backend/app/core/config.py`)

```
python
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini":       {"input_per_1k": 0.00015, "output_per_1k": 0.00060},
    "gpt-4o":            {"input_per_1k": 0.00250, "output_per_1k": 0.01000},
    "claude-3-5-sonnet": {"input_per_1k": 0.00300, "output_per_1k": 0.01500},
    "claude-3-haiku":    {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
    "gemini-1.5-pro":    {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
    "default":           {"input_per_1k": 0.00010, "output_per_1k": 0.00010},
}
```

### Formula

```
cost = (tokens_in / 1000) * input_per_1k + (tokens_out / 1000) * output_per_1k
```

Unknown models fall back to the `"default"` pricing entry. If no token counts are present, cost is `0.0`.

---

## 3. Trace Tree Reconstruction

### Algorithm (`backend/app/services/trace_service.py`)

1. **Query** all spans for the run ordered by `started_at ASC` (stable, index-backed).
2. **Index** spans by `id` in a dict.
3. **Build adjacency** — for each span, add it to its parent's `children` list (using `parent_span_id`).
4. **Find roots** — spans whose `parent_span_id` is `None` OR whose parent is not in the span set (orphans).
5. **Synthesize** — if multiple roots exist, or if the run span is not the sole root, a synthetic root span is created whose children are all the roots. This handles:
   - Runs with no run span (Phase 2 edge case)
   - Orphan spans with dangling `parent_span_id`
   - Multiple top-level spans (future DAG/parallelism)

### Forward compatibility with DAG/parallelism

The tree structure is built entirely from `parent_span_id` relationships — it does **not** assume linear execution. A future parallel execution can emit spans with the same parent (sibling branches) and the tree will faithfully represent them. The synthetic-root fallback also means a future DAG with multiple top-level nodes still renders correctly.

---

## 4. Indexing & Performance

### Query patterns optimized

| Query | Index | Purpose |
|-------|-------|---------|
| Fetch all spans for a run, ordered by start time | `idx_spans_run_started (run_id, started_at)` | Primary trace-tree + summary query; covers ordering + filtering by run |
| Find spans by parent (tree walk) | `idx_spans_parent (parent_span_id)` | Fast child lookup during tree reconstruction |
| Filter spans by type within a run | `idx_spans_run_type (run_id, span_type)` | `/spans?type=tool` filtering |

### Migration (`backend/migrations/007_phase4_trace_engine.sql`)

```
sql
CREATE INDEX IF NOT EXISTS idx_spans_run_started ON spans (run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans (parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_run_type ON spans (run_id, span_type);
```

These are **composite** indexes where appropriate — the leading column (`run_id`) makes the single-run filter selective, and the trailing column supports the ordering/type filter without a separate sort.

---

## 5. Backwards-Compatibility Plan

### Spans inserted before Phase 4 (no `parent_span_id`)

Phases 2 and 3 recorded spans with `parent_span_id` only for LLM/tool spans (which referenced their step span). Step spans and the run span had `parent_span_id = NULL`.

**Handling:**

1. **New columns** — `span_type`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `attributes`, `duration_ms` are added as nullable columns with defaults. Existing rows get `NULL`/`0` values.
2. **Backfill** — the migration backfills `span_type` from `kind`:
   - `run` → `system`
   - `step` → `system`
   - `llm` → `llm_step`
   - `tool` → `tool`
   - `eval` → `eval`
   - `approval` → `approval`
3. **Token/model backfill from `meta_json`** — the migration copies `meta_json->'model'`, `meta_json->'tokens'->'prompt'`, and `meta_json->'tokens'->'completion'` into the dedicated columns where present.
4. **Trace tree** — whatever `parent_span_id` values exist are honored. Orphans (no parent or dangling parent) attach to the synthetic root. No data migration of parent relationships is required — the tree is reconstructed on read.
5. **Cost** — computed on read from the pricing table, so even legacy spans without token columns cost `0.0` (no crash, no NaN).

---

## 6. Large-Run Stress Considerations (5)

### 1. Span volume / unbounded tree depth

A long run with hundreds of steps and thousands of spans produces a deeply nested tree.

**Mitigation:** The `/spans` endpoint returns a **flat** list (no nesting) for debugging, keeping payloads small. The `/summary` endpoint aggregates in the DB or via a single pass, never materializing the full tree. The `/trace` endpoint is intended for interactive inspection of a single run — if a run produces an extreme span count, the flat `/spans` endpoint with type filtering is the recommended debugging path.

### 2. Payload size (full tree JSON)

A run with 10,000 spans serializes to a large JSON tree.

**Mitigation:** Responses exclude `input_json`/`output_json` (the heavy fields) by default — only metadata (ids, timestamps, tokens, cost, status) is returned. If full I/O payloads are needed, they should be fetched per-span via a future `/spans/{id}` endpoint. This keeps the tree response small and fast.

### 3. Query performance on large span tables

As the `spans` table grows across many runs, unindexed queries degrade.

**Mitigation:** Composite indexes `(run_id, started_at)` and `(run_id, span_type)` make single-run queries selective. The tree reconstruction performs **one** indexed query for the whole run, then builds the tree in memory (O(n)) — no N+1 queries.

### 4. Concurrent trace reads during active runs

While a run is executing, spans are being written concurrently with trace reads.

**Mitigation:** Reads use a consistent snapshot (single SELECT with `ORDER BY started_at`). The tree builder tolerates partially-written runs (a span with no `ended_at` contributes `duration_ms = None` and is rendered as in-progress). No locks are taken — readers never block writers.

### 5. Pagination / partial loading

For very large runs, returning the entire tree or span list in one response is impractical.

**Mitigation:** The `/spans` endpoint is designed to be **paginated** (add `limit`/`offset` or cursor params in a future iteration) with stable `started_at ASC` ordering. The `/trace` endpoint supports **depth-limiting** (only return the top N levels) as a future enhancement. The `/summary` endpoint is always O(1)-ish — it aggregates in a single pass and never returns raw spans.

---

## 7. Files Modified

| File | Change |
|------|--------|
| `backend/migrations/007_phase4_trace_engine.sql` | Added span columns + indexes |
| `backend/app/db/models.py` | Span model: added `span_type`, `duration_ms`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `attributes` + indexes |
| `backend/app/core/config.py` | Added `MODEL_PRICING` table |
| `backend/app/services/trace_service.py` | New: trace tree, summary, cost calc, span filtering |
| `backend/app/models/schemas.py` | Added `SpanNode`, `TraceTreeResponse`, `RunSummaryResponse`; extended `SpanResponse` |
| `backend/app/api/v1/runs.py` | Implemented `/trace`, `/summary`, added `/spans` |
| `backend/app/services/run_service.py` | Wired `parent_span_id` + populated new span columns |
| `backend/tests/test_phase4_trace.py` | New: trace/summary/spans/cost tests |
| `backend/tests/test_phase2_runtime.py` | Updated 501 assertions → 200 |
| `backend/tests/test_api_v1.py` | Removed trace/summary from 501 list |

## No UI Added

This phase is **backend-only**. No frontend/UI changes were made. The Trace Engine is exposed via the API (`GET /runs/{id}/trace`, `GET /runs/{id}/summary`, `GET /runs/{id}/spans`).
