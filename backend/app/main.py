"""
MERIDIAN — AI Agent Operations Platform
FastAPI Application Entry Point

Phase 0: Foundation & Data Contracts
- Health endpoint at /health
- All v1 routers mounted at /api/v1/
- API key auth on all non-health routes (Phase 9)
- All non-health routes return 501 Not Implemented
"""

import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    missions,
    mission_versions,
    steps,
    runs,
    tools,
    approvals,
    evals,
    traces,
)
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.db.session import check_db_connection, async_session_factory
from app.models.schemas import HealthResponse

# Setup logging on import
setup_logging()
logger = get_logger(__name__)


# ──────────────────────────────────────────────
# API Key Auth Middleware (Phase 9)
# ──────────────────────────────────────────────

# Localhost CIDR ranges that bypass API key auth in dev mode
LOCALHOST_CIDR = ["127.0.0.1/32", "::1/128", "192.168.0.0/16", "10.0.0.0/8"]

def _is_localhost(host: str) -> bool:
    """Check if the request host is a localhost address."""
    # Simple check - in production you'd use ipaddress module
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.") or host.startswith("10.")


def _check_api_key(request: Request) -> bool:
    """Check if the request has a valid API key or is localhost-bypassed."""
    # Skip auth for health endpoint
    if request.url.path == "/health":
        return True

    # Skip auth for localhost in dev mode
    if settings.DEBUG and _is_localhost(request.client.host or ""):
        return True

    # Check API key header
    provided_key = request.headers.get("X-API-Key", "")
    expected_key = settings.MERIDIAN_API_KEY

    # If API key is not configured and we're not in localhost bypass mode, reject
    if expected_key is None:
        if settings.DEBUG:
            # In dev without API key configured, allow all (but document this)
            return True
        return False

    # In production, require valid API key using constant-time comparison
    # to prevent timing attacks on the API key secret.
    if not hmac.compare_digest(provided_key, expected_key):
        return False

    return True


# ═════════════════════════════════════════════════════════════════════════
# Application factory
# ═════════════════════════════════════════════════════════════════════════


def create_app() -> FastAPI:
    """Application factory for MERIDIAN backend."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Startup ──
        logger.info(
            "MERIDIAN API v%s starting up",
            settings.APP_VERSION,
        )
        # Local dev with SQLite (no Postgres): create tables on startup.
        # Production (Postgres) uses migrations under backend/migrations/.
        if settings.DATABASE_URL.startswith("sqlite"):
            from app.db.models import Base
            from app.db.session import engine

            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                logger.info("SQLite dev mode: tables created/verified")
            except Exception as exc:
                logger.error("Failed to create SQLite tables on startup: %s", str(exc))

        # Start the watchdog background task (Phase 2 Agent Runtime).
        # It periodically reaps stale 'running' runs that were left behind
        # by crashed workers.
        app.state.watchdog_task = asyncio.create_task(_watchdog_loop())
        logger.info(
            "Watchdog started (interval=%ss, stale_threshold=%smin)",
            settings.WATCHDOG_INTERVAL_SECONDS,
            settings.STALE_RUN_THRESHOLD_MINUTES,
        )

        yield

        # ── Shutdown ──
        watchdog_task = getattr(app.state, "watchdog_task", None)
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("MERIDIAN API shutting down")

    app = FastAPI(
        title="MERIDIAN API",
        description="AI Agent Operations Platform — Backend API",
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    """
MERIDIAN — AI Agent Operations Platform
FastAPI Application Entry Point

Phase 0: Foundation & Data Contracts
- Health endpoint at /health
- All v1 routers mounted at /api/v1/
- API key auth on all non-health routes (Phase 9)
- Rate limiting on write endpoints (Phase 9)
- All non-health routes return 501 Not Implemented
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    missions,
    mission_versions,
    steps,
    runs,
    tools,
    approvals,
    evals,
    traces,
)
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.db.session import check_db_connection, async_session_factory
from app.models.schemas import HealthResponse

# Setup logging on import
setup_logging()
logger = get_logger(__name__)

# ──────────────────────────────────────────────
# Rate Limiting (Phase 9)
# ──────────────────────────────────────────────

# Simple in-memory rate limiter for prosumer deployments without Redis.
# Tracks request counts per client IP with a sliding window.
# Rate limits are applied as HTTP 429 responses.

#: Maximum number of write requests per minute per client
WRITE_RATE_LIMIT_PER_MIN = 60

#: Maximum number of write requests per second per client
WRITE_RATE_LIMIT_PER_SEC = 10

#: Burst allowance above the per-second limit
WRITE_RATE_BURST = 20

# Per-client request timestamps (IP -> list of unix timestamps, sliding window)
_rate_limit_state: dict = {}


def _check_rate_limit(client_ip: str) -> tuple[bool, int | None]:
    """
    Check if the client IP has exceeded write rate limits.

    Uses a sliding window of request timestamps per client IP.
    Returns (allowed, retry_after_seconds). Allowed is True if under limit.
    """
    import time as _time

    now = _time.time()
    timestamps = _rate_limit_state.setdefault(client_ip, [])

    # Drop requests older than the 60-second window
    cutoff = now - 60
    while timestamps and timestamps[0] <= cutoff:
        timestamps.pop(0)

    timestamps.append(now)

    # Per-second limit (with burst allowance)
    sec_cutoff = now - 1
    count_sec = sum(1 for ts in timestamps if ts >= sec_cutoff)
    if count_sec > WRITE_RATE_LIMIT_PER_SEC + WRITE_RATE_BURST:
        return False, 1

    # Per-minute limit
    if len(timestamps) > WRITE_RATE_LIMIT_PER_MIN:
        retry_after = max(1, int(60 - (now - timestamps[0])))
        return False, retry_after

    return True, None


# ═════════════════════════════════════════════════════════════════════════
# Application factory
# ═════════════════════════════════════════════════════════════════════════


def create_app() -> FastAPI:
    """Application factory for MERIDIAN backend."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Startup ──
        logger.info(
            "MERIDIAN API v%s starting up",
            settings.APP_VERSION,
        )
        # Local dev with SQLite (no Postgres): create tables on startup.
        # Production (Postgres) uses migrations under backend/migrations/.
        if settings.DATABASE_URL.startswith("sqlite"):
            from app.db.models import Base
            from app.db.session import engine

            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                logger.info("SQLite dev mode: tables created/verified")
            except Exception as exc:
                logger.error("Failed to create SQLite tables on startup: %s", str(exc))

        # Start the watchdog background task (Phase 2 Agent Runtime).
        # It periodically reaps stale 'running' runs that were left behind
        # by crashed workers.
        app.state.watchdog_task = asyncio.create_task(_watchdog_loop())
        logger.info(
            "Watchdog started (interval=%ss, stale_threshold=%smin)",
            settings.WATCHDOG_INTERVAL_SECONDS,
            settings.STALE_RUN_THRESHOLD_MINUTES,
        )

        yield

        # ── Shutdown ──
        watchdog_task = getattr(app.state, "watchdog_task", None)
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("MERIDIAN API shutting down")

    app = FastAPI(
        title="MERIDIAN API",
        description="AI Agent Operations Platform — Backend API",
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Before-request middleware: API key auth (Phase 9) ──
    @app.middleware("http")
    async def api_key_auth_middleware(request: Request, call_next):
        """Require X-API-Key header for all non-health routes."""
        if not _check_api_key(request):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid X-API-Key header. Set MERIDIAN_API_KEY env var or provide the header.",
            )
        response = await call_next(request)
        return response

    # ── Before-request middleware: Rate limiting (Phase 9) ──
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Apply rate limiting to write endpoints."""
        # Rate limiting is disabled in debug mode (local dev, test suites).
        # This mirrors the API key auth bypass and keeps dev friction-free.
        if settings.DEBUG:
            response = await call_next(request)
            return response

        # Only apply rate limiting to write endpoints
        write_paths = [
            "/api/v1/missions",
            "/api/v1/runs",
            "/api/v1/evals",
            "/api/v1/traces",
            "/api/v1/tools",
            "/api/v1/approvals",
        ]

        # Check if this is a write endpoint that needs rate limiting
        is_write_endpoint = any(
            request.url.path.startswith(prefix) for prefix in write_paths
        ) or request.method in ["POST", "PUT", "DELETE"]

        if is_write_endpoint:
            client_ip = request.client.host or "unknown"
            allowed, retry_after = _check_rate_limit(client_ip)

            if not allowed:
                # Return the 429 directly instead of raising inside the
                # middleware: BaseHTTPMiddleware does not route raised
                # HTTPExceptions through the registered handlers.
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded. Please wait before making another write request."
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        response = await call_next(request)
        return response

    # ── Health endpoint ──
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["health"],
        summary="Health check",
        description="Returns the current health status of the API, including database connectivity.",
    )
    async def health_check():
        db_healthy = await check_db_connection()
        return HealthResponse(
            status="healthy" if db_healthy else "degraded",
            version=settings.APP_VERSION,
            timestamp=datetime.now(timezone.utc),
            database_connected=db_healthy,
        )

    # Exception handlers to prevent stack trace leakage in production
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        if settings.DEBUG:
            detail = exc.detail
        else:
            detail = "An error occurred. Please try again later."
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request, exc):
        if settings.DEBUG:
            return JSONResponse(
                status_code=500,
                content={"detail": f"Internal error: {str(exc)}"},
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"detail": "An unexpected error occurred."},
            )

    # ── Mount v1 routers ──
    app.include_router(missions.router, prefix="/api/v1")
    app.include_router(mission_versions.router, prefix="/api/v1")
    app.include_router(steps.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(evals.router, prefix="/api/v1")
    app.include_router(traces.router, prefix="/api/v1")

    return app


async def _watchdog_loop() -> None:
    """
    Background task that periodically reaps stale running runs and
    purges old spans (trace retention policy).

    Runs every WATCHDOG_INTERVAL_SECONDS. Uses a fresh DB session per
    sweep to avoid holding a connection open across the whole loop.
    """
    from app.services.run_service import purge_old_spans, reap_stale_runs

    while True:
        try:
            async with async_session_factory() as db:
                reaped = await reap_stale_runs(db)
                if reaped:
                    logger.info("Watchdog reaped %d stale run(s)", reaped)

                # Trace retention: purge spans older than TRACE_RETENTION_DAYS
                purged = await purge_old_spans(db)
                if purged:
                    logger.info("Watchdog purged %d old span(s)", purged)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Watchdog sweep failed: %s", str(exc))
        await asyncio.sleep(settings.WATCHDOG_INTERVAL_SECONDS)


app = create_app()
