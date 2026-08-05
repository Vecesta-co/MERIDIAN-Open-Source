# 🧠 MERIDIAN — AI Agent Operations Platform

**Design, run, watch, and trust your AI agents — without writing a framework.**

MERIDIAN is a self-hosted, open-source platform that gives non-engineer AI utilizers and backend teams a single environment to define agent missions as structured workflows, execute them against sandboxed tools, observe every decision in real time, run automated and human evals, enforce approval gates, and iterate with confidence.

---

## 🚀 Quick Start (Phase 0)

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
# Connect to your Postgres instance and execute migrations in order:
# psql -U postgres -d meridian -f migrations/001_types.sql
# psql -U postgres -d meridian -f migrations/002_tables.sql
# psql -U postgres -d meridian -f migrations/003_indexes.sql
# psql -U postgres -d meridian -f migrations/004_triggers.sql

# 6. Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 7. Verify health endpoint
curl http://localhost:8000/health
# Expected: {"status":"healthy","version":"0.1.0","timestamp":"...","database_connected":true}
```

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
pytest -v
# Expected: All tests pass (health returns 200, API routes return 501)
```

### Using Docker Compose (Alternative)

```
bash
docker compose -f infra/docker-compose.yml up -d
# This starts PostgreSQL with auto-migration
# Backend API will be added in later phases
```

---

## 📁 Project Structure

```
meridian/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── missions.py     # Mission CRUD (placeholder)
│   │   │       ├── mission_versions.py  # Mission versioning (placeholder)
│   │   │       ├── steps.py        # Step management (placeholder)
│   │   │       ├── runs.py         # Run execution (placeholder)
│   │   │       ├── tools.py        # Tool registry (placeholder)
│   │   │       ├── approvals.py    # Approval gates (placeholder)
│   │   │       ├── evals.py        # Eval definitions (placeholder)
│   │   │       └── traces.py       # Trace observability (placeholder)
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py           # Pydantic Settings loader
│   │   │   └── logging.py          # Structured logging (no secrets)
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── session.py          # Async DB session management
│   │   │   └── models.py           # SQLAlchemy ORM models
│   │   └── models/
│   │       ├── __init__.py
│   │       └── schemas.py          # Pydantic data contracts
│   ├── migrations/
│   │   ├── 001_types.sql           # Enum types
│   │   ├── 002_tables.sql          # Core tables with FKs
│   │   ├── 003_indexes.sql         # Performance indexes
│   │   └── 004_triggers.sql        # Auto-update triggers
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py             # Test fixtures
│   │   ├── test_health.py          # Health endpoint tests
│   │   └── test_api_v1.py          # 501 placeholder route tests
│   ├── seed.sql                    # Demo seed data
│   └── requirements.txt
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── .env.example
│   ├── pages/
│   │   ├── index.js                # MERIDIAN UI skeleton
│   │   └── _app.js
│   └── styles/
│       └── globals.css
├── infra/
│   └── docker-compose.yml          # Infrastructure (placeholder)
├── docs/
│   └── architecture.md             # System architecture docs
├── .env.example                    # Environment template
├── TODO.md                         # Phase 0 progress tracker
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

### Phase 0 — Not Yet Implemented (501)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/runs` | Run execution — Phase 2 |
| GET/POST | `/api/v1/tools` | Tool registry — Phase 3 |
| GET | `/api/v1/traces` | Trace engine — Phase 4 |
| GET/POST | `/api/v1/evals` | Eval suite — Phase 5 |
| GET/POST | `/api/v1/approvals` | Approval gates — Phase 6 |

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
| 2 | Agent Runtime | 🔜 Planned |
| 3 | Tool Sandbox | 🔜 Planned |
| 4 | Trace Engine | 🔜 Planned |
| 5 | Eval Suite | 🔜 Planned |
| 6 | Approval Gate | 🔜 Planned |
| 7 | Mission Dashboard | 🔜 Planned |
| 8 | Integration Bus | 🔜 Planned |
| 9 | MVP Hardening | 🔜 Planned |

---

## 🔒 Security

- Secrets are NEVER stored or logged in plaintext
- Secrets table uses `storage_type` enum: `env_ref` (environment variable reference) or `encrypted` (ciphertext)
- Logging system includes a `SensitiveDataFilter` that redacts potential secret leakage
- API key authentication will be added in Phase 9

---

## 📄 License

MIT License — see LICENSE file for details.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines (coming in Phase 9).
