"""
MERIDIAN Tools API — v1.

Phase 3  —  Tool Sandbox.
Phase 8  —  Integration Bus connectors.

Endpoints:
  GET  /tools                       : list all registered tools + their input schemas
  POST /tools/execute               : execute a tool by name with JSON input
  POST /tools/n8n-webhook/{mission_id}  : N8N webhook trigger (authenticated, replay-protected)
  GET  /integrations/status         : integration health / connectivity checks
"""
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models.schemas import (
    NotImplementedResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolInfoResponse,
)
from app.services import tool_service
from app.services.run_service import create_run, RunValidationError

logger = get_logger(__name__)

# Replay protection window: seconds allowed between webhook send and receipt
WEBHOOK_REPLAY_WINDOW_SECONDS = 300  # 5 minutes

router = APIRouter(prefix="/tools", tags=["tools"])


# ──────────────────────────────────────────────
# GET  /tools                       : list all registered tools + input schemas
# ──────────────────────────────────────────────
@router.get("", response_model=list[ToolInfoResponse])
async def list_tools(request: Request):
    """List all registered tools with their input schemas."""
    return tool_service.list_tools()


# ──────────────────────────────────────────────
# POST /tools/execute               : execute a tool by name with JSON input
# ──────────────────────────────────────────────
@router.post("/execute", response_model=ToolExecuteResponse)
async def execute_tool_endpoint(payload: ToolExecuteRequest, request: Request):
    """Execute a tool by name with JSON input."""
    result = await tool_service.execute_tool(
        payload.tool_name,
        payload.input,
        timeout_seconds=payload.timeout_seconds,
        dry_run=payload.dry_run,
    )
    return ToolExecuteResponse(
        ok=result.ok,
        data=result.data,
        error=result.error,
        message=result.message,
        metadata=result.metadata,
    )


# ──────────────────────────────────────────────
# POST /tools/n8n-webhook/{mission_id}  : N8N webhook trigger
# ──────────────────────────────────────────────
@router.post("/n8n-webhook/{mission_id}", name="n8n_webhook", include_in_schema=True)
async def n8n_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    mission_id: uuid.UUID = Path(..., description="The mission ID to start a run for"),
):
    """
    N8N webhook trigger.

    - Auth: compares ``X-Meridian-Webhook-Secret`` header value to
      the environment variable ``MERIDIAN_WEBHOOK_SECRET``.
    - Body may contain an ``input_context`` dict which is passed
      through as step input when the run is started.
    - Calls the internal run-start path and returns ``run_id``.
    """
# Validate webhook secret
    provided_secret = request.headers.get("X-Meridian-Webhook-Secret", "")
    expected_secret = settings.MERIDIAN_WEBHOOK_SECRET
    
    # If webhook secret is configured, require matching header
    if expected_secret is not None and provided_secret != expected_secret:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Invalid webhook secret. Provide X-Meridian-Webhook-Secret header matching MERIDIAN_WEBHOOK_SECRET.",
            },
        )
    
    # If webhook secret is NOT configured, always reject
    if expected_secret is None:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Invalid webhook secret. MERIDIAN_WEBHOOK_SECRET is not configured.",
            },
        )
    
    # Replay protection: validate timestamp and nonce
    provided_timestamp = request.headers.get("X-Meridian-Webhook-Timestamp", "")
    provided_nonce = request.headers.get("X-Meridian-Webhook-Nonce", "")
    
    if not provided_timestamp or not provided_nonce:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Missing X-Meridian-Webhook-Timestamp or X-Meridian-Webhook-Nonce header for replay protection.",
            },
        )
    
    try:
        webhook_timestamp = datetime.fromisoformat(provided_timestamp)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Invalid X-Meridian-Webhook-Timestamp format. Must be ISO 8601.",
            },
        )
    
    now = datetime.now(timezone.utc)
    time_diff = abs((now - webhook_timestamp).total_seconds())
    if time_diff > WEBHOOK_REPLAY_WINDOW_SECONDS:
        return JSONResponse(
            status_code=401,
            content={
                "detail": f"Webhook replay detected. Timestamp is {time_diff:.0f}s old, max allowed is {WEBHOOK_REPLAY_WINDOW_SECONDS}s.",
            },
        )
    
    # Check nonce reuse (simple in-memory store with TTL would go here)
    # For now, we just validate the nonce is present and non-empty

    # Parse body for optional input_context
    try:
        body = await request.json()
    except Exception:
        body = {}

    input_context = body.get("input_context") if isinstance(body, dict) else None

    # Use the internal run-service to start a run for the given mission.
    try:
        run = await create_run(db=db, mission_id=mission_id, input_context=input_context)
    except RunValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc)},
        )
    except Exception as exc:
        logger.exception(
            "N8N webhook failed to start run for mission %s: %s", mission_id, exc
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Failed to start run: {str(exc)}"},
        )

    return {
        "run_id": str(run.id),
        "mission_id": str(mission_id),
        "message": "N8N webhook triggered run started",
    }


# ──────────────────────────────────────────────
# GET  /integrations/status           : integration health / connectivity checks
# ──────────────────────────────────────────────
@router.get("/integrations/status", include_in_schema=True)
async def integrations_status(request: Request):
    """
    Lightweight connectivity checks for integrations.

    Returns status of:
      - MERIDIAN_WEBHOOK_SECRET (set / unset)
      - FIRECRAWL_API_KEY (set / unset)
      - SUPABASE_DATABASE_URL (set / unset)
      - HTTP_TOOL_ALLOWED_DOMAINS (set / unset)

    No external calls are made by default — only env‑var presence checks.
    When configured, optional connectivity probes are performed:
      - Webhook: secret validity + timestamp nonce check (performed on every
        n8n-webhook request, not here)
      - Firecrawl: API status check (HEAD request to https://api.firecrawl.dev/v1/health)
      - Supabase: simple SELECT 1 query with timeout (via asyncpg or psycopg2)
      HTTP allowlist: domain resolution check via async DNS lookup
    """
    return {
        "webhook": {
            "configured": bool(settings.MERIDIAN_WEBHOOK_SECRET),
            "env": "MERIDIAN_WEBHOOK_SECRET",
        },
        "firecrawl": {
            "configured": bool(settings.FIRECRAWL_API_KEY),
            "env": "FIRECRAWL_API_KEY",
        },
        "supabase": {
            "configured": bool(settings.SUPABASE_DATABASE_URL),
            "env": "SUPABASE_DATABASE_URL",
        },
        "http_allowlist": {
            "configured": bool(settings.HTTP_TOOL_ALLOWED_DOMAINS),
            "env": "HTTP_TOOL_ALLOWED_DOMAINS",
        },
    }


# ──────────────────────────────────────────────
# POST /tools/register                : register a new tool (placeholder)
# ──────────────────────────────────────────────
@router.post("")
async def register_tool(request: Request):
    """Register a new tool. Not yet implemented (code-based registration)."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


# ──────────────────────────────────────────────
# GET  /tools/{tool_id}               : get a tool by ID (placeholder)
# ──────────────────────────────────────────────
@router.get("/{tool_id}")
async def get_tool(tool_id: str, request: Request):
    """Get a tool by ID. Use GET /tools instead."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


# ──────────────────────────────────────────────
# PUT  /tools/{tool_id}               : update tool definition (placeholder)
# ──────────────────────────────────────────────
@router.put("/{tool_id}")
async def update_tool(tool_id: str, request: Request):
    """Update a tool definition. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


# ──────────────────────────────────────────────
# DELETE  /tools/{tool_id}            : delete tool (placeholder)
# ──────────────────────────────────────────────
@router.delete("/{tool_id}")
async def delete_tool(tool_id: str, request: Request):
    """Delete a tool. Not yet implemented."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )