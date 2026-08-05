"""
MERIDIAN Missions API — v1.

Phase 1: Mission Designer.

Endpoints:
- POST   /missions              Create mission (JSON or YAML)
- GET    /missions              List missions (paginated, optional state filter)
- GET    /missions/{id}         Get mission with steps
- PUT    /missions/{id}         Update draft mission (increments version)
- POST   /missions/{id}/publish Publish mission (locks edits)
- POST   /missions/{id}/clone   Clone mission (draft v1)
- GET    /missions/{id}/yaml    Export mission as YAML
- POST   /missions/validate     Validate payload without saving
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db_session
from app.models.schemas import (
    MissionCloneResponse,
    MissionResponse,
    MissionWithStepsResponse,
    NotImplementedResponse,
    ValidationResult,
    YamlExportResponse,
)
from app.services import mission_service
from app.services.yaml_service import (
    dict_to_yaml,
    mission_to_yaml_dict,
)

router = APIRouter(prefix="/missions", tags=["missions"])
logger = get_logger(__name__)


def _http_400(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _http_403(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def _http_404(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


@router.get("", response_model=List[MissionResponse])
async def list_missions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    state: Optional[str] = Query(None, description="Filter by mission state"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    List all missions with pagination.

    Supports optional state filter (draft, published, archived).
    """
    try:
        missions, total = await mission_service.list_missions(
            session=session,
            page=page,
            page_size=page_size,
            state=state,
        )
        return missions
    except Exception as exc:
        logger.error("Failed to list missions: %s", str(exc))
        raise _http_400("Failed to list missions")


@router.post("", response_model=MissionWithStepsResponse, status_code=201)
async def create_mission(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create a new mission.

    Supports both:
    - JSON:  {"name": "...", "goal": "...", "steps": [...], "agents": [...]}
    - YAML:  {"yaml_text": "mission: ... steps: ..."}
    """
    try:
        body = await request.json()
    except Exception:
        raise _http_400("Invalid JSON body")

    if not body or not isinstance(body, dict):
        raise _http_400("Request body must be a JSON object")

    try:
        # Determine format: YAML or JSON
        if "yaml_text" in body:
            yaml_text = body["yaml_text"]
            if not yaml_text or not yaml_text.strip():
                raise _http_400("yaml_text cannot be empty")

            mission, steps, _workflow = await mission_service.create_mission_from_yaml(
                session=session,
                yaml_text=yaml_text,
            )
        else:
            mission, steps = await mission_service.create_mission_from_json(
                session=session,
                mission_data=body,
            )

        return mission_service.build_mission_with_steps(mission, steps)
    except ValueError as exc:
        logger.warning("Mission creation validation error: %s", str(exc))
        raise _http_400(str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to create mission: %s", str(exc))
        raise _http_400("Failed to create mission: " + str(exc))


@router.post("/validate", response_model=ValidationResult)
async def validate_mission(request: Request):
    """
    Validate a mission payload without saving.

    Supports both JSON and YAML formats.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=ValidationResult(
                valid=False,
                errors=[
                    {
                        "field": "body",
                        "message": "Invalid JSON body",
                        "code": "parse_error",
                    }
                ],
            ).model_dump(),
        )

    if not body or not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content=ValidationResult(
                valid=False,
                errors=[
                    {
                        "field": "body",
                        "message": "Request body must be a JSON object",
                        "code": "invalid_type",
                    }
                ],
            ).model_dump(),
        )

    result = mission_service.validate_mission_payload(body)
    status_code = 200 if result.valid else 400
    return JSONResponse(status_code=status_code, content=result.model_dump())


@router.get("/{mission_id}", response_model=MissionWithStepsResponse)
async def get_mission(
    mission_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Get a mission by ID with its steps.

    Returns mission metadata plus the steps from the latest version.
    """
    try:
        mission_uuid = uuid.UUID(mission_id)
    except ValueError:
        raise _http_404("Invalid mission ID format")

    try:
        detail = await mission_service.get_mission_detail(session, mission_uuid)
        return detail
    except ValueError:
        raise _http_404(f"Mission {mission_id} not found")
    except Exception as exc:
        logger.error("Failed to get mission %s: %s", mission_id, str(exc))
        raise _http_400("Failed to get mission")


@router.put("/{mission_id}", response_model=MissionWithStepsResponse)
async def update_mission(
    mission_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Update a draft mission.

    - Increments version on every save
    - Returns 403 if mission is published
    - Replaces steps if provided
    """
    try:
        mission_uuid = uuid.UUID(mission_id)
    except ValueError:
        raise _http_404("Invalid mission ID format")

    try:
        body = await request.json()
    except Exception:
        raise _http_400("Invalid JSON body")

    if not body or not isinstance(body, dict):
        raise _http_400("Request body must be a JSON object")

    try:
        mission, _steps = await mission_service.update_mission(
            session=session,
            mission_id=mission_uuid,
            update_data=body,
        )
        detail = await mission_service.get_mission_detail(session, mission_uuid)
        return detail
    except ValueError as exc:
        if "not found" in str(exc):
            raise _http_404(str(exc))
        raise _http_400(str(exc))
    except PermissionError as exc:
        raise _http_403(str(exc))
    except Exception as exc:
        logger.error("Failed to update mission %s: %s", mission_id, str(exc))
        raise _http_400("Failed to update mission")


@router.post("/{mission_id}/publish", response_model=MissionResponse)
async def publish_mission(
    mission_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Publish a mission.

    - Sets state to 'published'
    - Idempotent: publishing an already-published mission returns 200
    - Published missions cannot be edited
    """
    try:
        mission_uuid = uuid.UUID(mission_id)
    except ValueError:
        raise _http_404("Invalid mission ID format")

    try:
        mission = await mission_service.publish_mission(session, mission_uuid)
        return mission_service.mission_to_response(mission)
    except ValueError:
        raise _http_404(f"Mission {mission_id} not found")
    except Exception as exc:
        logger.error("Failed to publish mission %s: %s", mission_id, str(exc))
        raise _http_400("Failed to publish mission")


@router.post("/{mission_id}/clone", response_model=MissionCloneResponse)
async def clone_mission(
    mission_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Clone a mission.

    - Creates an independent copy with state=draft, version=1
    - Name becomes "{original name} (Copy)"
    - Steps are copied as independent records
    """
    try:
        mission_uuid = uuid.UUID(mission_id)
    except ValueError:
        raise _http_404("Invalid mission ID format")

    try:
        cloned = await mission_service.clone_mission(session, mission_uuid)
        return MissionCloneResponse(
            mission=mission_service.mission_to_response(cloned),
            message="Mission cloned successfully",
        )
    except ValueError:
        raise _http_404(f"Mission {mission_id} not found")
    except Exception as exc:
        logger.error("Failed to clone mission %s: %s", mission_id, str(exc))
        raise _http_400("Failed to clone mission")


@router.delete("/{mission_id}")
async def delete_mission(mission_id: str, request: Request):
    """Delete a mission. Not yet implemented in Phase 1."""
    return JSONResponse(
        status_code=501,
        content=NotImplementedResponse(
            path=str(request.url),
            method=request.method,
        ).model_dump(),
    )


@router.get("/{mission_id}/yaml", response_model=YamlExportResponse)
async def export_mission_yaml(
    mission_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Export a mission as YAML.

    Returns the mission definition in YAML format, including all steps.
    """
    try:
        mission_uuid = uuid.UUID(mission_id)
    except ValueError:
        raise _http_404("Invalid mission ID format")

    try:
        detail = await mission_service.get_mission_detail(session, mission_uuid)
    except ValueError:
        raise _http_404(f"Mission {mission_id} not found")
    except Exception as exc:
        logger.error("Failed to get mission %s for export: %s", mission_id, str(exc))
        raise _http_400("Failed to get mission")

    try:
        # Convert steps to YAML step dicts
        steps_data = []
        for step in detail.get("steps", []):
            step_dict = {
                "key": step.get("step_key", ""),
                "name": step.get("name", ""),
                "step_type": step.get("step_type") or step.get("kind", "llm"),
            }
            if step.get("agent_key"):
                step_dict["agent_key"] = step["agent_key"]
            if step.get("prompt_template"):
                step_dict["prompt_template"] = step["prompt_template"]
            if step.get("tool_refs"):
                step_dict["tool_refs"] = step["tool_refs"]
            if step.get("approval_required"):
                step_dict["approval_required"] = True
            if step.get("max_retries") and step["max_retries"] != 3:
                step_dict["max_retries"] = step["max_retries"]
            if step.get("timeout_seconds") and step["timeout_seconds"] != 300:
                step_dict["timeout_seconds"] = step["timeout_seconds"]
            steps_data.append(step_dict)

        yaml_data = mission_to_yaml_dict(
            mission_name=detail.get("name", ""),
            mission_goal=detail.get("goal") or "",
            steps=steps_data,
            version=detail.get("version", 1),
            status=detail.get("state", "draft"),
        )
        yaml_text = dict_to_yaml(yaml_data)
        if yaml_text is None:
            raise _http_400("Failed to serialize mission to YAML")

        return YamlExportResponse(yaml_text=yaml_text)
    except Exception as exc:
        logger.error("Failed to export mission %s as YAML: %s", mission_id, str(exc))
        raise _http_400("Failed to export mission as YAML")
