"""
MERIDIAN Worker — Phase 2 Agent Runtime.

RQ worker that consumes run-execution jobs from Redis and executes
them asynchronously. This is the background process that runs the
mission steps.

Worker choice rationale (RQ vs Celery):
  - RQ is simpler, lighter, and sufficient for Phase 2's sequential
    execution model. It uses Redis directly with no extra broker config.
  - Celery adds a lot of infrastructure (beats, result backends, etc.)
    that we don't need yet. We can migrate to Celery later if we need
    distributed workers, cron scheduling, or more complex routing.

Startup:
  python -m app.services.worker

Requires Redis running (REDIS_URL env var).
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

from redis import Redis
from rq import Queue
from rq.worker import Worker

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_factory

logger = get_logger(__name__)

# Redis connection — lazily initialised so that importing this module
# does not block on a Redis connection (important for tests and for
# the API server which may run without Redis; the run stays pending
# and is picked up by the reaper when Redis comes back).
REDIS_URL = settings.REDIS_URL or "redis://localhost:6379/0"
_redis_conn = None
_run_queue = None


def _get_redis() -> Redis:
    """Return a lazily-created Redis connection."""
    global _redis_conn
    if _redis_conn is None:
        # Short socket timeout so that enqueue fails fast when Redis
        # is not running (important for tests and graceful degradation).
        _redis_conn = Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_conn


def _get_queue() -> Queue:
    """Return a lazily-created RQ queue."""
    global _run_queue
    if _run_queue is None:
        _run_queue = Queue("meridian-runs", connection=_get_redis())
    return _run_queue


def enqueue_run(run_id: uuid.UUID) -> str:
    """
    Enqueue a run for execution.

    Returns the RQ job ID.
    """
    job = _get_queue().enqueue(
        "app.services.worker.execute_run_job",
        str(run_id),
        job_timeout=3600,  # 1 hour max per run
    )
    logger.info("Enqueued run %s as job %s", run_id, job.id)
    return job.id


def execute_run_job(run_id_str: str) -> dict:
    """
    RQ job entrypoint. Runs the async execution in a fresh event loop.

    This is a sync function (RQ workers are sync by default).
    We create a new event loop and run the async execute_run.
    """
    run_id = uuid.UUID(run_id_str)
    logger.info("Worker executing run %s", run_id)

    async def _run():
        async with async_session_factory() as db:
            from app.services.run_service import execute_run
            return await execute_run(db, run_id)

    try:
        run = asyncio.run(_run())
        return {
            "run_id": str(run.id),
            "status": run.status,
            "error_summary": run.error_summary,
        }
    except Exception as exc:
        logger.exception("Worker failed to execute run %s: %s", run_id, exc)
        # Mark the run as failed
        async def _fail():
            async with async_session_factory() as db:
                from app.db.models import Run
                run = await db.get(Run, run_id)
                if run and run.status not in ("completed", "failed", "cancelled", "timed_out"):
                    run.status = "failed"
                    run.ended_at = datetime.now(timezone.utc)
                    run.error_summary = f"Worker error: {str(exc)}"
                    await db.commit()
        asyncio.run(_fail())
        return {
            "run_id": str(run_id),
            "status": "failed",
            "error_summary": f"Worker error: {str(exc)}",
        }


def run_worker() -> None:
    """
    Start an RQ worker listening on the meridian-runs queue.
    """
    logger.info("Starting MERIDIAN RQ worker (queue: meridian-runs)")
    worker = Worker([_get_queue()], connection=_get_redis())
    worker.work()


if __name__ == "__main__":
    run_worker()
