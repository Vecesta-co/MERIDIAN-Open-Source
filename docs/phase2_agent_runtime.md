# MERIDIAN Phase 2 — Agent Runtime

This document describes the Phase 2 **Agent Runtime** module: the sequential execution engine, the background worker, the run lifecycle API, and the failure-handling model.

---

## 1. State Machine

### Run States

```
                    ┌────────────┐
                    │  pending   │
                    └─────┬──────┘
                          │ worker picks up
                          ▼
                    ┌────────────┐
        ┌──────────▶│  running   │◀──────────┐
        │           └─────┬──────┘           │ cancel_requested
        │                 │                  │ detected between
        │                 │                  │ steps
        │                 ├──────────────┐   │
        │                 │              │   │
        │                 ▼              ▼   │
        │           ┌──────────┐   ┌──────────┐
        │           │completed │   │ cancelled│
        │           └──────────┘   └──────────┘
        │                 ▲
        │                 │
        └── watchdog ─────┘
        (stale running    │
         > threshold)     │
                          ▼
                    ┌──────────┐
                    │  failed  │
                    └──────────┘
```

**Transitions:**

| From | Event / Condition | To |
|------|-------------------|----|
| `pending` | Worker begins execution | `running` |
| `running` | All steps completed | `completed` |
| `running` | A step fails after exhausting retries | `failed` |
| `running` | `cancel_requested` flag set; checked between steps | `cancelled` |
| `running` | Watchdog (`reap_stale_runs`) finds run stale (> threshold) | `failed` |
| `running` | Global run deadline exceeded (sum of step timeouts + margin) | `timed_out` |
| `pending` | Watchdog leaves it pending (worker will pick up when Redis returns) | `pending` |

> `awaiting_approval` and `paused` are reserved for Phase 6 (Approval Gate) — not reachable in Phase 2.

### RunStep States

```
                    ┌────────────┐
                    │  pending   │
                    └─────┬──────┘
                          │ execution starts
                          ▼
                    ┌────────────┐
                    │  running   │
                    └─────┬──────┘
                          │
              ┌───────────┼───────────┐
              │           │           │
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │completed │ │  failed  │ │ cancelled│
        └──────────┘ └──────────┘ └──────────┘
```

**Transitions:**

| From | Event / Condition | To |
|------|-------------------|----|
| `pending` | Step execution begins | `running` |
| `running` | Step returns success | `completed` |
| `running` | Step fails all retries | `failed` |
| `running` | Cancellation requested during step | `cancelled` |
| `pending` | Run cancelled between steps | `cancelled` |

---

## 2. Worker Choice Rationale: RQ vs Celery

**Selected: RQ (Redis Queue)**

| Consideration | RQ | Celery |
|---------------|----|--------|
| **Complexity** | Minimal — a single `Queue` + `Worker` | Heavy — brokers, result backends, beats, multiple worker types |
| **Setup** | One Redis connection + one worker process | Requires broker config, result backend, more moving parts |
| **Suitability** | Perfect for Phase 2's sequential execution model | Overkill for a single sequential queue |
| **Distributed workers** | Not built-in | Built-in (would be useful later) |
| **Cron/scheduling** | Not built-in | Built-in (Celery Beat) |
| **Migration path** | Easy to swap later | N/A |

**Conclusion:** RQ is the right choice for Phase 2 — it's simple, lightweight, and sufficient for sequential mission execution. We can migrate to Celery in a later phase if we need distributed workers, cron scheduling, or complex routing.

---

## 3. Endpoint Behaviors + Example Requests

### `POST /api/v1/runs` — Create a run

Creates a `pending` run from a **published** mission and enqueues it on the worker (best-effort — if Redis is down, the run stays pending and is picked up later).

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{
    "mission_id": "3f2a1c8e-9b4a-4d6e-8f0a-2b5c7d9e1f3a",
    "input_context": {"topic": "AI agents"}
  }'
```

**Success (201):**
```json
{
  "id": "9c8b7a6e-5d4c-4b3a-2a1f-0e9d8c7b6a54",
  "mission_id": "3f2a1c8e-9b4a-4d6e-8f0a-2b5c7d9e1f3a",
  "mission_version_id": "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d",
  "status": "pending",
  "cancel_requested": false,
  "triggered_by": "manual",
  "created_at": "2026-08-05T21:31:52Z"
}
```

**Errors:**
- `400` — Mission is a draft (not published): `{"detail": "Mission 'X' is not published (state=draft). Only published missions can be run."}`
- `404` — Mission not found: `{"detail": "Mission <id> not found"}`

### `GET /api/v1/runs` — List runs

Returns the most recent runs, newest first.

```bash
curl http://localhost:8000/api/v1/runs
```

**Success (200):** `[ { ...RunResponse... }, ... ]`

### `GET /api/v1/runs/{run_id}` — Get run detail

Returns the run with its steps (`run_steps`) and spans.

```bash
curl http://localhost:8000/api/v1/runs/9c8b7a6e-5d4c-4b3a-2a1f-0e9d8c7b6a54
```

**Success (200):**
```json
{
  "id": "9c8b7a6e-5d4c-4b3a-2a1f-0e9d8c7b6a54",
  "status": "completed",
  "run_steps": [
    {
      "id": "11111111-1111-1111-1111-111111111111",
      "status": "completed",
      "attempt_count": 1,
      "step_key": "step_1",
      "step_name": "Step 1",
      "step_kind": "llm",
      "order_index": 0,
      "output_json": {"text": "...", "model": "gpt-4o-mini"}
    }
  ],
  "spans": [ { "kind": "run", "status": "ok" }, ... ]
}
```

**Errors:**
- `404` — Run not found: `{"detail": "Run <id> not found"}`

### `GET /api/v1/runs/{run_id}/steps` — Get run steps

Returns the run's steps ordered by `order_index`.

```bash
curl http://localhost:8000/api/v1/runs/9c8b7a6e-5d4c-4b3a-2a1f-0e9d8c7b6a54/steps
```

**Success (200):** `[ { ...RunStepDetailResponse... }, ... ]`

### `POST /api/v1/runs/{run_id}/cancel` — Cancel a run

Sets `cancel_requested=true`. The worker checks this between steps and marks the run `cancelled`.

```bash
curl -X POST http://localhost:8000/api/v1/runs/9c8b7a6e-5d4c-4b3a-2a1f-0e9d8c7b6a54/cancel
```

**Success (200):** `{ "id": "...", "cancel_requested": true, ... }`

**Errors:**
- `400` — Run already in terminal state: `{"detail": "Run <id> is already in terminal state 'completed'"}`
- `404` — Run not found

### Placeholder endpoints (still 501 — Phase 4+)

- `GET /api/v1/runs/{run_id}/trace` — Trace Engine (Phase 4)
- `GET /api/v1/runs/{run_id}/summary` — Summary (Phase 4)
- `GET /api/v1/runs/{run_id}/evals` — Eval Suite (Phase 5)

---

## 4. Failure Modes Checklist (8+ items)

| # | Failure Mode | Detection | Recovery |
|---|--------------|-----------|----------|
| 1 | **Worker process crashes** mid-run | Run stuck in `running`; watchdog `reap_stale_runs()` runs periodically | Watchdog marks run `failed` with error summary; no stuck runs |
| 2 | **Redis goes down** during enqueue | `enqueue_run()` raises (2s socket timeout) | API catches exception, logs warning; run stays `pending`; reaper/next enqueue picks it up when Redis returns |
| 3 | **LLM call times out** | `TimeoutError` raised by `call_llm()` | Retried with exponential backoff (1s, 2s, 4s...) up to `max_retries` |
| 4 | **LLM transient failure** (rate limit, network) | `RuntimeError` raised | Same retry/backoff logic |
| 5 | **LLM persistent failure** after retries | Final `RuntimeError` | Step marked `failed`; run marked `failed` with error summary |
| 6 | **Tool step executed** | `tool_service.execute_tool()` returns non-ok result | `NonRetryableStepError` raised → step/run `failed` immediately (no wasted retries; Tool Sandbox in Phase 3) |
| 7 | **Cancellation during step execution** | `cancel_requested` checked before each attempt | Step marked `cancelled`; run marked `cancelled` |
| 8 | **Cancellation between steps** | `cancel_requested` checked in main loop | Pending steps marked `cancelled`; run marked `cancelled` |
| 9 | **Unknown step kind** | `_execute_step_with_retry()` else branch | Step/run `failed` with "Unknown step kind" |
| 10 | **Run created but never enqueued** (Redis down at POST) | Run stays `pending` with no worker pickup | Reaper/next enqueue picks it up; or manual re-enqueue |
| 11 | **DB session failure during execution** | Exception propagates to `worker.execute_run_job()` | Worker catches, marks run `failed` with error summary |
| 12 | **Global run timeout exceeded** | `datetime.now(timezone.utc) >= run_deadline` (sum of step timeouts + margin) | Run marked `timed_out`; remaining pending/running steps marked `timed_out` |

---

## 5. Mandatory Self-Check

### 5.1 Run Recovery on Worker Crash

**Scenario:** The worker process crashes mid-run (e.g. after step 1 completes, before step 2).

**Recovery flow:**
1. The run is left in `running` status with `started_at` set to when execution began.
2. A separate watchdog process (or scheduled job) calls `reap_stale_runs()`.
3. The watchdog queries for runs where `status == 'running'` AND `started_at < now() - STALE_RUN_THRESHOLD_MINUTES` (default 30 min).
4. Any match is marked `failed` with error summary: `"Run exceeded stale threshold (30 min) — worker likely crashed. Marked failed by watchdog."`
5. `ended_at` is set, and the run is no longer stuck.

**Result:** No run is left in an inconsistent `running` state forever. The reaper guarantees crash recovery.

### 5.2 Tool Calls Are Stubbed

**Confirmed:** Tool execution is **NOT implemented** in Phase 2.

- `tool_service.execute_tool()` always returns:
  
```json
  {
    "ok": false,
    "error": "tool_not_implemented",
    "message": "Tool 'web_search' is not implemented in Phase 2 (Tool Sandbox arrives in Phase 3)",
    "tool_name": "web_search"
  }
  
```
- `run_service._execute_tool_step()` calls this stub and raises `NonRetryableStepError` if `ok` is false.
- This means any mission containing a tool step will fail that step immediately (no retries wasted) until Phase 3.
- Test `test_tool_step_returns_not_implemented` confirms this behavior.

---

## 6. Scope Constraints (Phase 2)

| Feature | Status |
|---------|--------|
| Sequential execution engine | ✅ Implemented |
| Background worker (RQ) | ✅ Implemented |
| LLM step execution (LiteLLM) | ✅ Implemented |
| Cancellation | ✅ Implemented |
| Retry with exponential backoff | ✅ Implemented |
| Crash recovery (watchdog) | ✅ Implemented |
| Global run timeout | ✅ Implemented |
| Context truncation (MAX_CONTEXT_CHARS) | ✅ Implemented |
| Trace spans (basic, incl. model/tokens) | ✅ Implemented |
| Tool sandbox | ❌ **Stub only** (Phase 3) |
| Approval gates | ❌ **Placeholder only** (Phase 6) |
| Eval suite | ❌ **Placeholder hook only** (Phase 5) |
