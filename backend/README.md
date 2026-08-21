# MERIDIAN

**MERIDIAN** — a mission execution platform that coordinates LLM agents, HTTP tools, and approvals into repeatable workflows.

[![Docker](https://img.shields.io/docker/badge/meridian-api?style=flat-square)](https://docs.docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

## Phase 9 — Hardening, Docker, Auth, Docs, Seed Demo

This phase makes MERIDIAN reliably runnable by a non-coder with `docker compose up`, secure for local/prosumer usage, and documented with a working demo mission.

---

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [Starting the Platform](#starting-the-platform)
4. [Running the Demo Mission](#running-the-demo-mission)
5. [API Usage Examples](#api-usage-examples)
6. [Troubleshooting](#troubleshooting)
7. [Production Risks (Phase 9.7)](#production-risks-phase-97)

---

## 1. Installation

### Prerequisites

- [Docker](https://docs.docker.com/engine/install/) and [Docker Compose](https://docs.docker.com/compose/install/)
- [Python 3.12+](https://www.python.org/downloads/) (for running seed script or development)

### Quick Install

```bash
# Clone the repository
git clone https://github.com/yourorg/meridian.git
cd meridian

# Start all services with Docker Compose
docker compose up -d

# Wait for services to be healthy
docker compose ps
```

---

## 2. Configuration

### Environment Variables

Copy `.env` and adjust as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `MERIDIAN_API_KEY` | `dev-api-key-localhost-bypass` | API key for `X-API-Key` header auth |
| `API_KEY` | `dev-api-key-localhost-bypass` | Legacy alias |
| `POSTGRES_DB` | `meridian` | PostgreSQL database name |
| `POSTGRES_USER` | `meridian_user` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `meridian_pass` | PostgreSQL password |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `DEBUG` | `True` | Set `False` for production; enables localhost API key bypass |
| `STALE_RUN_THRESHOLD_MINUTES` | `30` | Watchdog threshold for marking stale runs failed |
| `WRITE_RATE_LIMIT_PER_MIN` | `60` | Max write operations per minute per IP |
| `WRITE_RATE_LIMIT_PER_SEC` | `10` | Max write operations per second per IP |
| `WRITE_RATE_BURST` | `20` | Max burst write operations |
| `MERIDIAN_WEBHOOK_SECRET` | `dev-webhook-secret` | Shared secret for webhook authentication |

### API Key Authentication

All non-health routes require an `X-API-Key` header:

```bash
curl -X POST http://localhost:8000/api/v1/missions \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Mission", "goal": "Test", "steps": [...]}'
```

**Localhost bypass**: When `DEBUG=True`, the API key check is skipped for localhost IPs (`127.0.0.1`, `::1`, `192.168.0.0/16`, `10.0.0.0/8`). This lets developers test without setting the API key while in development mode.

For production, set `DEBUG=False` and provide `MERIDIAN_API_KEY` — all requests must include the header.

---

## 3. Starting the Platform

### Docker Compose

```bash
docker compose up -d
```

This starts 5 services:

| Service | Port | Description |
|---|---|---|
| `api` | 8000 | FastAPI application (main entry point) |
| `ui` | 3000 | Frontend interface (React) |
| `postgres` | 5432 | PostgreSQL database |
| `redis` | 6379 | Redis (rate limiting, watchdog locking) |
| `worker` | N/A | Background task processor |

**Health checks**: All services include `healthcheck` with `condition: service_healthy`. Use `docker compose ps` to verify all are `Up` and `healthy`.

**Restart policy**: `restart: unless-stopped` on all services ensures they recover after daemon restarts.

### Without Docker (Development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MERIDIAN_API_KEY=dev-api-key-localhost-bypass
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 4. Running the Demo Mission

### Seed the Demo Data

```bash
python seed_demo.py
```

This creates:

1. **Mission**: "Demo Mission: Content Review" with 3 steps:
   - **Step 1** (LLM): Generate content via agent1
   - **Step 2** (Tool): HTTP request to example.com
   - **Step 3** (Approval): Human approval required
2. **Eval Definition**: "Content Quality Check" with rules (word_count 200-500, has_approval = true)
3. **Mission**: Published and ready for runs
4. **Run**: Created and ready to execute

### Demo Walkthrough (Manual UI)

1. **Start the platform**: `docker compose up -d`
2. **Wait for services**: `docker compose ps` — all should be `healthy`
3. **Seed the demo**: `python seed_demo.py`
4. **View the UI**: Open http://localhost:3000
5. **Create a run**: Navigate to "Missions" → "Demo Mission: Content Review" → "Start Run"
6. **Monitor execution**: Watch the run progress through steps
7. **Approve at Step 3**: When the approval step is reached, review the generated content and approve
8. **View results**: After completion, see the eval score and output data

### API Walkthrough

```bash
# 1. Create a mission (requires API key when DEBUG=False)
curl -X POST http://localhost:8000/api/v1/missions \
  -H "X-API-Key: dev-api-key-localhost-bypass" \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Mission: Content Review", "goal": "Review and approve generated content", "description": "Demo mission for Phase 9 integration", "steps": [{"key": "step1", "name": "Generate Content", "step_type": "llm", "agent_key": "agent1", "prompt_template": "Generate a 300-word blog post about AI trends.", "order_index": 0}, {"key": "step2", "name": "Review Content", "step_type": "tool", "tool_refs": [{"tool_name": "http_request", "config": {"method": "GET", "url": "https://example.com"}}], "approval_required": False, "order_index": 1}, {"key": "step3", "name": "Approve Content", "step_type": "approval", "approval_required": True, "order_index": 2}]}

# 2. Create an eval definition
curl -X POST http://localhost:8000/api/v1/evals \
  -H "X-API-Key: dev-api-key-localhost-bypass" \
  -H "Content-Type: application/json" \
  -d '{"name": "Content Quality Check", "scope": "run", "eval_type": "rule_based", "config": {"rules": [{"field": "word_count", "min": 200, "max": 500}]}, "threshold": 0.8}'

# 3. Publish the mission
curl -X POST http://localhost:8000/api/v1/missions/{mission_id}/publish \
  -H "X-API-Key: dev-api-key-localhost-bypass"

# 4. Create a run
curl -X POST http://localhost:8000/api/v1/runs \
  -H "X-API-Key: dev-api-key-localhost-bypass" \
  -H "Content-Type: application/json" \
  -d '{"mission_id": "{mission_id}", "input_context": {"demo": true}}'
```

---

## 5. API Usage Examples

### Health Check (no API key required)

```bash
curl http://localhost:8000/healthz
```

### List Missions

```bash
curl -H "X-API-Key: dev-api-key-localhost-bypass" http://localhost:8000/api/v1/missions
```

### Create a Mission

```bash
curl -X POST "/api/v1/missions" \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Mission", "goal": "Do something", "steps": [{"key": "s1", "step_type": "llm", "agent_key": "a1", "prompt_template": "Say hello", "order_index": 0}]}'
```

### Rate Limiting

Write endpoints are rate-limited per-IP:

- **Per minute**: `WRITE_RATE_LIMIT_PER_MIN` (default: 60)
- **Per second**: `WRITE_RATE_LIMIT_PER_SEC` (default: 10)
- **Burst allowance**: `WRITE_RATE_BURST` (default: 20)

If rate limited, you'll receive `429` with `Retry-After` header:

```bash
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

---

## 6. Troubleshooting

### Docker Compose Issues

| Problem | Solution |
|---|---|
| Services not starting | Run `docker compose logs` to see error output |
| API not responding | Check `docker compose ps` — ensure `api` is `healthy` |
| Database connection errors | Verify `.env` PostgreSQL credentials match |
| Rate limiting blocks | Wait for rate limit window; reduce concurrent requests |
| API key auth failing | Ensure `X-API-Key` header is sent; set `DEBUG=False` for production |

### Common Errors

- **`401 Unauthorized`**: Missing or invalid `X-API-Key` header
- **`429 Too Many Requests`**: Rate limit exceeded — wait and retry
- **`500 Internal Server Error`**: Check `docker compose logs api` for traceback
- **`connection refused`**: Ensure `docker compose up -d` has completed and services are healthy

### API Key Bypass Not Working

If `DEBUG=False` and you're getting auth errors on localhost:

- The bypass only applies to `127.0.0.1`, `::1`, `192.168.0.0/16`, `10.0.0.0/8`
- Ensure your client is connecting from one of these IPs
- In production, always provide a valid `X-API-Key` header

---

## 7. Production Risks (Phase 9.7)

The following 12 risks are documented for MVP readiness. Each is labeled as **accepted-for-MVP** (known issue to monitor) or **fixed-now** (requires code change before production deployment).

| # | Risk | Category | Status | Mitigation |
|---|---|---|---|---|
| 1 | Single-threaded worker processes concurrent runs sequentially | Reliability | accepted-for-MVP | Scale worker instances for concurrent processing |
| 2 | No TLS/HTTPS termination — HTTP only in Docker dev | Security | accepted-for-MVP | Add Traefik/NGINX reverse proxy with TLS for production |
| 3 | In-memory rate limiter lost on restart | Reliability | accepted-for-MVP | Persist rate limits in Redis; upgrade to token bucket filter |
| 4 | No database connection pooling overflow protection | Reliability | fixed-now | Set `pool_size` and `max_overflow` in SQLAlchemy config |
| 5 | Seed demo uses test client only — no real auth flow | Usability | accepted-for-MVP | Document manual auth steps; add OAuth2 later |
| 6 | Watchdog threshold hardcoded at 30 minutes | Reliability | accepted-for-MVP | Make configurable via `STALE_RUN_THRESHOLD_MINUTES` env var |
| 7 | No authentication audit logging | Security | fixed-now | Add request logging with API key identity tracking |
| 8 | CORS configured for all origins (`*`) | Security | fixed-now | Restrict to specific frontend origin in production |
| 9 | No firewall/ingress protection — all ports exposed | Security | accepted-for-MVP | Add network policies; expose only required ports |
| 10 | Database credentials in `.env` not rotated | Security | fixed-now | Implement secret rotation via Vault or similar |
| 11 | No graceful shutdown handler for worker processes | Reliability | accepted-for-MVP | Add SIGTERM handler to complete in-flight runs |
| 12 | Eval results not persisted to database — lost on restart | Reliability | fixed-now | Store eval scores and run outcomes durably |

### Accepted-for-MVP Risks (Monitor After Launch)

These 6 risks are known but deemed acceptable for initial MVP launch, with documented monitoring:

1. **Single-threaded worker** — Expected for Phase 1; scaling planned in Phase 2
2. **No TLS** — Docker dev only; production uses reverse proxy with Let's Encrypt
3. **In-memory rate limits** — Redis-backed upgrade planned; acceptable for low-traffic prosumer use
4. **Seed demo auth** — Test-only workflow; manual API key entry documented for real usage
5. **Watchdog threshold configurable** — 30-min default is reasonable for demo; tunable per deployment
6. **No graceful shutdown** — Worker will complete current step then exit; acceptable for short runs

### Fixed-Now Risks (Must Fix Before Production)

These 6 risks must be addressed before deploying MERIDIAN to production:

1. **DB connection pooling** — Must set appropriate pool sizes to prevent exhaustion
2. **Audit logging** — Must track which API key made which request for accountability
3. **CORS restrictions** — Must lock down to specific frontend origins
4. **Secret rotation** — Must implement automated credential rotation
5. **Eval result persistence** — Must durably store eval outcomes for audit and monitoring
6. **Eval persistence** — Results must not be lost on service restart

---

## Contributing

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

### Development Setup

```bash
# Clone and set up
git clone https://github.com/yourorg/meridian.git
cd meridian
cp .env.example .env
docker compose up -d

# Run tests
python -m pytest tests/ -v

# Seed demo
python seed_demo.py

# Lint
ruff check app/
ruff format app/

# Type check
mypy app/
```

### Adding New Features

1. Follow the existing code patterns in `app/`
2. Add API routes in `app/api/v1/`
3. Update Dockerfile if adding Python dependencies
4. Update `docker-compose.yml` if adding new services
5. Add tests in `tests/`
6. Update README with new functionality
7. Run the full test suite to verify no regressions

### Code Style

- Black-formatted Python (`ruff format`)
- Type annotations on all public functions
- Docstrings for all modules, classes, and public functions
- Tests must pass before merge

---

## License

MERIDIAN is open source software licensed under the [MIT License](https://opensource.org/licenses/MIT).