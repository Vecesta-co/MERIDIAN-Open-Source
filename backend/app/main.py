"""
MERIDIAN — AI Agent Operations Platform
FastAPI Application Entry Point

Phase 0: Foundation & Data Contracts
- Health endpoint at /health
- All v1 routers mounted at /api/v1/
- All non-health routes return 501 Not Implemented
"""

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from app.models.schemas import HealthResponse, NotImplementedResponse

# Setup logging on import
setup_logging()
logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Application factory for MERIDIAN backend."""
    app = FastAPI(
        title="MERIDIAN API",
        description="AI Agent Operations Platform — Backend API",
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    # ── Mount v1 routers ──
    app.include_router(missions.router, prefix="/api/v1")
    app.include_router(mission_versions.router, prefix="/api/v1")
    app.include_router(steps.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(evals.router, prefix="/api/v1")
    app.include_router(traces.router, prefix="/api/v1")

    # ── Startup / Shutdown events ──
    @app.on_event("startup")
    async def startup_event():
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

    @app.on_event("shutdown")
    async def shutdown_event():
        # Cancel the watchdog task on shutdown
        watchdog_task = getattr(app.state, "watchdog_task", None)
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("MERIDIAN API shutting down")

    return app


async def _watchdog_loop() -> None:
    """
    Background task that periodically reaps stale running runs.

    Runs every WATCHDOG_INTERVAL_SECONDS. Uses a fresh DB session per
    sweep to avoid holding a connection open across the whole loop.
    """
    from app.services.run_service import reap_stale_runs

    while True:
        try:
            async with async_session_factory() as db:
                reaped = await reap_stale_runs(db)
                if reaped:
                    logger.info("Watchdog reaped %d stale run(s)", reaped)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Watchdog sweep failed: %s", str(exc))
        await asyncio.sleep(settings.WATCHDOG_INTERVAL_SECONDS)


app = create_app()
