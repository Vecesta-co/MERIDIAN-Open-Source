# MERIDIAN — Contributing Guide

Thank you for contributing to MERIDIAN! This guide outlines the process and standards for contributing code, documentation, and tests.

## Development Workflow

### 1. Fork and Branch

```bash
# Clone your fork
git clone https://github.com/yourusername/meridian.git
cd meridian

# Create a feature branch
git checkout -b feature/amazing-feature
```

### 2. Set Up Development Environment

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start infrastructure services (Postgres + Redis, optional for local dev)
docker compose up -d

# Verify services are healthy
docker compose ps
```

Runs are normally executed by the RQ worker (Redis). For a single-process demo
without Redis, set `EXECUTE_RUNS_IN_PROCESS=true` in your environment — runs are
then executed directly in the API process when enqueueing fails. See `seed_demo.py`
for a self-contained demo that does this.

### 3. Make Your Changes

- Follow existing code patterns and conventions
- Add type annotations to all public functions
- Write docstrings for new modules, classes, and functions
- Add or update tests in `tests/` to cover new functionality
- Run the full test suite to check for regressions

### 4. Code Style

- **Formatter**: `ruff format` (Black-compatible)
- **Linter**: `ruff check`
- **Type checking**: `mypy app/`
- All Python files must have module-level and public-function docstrings

### 5. Run Tests

```bash
python -m pytest tests/ -v
```

> **Note**: The full suite (`python -m pytest tests/`) is green — all tests pass with no
> external dependencies. Tests use an in-memory SQLite database and never touch Redis,
> Postgres, or external APIs. New code must not introduce additional failures.

### 6. Commit and Push

```bash
git add .
git commit -m "feat: add amazing feature"

# Push to your fork
git push origin feature/amazing-feature
```

### 7. Pull Request

- Open a PR against the main repository
- Fill the PR template
- Ensure all CI checks pass
- Address reviewer feedback
- Merge after approval

## Project Structure

```
meridian/
├── app/                    # Main application source
│   ├── api/               # API routes (v1)
│   ├── core/              # Configuration, settings
│   ├── db/                # Database layer
│   ├── services/          # Business logic services
│   └── main.py            # FastAPI app entry point
├── tests/                 # Test suite
├── docker-compose.yml     # Docker service orchestration
├── Dockerfile             # API/worker container definition
├── seed_demo.py           # Demo data seeding script
├── README.md              # User documentation
└── CONTRIBUTING.md        # This file
```

## Adding New Features

### API Routes

- Place routes in `app/api/v1/` as modular routers
- All non-health routes require `X-API-Key` header authentication
- Use `db: AsyncSession` dependency for database access
- Apply `rate_limit_middleware` to write endpoints

### New Services

- Add service logic in `app/services/`
- Register lifespan startup/shutdown events in `app/main.py`
- Add Docker service in `docker-compose.yml` if needed
- Add healthcheck

### Database Models

- Define models in `app/db/models.py` or per-app models
- Use SQLAlchemy 2.0 style (`select()`, `update()`, `delete()`)
- Keep migrations in mind for schema changes

### Testing

- Use `httpx.AsyncClient` with `ASGITransport` for integration tests
- Use the `TestSessionFactory` from `tests/conftest.py` for database sessions
- Mark tests with appropriate categories
- Test both success and error paths

## Review Checklist

PRs must satisfy these requirements before merge:

- [ ] All new and modified functions have type annotations
- [ ] All new and modified functions have docstrings
- [ ] `ruff check app/` passes (linting)
- [ ] `ruff format app/` passes (formatting)
- [ ] `mypy app/` passes (no new type errors)
- [ ] `python -m pytest tests/` — no new failures introduced
- [ ] API key auth is correctly applied (or intentionally bypassed for dev)
- [ ] Rate limiting is applied to write endpoints (or intentionally exempt)
- [ ] Docker services start cleanly with `docker compose up -d`
- [ ] Documentation (README) updated if user-facing behavior changed

## Thank You!

Your contribution helps make MERIDIAN better for everyone. Thanks for taking the time to follow these guidelines.