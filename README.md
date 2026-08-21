# 🧠 MERIDIAN — AI Agent Operations Platform

**Contributed by Vecesta.co**

**Design, run, watch, and trust your AI agents — without writing a framework.**

MERIDIAN is a self-hosted, open-source platform that gives non-engineer AI utilizers and backend teams a single environment to define agent missions as structured workflows, execute them against sandboxed tools, observe every decision in real time, run automated and human evals, enforce approval gates, and iterate with confidence.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ (or Supabase account)
- pip / virtualenv
- Node.js 18+ (for frontend)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/your-org/meridian.git
cd meridian

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
cd backend
pip install -r requirements.txt

# 4. Copy environment configuration
cp ../.env.example .env
# Edit .env with your database credentials

# 5. Run database migrations
# With PostgreSQL: psql -U postgres -d meridian -f migrations/001_types.sql
# (repeat for 002_tables.sql, 003_indexes.sql, 004_triggers.sql)
# For a quick start without Postgres, SQLite is also supported (see .env).

# 6. Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. Verify health endpoint
curl http://localhost:8000/health
# Expected: {"status":"healthy","version":"0.1.0","timestamp":"...","database_connected":true}
```

### Run the Self-Contained Demo

```bash
# From the backend directory
python seed_demo.py
```

The demo creates a published mission (tool + approval steps, so no LLM API key is
needed), creates a run, and executes it **in-process** — no Redis or worker required.
The run executes the tool steps and pauses at the approval gate, printing the approval
ID to approve via `POST /api/v1/approvals/{id}/decide`.

### Run the Full Stack (Docker Compose)

```bash
docker compose -f backend/docker-compose.yml up -d
```

This starts PostgreSQL, Redis, the API, an RQ worker, and the dashboard UI.
The API is configured with `EXECUTE_RUNS_IN_PROCESS=true` so runs also progress
even if the worker/Redis is unavailable.

### Frontend (Next.js Skeleton)

```
bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
# Open http://localhost:3000 in your browser
# You should see the MERIDIAN UI skeleton page
```

### Running Tests

```
bash
# From the backend directory
cd backend
python -m pytest tests -q
# Expected: 265 passed — the suite is fully green with no external
# dependencies (in-memory SQLite, no Redis/Postgres/API calls).
```

### Using Docker Compose (Alternative)

```
bash
docker compose -f backend/docker-compose.yml up -d
# Full stack: PostgreSQL + Redis + API + worker + dashboard UI.
# The older infra/docker-compose.yml only provides PostgreSQL.
```

---

## 📁 Project Structure

```
meridian/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app entry point + middleware (auth, rate limit)
│   │   ├── api/
│   │   │   └── v1/                 # missions, mission_versions, runs, tools, approvals, evals, traces
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic Settings loader (.env)
│   │   │   └── logging.py          # Structured logging with SensitiveDataFilter
│   │   ├── db/
│   │   │   ├── session.py          # Async DB session management
│   │   │   └── models.py           # SQLAlchemy ORM models
│   │   ├── models/
│   │   │   └── schemas.py          # Pydantic data contracts
│   │   ├── services/               # run_service, mission_service, tool_service, eval_service, trace_service, worker
│   │   └── tools/
│   │       ├── base.py             # BaseTool (TypeAdapter-based JSON schema)
│   │       ├── registry.py         # Tool registry + dispatch/validation
│   │       └── builtins/           # http_request, supabase_crud, supabase_query, firecrawl_scrape, browseuse_action, rag_query
│   ├── migrations/
│   │   ├── 001_types.sql           # Enum types
│   │   ├── 002_tables.sql          # Core tables with FKs
│   │   ├── 003_indexes.sql         # Performance indexes
│   │   └── 004_triggers.sql        # Auto-update triggers
│   ├── tests/                      # 265 tests, fully green (in-memory SQLite)
│   ├── ui/                         # Static dashboard (nginx-served)
│   ├── seed_demo.py                # Self-contained demo (in-process run execution)
│   ├── docker-compose.yml          # Full stack: postgres + redis + api + worker + ui
│   └── requirements.txt
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── .env.example
│   └── pages/                      # MERIDIAN UI skeleton (Next.js)
├── infra/
│   └── docker-compose.yml          # Infrastructure (PostgreSQL only)
├── docs/
│   └── architecture.md             # System architecture docs
├── seed.sql                        # Demo seed data
├── .env.example                    # Environment template
├── TODO.md                         # Progress tracker
└── README.md
```

---

## 🗄️ Database Schema

### Enum Types

| Enum | Values |
|------|--------|
| `mission_state` | draft, published, archived |
| `run_status` | pending, running, awaiting_approval, paused, completed, failed, cancelled, timed_out |
| `step_status` | pending, running, completed, failed, skipped, cancelled, timed_out |
| `approval_status` | pending, approved, rejected, expired |
| `span_kind` | run, step, llm, tool, eval, approval, system |
| `span_status` | ok, error, cancelled |
| `step_kind` | llm, tool, approval |
| `secret_storage_type` | env_ref, encrypted |
| `eval_target` | run, step, tool |

### Core Tables (12 total)

1. **missions** — Agent mission definitions
2. **mission_versions** — Versioned mission snapshots
3. **steps** — Individual workflow steps
4. **runs** — Execution instances
5. **run_steps** — Per-run per-step tracking
6. **spans** — Trace/observability records
7. **tools** — Tool registry metadata
8. **approvals** — Human-in-the-loop records
9. **eval_definitions** — Quality check configs
10. **eval_results** — Eval scores and verdicts
11. **secrets** — Encrypted credential storage
12. **agents** — Agent definitions (lightweight registry)

---

## 🌐 API Endpoints

### Phase 1 — Mission Designer (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | `/health` | ✅ 200 | Health check |
| POST | `/api/v1/missions` | ✅ 201 | Create mission (JSON or YAML) |
| GET | `/api/v1/missions` | ✅ 200 | List missions (paginated) |
| GET | `/api/v1/missions/{id}` | ✅ 200 | Get mission with steps |
| PUT | `/api/v1/missions/{id}` | ✅ 200 | Update draft mission (increments version) |
| POST | `/api/v1/missions/{id}/publish` | ✅ 200 | Publish mission (locks edits) |
| POST | `/api/v1/missions/{id}/clone` | ✅ 200 | Clone mission (draft v1) |
| GET | `/api/v1/missions/{id}/yaml` | ✅ 200 | Export mission as YAML |
| POST | `/api/v1/missions/validate` | ✅ 200/400 | Validate payload without saving |
| DELETE | `/api/v1/missions/{id}` | 🔧 501 | Delete mission (Phase 5+) |

### Phase 2 — Agent Runtime (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| POST | `/api/v1/runs` | ✅ 201 | Create a run from a published mission |
| GET | `/api/v1/runs` | ✅ 200 | List runs (newest first) |
| GET | `/api/v1/runs/{id}` | ✅ 200 | Get run detail with steps and spans |
| GET | `/api/v1/runs/{id}/steps` | ✅ 200 | Get run's steps ordered by order_index |
| POST | `/api/v1/runs/{id}/cancel` | ✅ 200 | Cancel a run (sets cancel_requested) |
| GET | `/api/v1/runs/{id}/trace` | ✅ 200 | Full run trace tree (Phase 4) |
| GET | `/api/v1/runs/{id}/summary` | ✅ 200 | Run summary with step attempts (Phase 4) |
| GET | `/api/v1/runs/{id}/evals` | ✅ 200 | Eval results for a run (Phase 5) |

### Phase 3 — Tool Sandbox (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | `/api/v1/tools` | ✅ 200 | List registered tools |
| POST | `/api/v1/tools/execute` | ✅ 200 | Execute a tool with validated input |

### Phase 4 — Trace Engine (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | `/api/v1/runs/{id}/trace` | ✅ 200 | Full run trace tree with cycle detection |
| GET | `/api/v1/runs/{id}/summary` | ✅ 200 | Run summary (step attempts, duration, cost) |
| GET | `/api/v1/runs/{id}/spans` | ✅ 200 | Raw span records for a run |
| GET | `/api/v1/runs/failing-steps` | ✅ 200 | Cross-run step failure aggregation |
| GET | `/api/v1/runs/failing-steps/{step_id}/runs` | ✅ 200 | Runs where a specific step failed |

### Phase 5 — Eval Suite (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | `/api/v1/evals` | ✅ 200 | List eval definitions |
| POST | `/api/v1/evals` | ✅ 201 | Create an eval definition |
| GET | `/api/v1/evals/{id}` | ✅ 200 | Get an eval definition |
| PUT | `/api/v1/evals/{id}` | ✅ 200 | Update an eval definition |
| DELETE | `/api/v1/evals/{id}` | ✅ 204 | Delete an eval definition |
| GET | `/api/v1/runs/{id}/evals` | ✅ 200 | Eval results for a run |
| POST | `/api/v1/runs/{id}/evals/run` | ✅ 200 | Manually re-run attached evals (terminal runs only) |

### Phase 6 — Approval Gate (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | `/api/v1/approvals` | ✅ 200 | List approvals (optionally filtered by status) |
| GET | `/api/v1/approvals/{id}` | ✅ 200 | Get an approval with context and trace |
| POST | `/api/v1/approvals/{id}/decide` | ✅ 200 | Approve / reject / modify an approval |

### Phase 8 — Integration Bus (Implemented)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| POST | `/api/v1/tools/n8n-webhook/{mission_id}` | ✅ 200 | N8N webhook — creates a run from a published mission (HMAC-signed, replay-protected) |

### Phase 5+ — Not Yet Implemented (501)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/evals/results` | Global eval results listing — Phase 5+ |
| GET | `/api/v1/evals/results/{id}` | Get an eval result — Phase 5+ |
| POST | `/api/v1/tools` | Tool registration — Phase 5+ |
| GET/PUT/DELETE | `/api/v1/tools/{id}` | Tool management — Phase 5+ |
| DELETE | `/api/v1/missions/{id}` | Delete mission — Phase 5+ |

### Mission Designer Curl Examples

**1. Create Mission (JSON)**

```bash
curl -X POST http://localhost:8000/api/v1/missions \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Research Assistant",
    "goal": "Research a topic and produce a summary",
    "steps": [
      {
        "key": "research",
        "name": "Research",
        "step_type": "llm",
        "agent_key": "agent_1",
        "prompt_template": "Research the topic: {{topic}}",
        "max_retries": 2,
        "timeout_seconds": 120
      },
      {
        "key": "summarize",
        "name": "Summarize",
        "step_type": "llm",
        "agent_key": "agent_1",
        "prompt_template": "Summarize: {{research.output}}",
        "order_index": 1
      }
    ]
  }'
```

**2. Create Mission (YAML)**

```bash
curl -X POST http://localhost:8000/api/v1/missions \
  -H "Content-Type: application/json" \
  -d '{
    "yaml_text": "version: \"1.0\"\nmission:\n  name: \"YAML Mission\"\n  goal: \"Test YAML creation\"\n  version: 1\n  status: draft\n\nagents:\n  - key: \"agent_1\"\n    name: \"Agent One\"\n    model: \"gpt-4o\"\n\nsteps:\n  - key: \"research\"\n    name: \"Research\"\n    step_type: \"llm\"\n    agent_key: \"agent_1\"\n    prompt_template: \"Research the topic\"\n"
  }'
```

**3. List Missions**

```bash
curl http://localhost:8000/api/v1/missions
```

**4. Get Mission**

```bash
curl http://localhost:8000/api/v1/missions/{mission_id}
```

**5. Update Mission (increments version to v2)**

```bash
curl -X PUT http://localhost:8000/api/v1/missions/{mission_id} \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Mission Name",
    "steps": [
      { "key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Do step 1" }
    ]
  }'
```

**6. Publish Mission**

```bash
curl -X POST http://localhost:8000/api/v1/missions/{mission_id}/publish
```

**7. Clone Mission**

```bash
curl -X POST http://localhost:8000/api/v1/missions/{mission_id}/clone
```

**8. Export Mission as YAML**

```bash
curl http://localhost:8000/api/v1/missions/{mission_id}/yaml
```

**9. Validate Mission Payload**

```bash
curl -X POST http://localhost:8000/api/v1/missions/validate \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test",
    "goal": "Test goal",
    "steps": [
      { "key": "step_1", "name": "Step 1", "step_type": "llm", "agent_key": "agent_1" }
    ]
  }'
# Valid → 200 {"valid": true, "errors": []}
# Invalid → 400 {"valid": false, "errors": [...]}
```

---

## 🧪 Development

```bash
# Run tests
cd backend
pytest -v

# Run specific test file
pytest tests/test_health.py -v

# Run with coverage (when implemented)
pytest --cov=app tests/

# Lint check
ruff check .

# Type check
mypy .
```

---

## 📋 Phase Roadmap

| Phase | Name | Status |
|-------|------|--------|
| 0 | Foundation & Data Contracts | ✅ Complete |
| 1 | Mission Designer | ✅ Complete |
| 2 | Agent Runtime | ✅ Complete |
| 3 | Tool Sandbox | ✅ Complete |
| 4 | Trace Engine | ✅ Complete |
| 5 | Eval Suite | ✅ Complete |
| 6 | Approval Gate | ✅ Complete |
| 7 | Mission Dashboard | ✅ Complete |
| 8 | Integration Bus (N8N webhook) | ✅ Complete |
| 9 | MVP Hardening (auth, rate limiting) | ✅ Complete |

---

## 🔒 Security

- API key authentication on all non-health routes (`X-API-Key` header), compared
  using constant-time `hmac.compare_digest` to prevent timing attacks. Localhost
  requests in dev mode (`DEBUG=true`) are exempt.
- Rate limiting on write endpoints (sliding window, `429` with `Retry-After`),
  disabled in debug mode.
- N8N webhook requests are authenticated with an HMAC signature and replay
  protection (timestamp + nonce).
- Secrets are NEVER stored or logged in plaintext
- Secrets table uses `storage_type` enum: `env_ref` (environment variable reference) or `encrypted` (ciphertext)
- Logging system includes a `SensitiveDataFilter` that redacts potential secret leakage
- HTTP exception handlers sanitize internal error details unless `DEBUG=true`

---

## 📄 License

MIT License — see LICENSE file for details.

---

## 🤝 Contributing

**Contributed by Vecesta.co**

See [CONTRIBUTING.md](backend/CONTRIBUTING.md) for development workflow, code style, and testing guidelines.
