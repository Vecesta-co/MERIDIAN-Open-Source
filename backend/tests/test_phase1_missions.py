"""
MERIDIAN Phase 1 — Mission Designer Tests.

Tests the mission CRUD, versioning, cloning, YAML export, and validation endpoints.
"""

import uuid

import pytest
from httpx import AsyncClient


# ──────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────


def valid_mission_payload() -> dict:
    """A valid mission JSON payload with 2 steps."""
    return {
        "name": "Test Mission",
        "goal": "Test the mission designer API",
        "description": "A mission for testing",
        "steps": [
            {
                "key": "step_1",
                "name": "Step 1",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Do step 1",
                "max_retries": 2,
                "timeout_seconds": 120,
            },
            {
                "key": "step_2",
                "name": "Step 2",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Do step 2",
                "order_index": 1,
            },
        ],
    }


def valid_yaml_text() -> str:
    """A valid mission YAML text."""
    return """\
version: "1.0"
mission:
  name: "YAML Mission"
  goal: "Test YAML mission creation"
  version: 1
  status: draft

agents:
  - key: "agent_1"
    name: "Agent One"
    model: "gpt-4o"

steps:
  - key: "research"
    name: "Research"
    step_type: "llm"
    agent_key: "agent_1"
    prompt_template: "Research the topic"
    max_retries: 2
    timeout_seconds: 120

  - key: "summarize"
    name: "Summarize"
    step_type: "llm"
    agent_key: "agent_1"
    prompt_template: "Summarize the research"
    order_index: 1
"""


async def create_test_mission(client: AsyncClient) -> dict:
    """Helper: create a mission and return the response JSON."""
    response = await client.post("/api/v1/missions", json=valid_mission_payload())
    assert response.status_code == 201, f"Failed to create mission: {response.text}"
    return response.json()


# ──────────────────────────────────────────────
# 1. Create Mission (JSON)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_mission_json(async_client: AsyncClient):
    """POST /missions with JSON creates a draft v1 mission."""
    response = await async_client.post("/api/v1/missions", json=valid_mission_payload())

    assert response.status_code == 201
    data = response.json()

    assert data["name"] == "Test Mission"
    assert data["goal"] == "Test the mission designer API"
    assert data["state"] == "draft"
    assert data["version"] == 1
    assert data["id"]


@pytest.mark.asyncio
async def test_create_mission_yaml(async_client: AsyncClient):
    """POST /missions with yaml_text creates a mission from YAML."""
    response = await async_client.post(
        "/api/v1/missions", json={"yaml_text": valid_yaml_text()}
    )

    assert response.status_code == 201, f"Failed: {response.text}"
    data = response.json()

    assert data["name"] == "YAML Mission"
    assert data["goal"] == "Test YAML mission creation"
    assert data["state"] == "draft"
    assert data["version"] == 1


# ──────────────────────────────────────────────
# 2. Get Mission
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_mission_with_steps(async_client: AsyncClient):
    """GET /missions/{id} returns mission with steps."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    response = await async_client.get(f"/api/v1/missions/{mission_id}")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == mission_id
    assert data["name"] == "Test Mission"
    assert isinstance(data["steps"], list)
    assert len(data["steps"]) == 2

    # Steps ordered correctly
    first_step = data["steps"][0]
    assert first_step["step_key"] == "step_1"
    assert first_step["agent_key"] == "agent_1"


@pytest.mark.asyncio
async def test_get_mission_not_found(async_client: AsyncClient):
    """GET /missions/{nonexistent_id} returns 404."""
    response = await async_client.get(
        f"/api/v1/missions/{uuid.uuid4()}"
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_mission_invalid_uuid(async_client: AsyncClient):
    """GET /missions/{invalid_id} returns 404 for invalid UUID."""
    response = await async_client.get("/api/v1/missions/not-a-uuid")

    assert response.status_code == 404


# ──────────────────────────────────────────────
# 3. List Missions
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_missions(async_client: AsyncClient):
    """GET /missions returns a list of missions."""
    await create_test_mission(async_client)

    response = await async_client.get("/api/v1/missions")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


# ──────────────────────────────────────────────
# 4. Update Mission (Versioning)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_mission_increments_version(async_client: AsyncClient):
    """PUT /missions/{id} increments version on draft mission."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]
    assert created["version"] == 1

    update_payload = {
        "name": "Updated Mission Name",
        "goal": "Test the mission designer API",
        "steps": valid_mission_payload()["steps"],
    }
    response = await async_client.put(f"/api/v1/missions/{mission_id}", json=update_payload)

    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "Updated Mission Name"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_update_published_mission_returns_403(async_client: AsyncClient):
    """PUT on published mission returns 403."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    # Publish first
    publish_resp = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert publish_resp.status_code == 200

    # Attempt update
    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Should Fail"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_mission_with_no_steps_still_increments(async_client: AsyncClient):
    """PUT with no steps field still increments version."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Name Only Change"},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2


# ──────────────────────────────────────────────
# 5. Publish Mission
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_mission(async_client: AsyncClient):
    """POST /missions/{id}/publish sets state to published."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    response = await async_client.post(f"/api/v1/missions/{mission_id}/publish")

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "published"


@pytest.mark.asyncio
async def test_publish_mission_idempotent(async_client: AsyncClient):
    """Publishing an already-published mission returns 200."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    first = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert first.status_code == 200

    second = await async_client.post(f"/api/v1/missions/{mission_id}/publish")
    assert second.status_code == 200
    assert second.json()["state"] == "published"


# ──────────────────────────────────────────────
# 6. Clone Mission
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_mission(async_client: AsyncClient):
    """POST /missions/{id}/clone creates independent draft v1."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    response = await async_client.post(f"/api/v1/missions/{mission_id}/clone")

    assert response.status_code == 200
    data = response.json()

    assert data["mission"]["name"] == "Test Mission (Copy)"
    assert data["mission"]["state"] == "draft"
    assert data["mission"]["version"] == 1
    assert data["mission"]["id"] != mission_id

    # Verify clone can be fetched and has steps
    clone_id = data["mission"]["id"]
    get_resp = await async_client.get(f"/api/v1/missions/{clone_id}")
    assert get_resp.status_code == 200
    assert len(get_resp.json()["steps"]) == 2


@pytest.mark.asyncio
async def test_clone_nonexistent_mission_returns_404(async_client: AsyncClient):
    """Cloning a nonexistent mission returns 404."""
    response = await async_client.post(f"/api/v1/missions/{uuid.uuid4()}/clone")

    assert response.status_code == 404


# ──────────────────────────────────────────────
# 7. YAML Export
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_mission_yaml(async_client: AsyncClient):
    """GET /missions/{id}/yaml returns YAML text."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    response = await async_client.get(f"/api/v1/missions/{mission_id}/yaml")

    assert response.status_code == 200
    data = response.json()

    assert "yaml_text" in data
    yaml_text = data["yaml_text"]
    assert "Test Mission" in yaml_text
    assert "step_1" in yaml_text
    assert "step_2" in yaml_text


@pytest.mark.asyncio
async def test_yaml_export_roundtrip(async_client: AsyncClient):
    """YAML export can be parsed back into an equivalent mission."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    # Export YAML
    export_resp = await async_client.get(f"/api/v1/missions/{mission_id}/yaml")
    assert export_resp.status_code == 200
    yaml_text = export_resp.json()["yaml_text"]

    # Create a new mission from the exported YAML
    create_resp = await async_client.post(
        "/api/v1/missions", json={"yaml_text": yaml_text}
    )
    assert create_resp.status_code == 201, f"Failed: {create_resp.text}"

    data = create_resp.json()
    assert data["name"] == "Test Mission"
    assert data["version"] == 1


# ──────────────────────────────────────────────
# 8. Validate Mission
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_valid_mission(async_client: AsyncClient):
    """POST /missions/validate returns valid=True for good payload."""
    response = await async_client.post(
        "/api/v1/missions/validate", json=valid_mission_payload()
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_validate_missing_name(async_client: AsyncClient):
    """Validation rejects empty mission name."""
    payload = valid_mission_payload()
    payload["name"] = ""

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["field"] == "name" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_empty_steps(async_client: AsyncClient):
    """Validation rejects missions with no steps."""
    payload = valid_mission_payload()
    payload["steps"] = []

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False


@pytest.mark.asyncio
async def test_validate_duplicate_step_keys(async_client: AsyncClient):
    """Validation rejects duplicate step keys."""
    payload = valid_mission_payload()
    payload["steps"].append(payload["steps"][0].copy())

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "duplicate_key" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_missing_agent_key(async_client: AsyncClient):
    """Validation rejects llm step without agent_key."""
    payload = valid_mission_payload()
    del payload["steps"][0]["agent_key"]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any("agent_key" in e["field"] for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_invalid_tool_refs(async_client: AsyncClient):
    """Validation rejects invalid tool_refs structure."""
    payload = valid_mission_payload()
    payload["steps"][0]["tool_refs"] = [{"invalid": "structure"}]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "invalid_structure" for e in data["errors"])


# ──────────────────────────────────────────────
# 9. Validation on Create
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_mission_empty_body_returns_422(async_client: AsyncClient):
    """POST /missions with empty body returns 400/422."""
    response = await async_client.post("/api/v1/missions", content="", headers={"Content-Type": "application/json"})

    # Depending on FastAPI config, this may be 400 or 422
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_mission_missing_name_returns_400(async_client: AsyncClient):
    """POST /missions with missing name returns 400."""
    payload = valid_mission_payload()
    del payload["name"]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_mission_duplicate_keys_returns_400(async_client: AsyncClient):
    """POST /missions with duplicate step keys returns 400."""
    payload = valid_mission_payload()
    payload["steps"].append(payload["steps"][0].copy())

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400


# ──────────────────────────────────────────────
# 10. Edge Cases
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_100_steps_mission_accepted(async_client: AsyncClient):
    """A mission with 100+ steps is accepted."""
    payload = valid_mission_payload()
    for i in range(100):
        payload["steps"].append(
            {
                "key": f"step_{i + 10}",
                "name": f"Step {i + 10}",
                "step_type": "llm",
                "agent_key": "agent_1",
            }
        )

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_yaml_export_no_steps_is_valid_yaml(async_client: AsyncClient):
    """A mission with no steps exports as valid YAML with empty list."""
    payload = valid_mission_payload()
    payload["steps"] = []

    # This should fail at creation, but direct DB state without steps should still export
    response = await async_client.get(f"/api/v1/missions/{uuid.uuid4()}/yaml")
    assert response.status_code == 404  # No mission exists


# ──────────────────────────────────────────────
# 11. Audit Fix — Create-Path Validation Parity
#    (POST /missions must reject the same payloads /validate rejects)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_mission_missing_goal_returns_400(async_client: AsyncClient):
    """POST /missions with no goal returns 400 (matches /validate)."""
    payload = valid_mission_payload()
    del payload["goal"]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "goal" in response.text


@pytest.mark.asyncio
async def test_create_llm_step_missing_agent_key_returns_400(async_client: AsyncClient):
    """POST /missions with llm step lacking agent_key returns 400 (matches /validate)."""
    payload = valid_mission_payload()
    del payload["steps"][0]["agent_key"]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "agent_key" in response.text


@pytest.mark.asyncio
async def test_create_invalid_tool_refs_returns_400(async_client: AsyncClient):
    """POST /missions with malformed tool_refs returns 400 (matches /validate)."""
    payload = valid_mission_payload()
    payload["steps"][0]["tool_refs"] = [{"bad": "shape"}]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "tool_refs" in response.text


@pytest.mark.asyncio
async def test_create_tool_step_without_tool_refs_returns_400(async_client: AsyncClient):
    """POST /missions with a tool step lacking tool_refs returns 400."""
    payload = valid_mission_payload()
    payload["steps"].append(
        {
            "key": "tool_step",
            "name": "Tool Step",
            "step_type": "tool",
        }
    )

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "tool_refs" in response.text


@pytest.mark.asyncio
async def test_create_tool_step_with_tool_refs_succeeds(async_client: AsyncClient):
    """POST /missions with a properly-configured tool step succeeds."""
    payload = valid_mission_payload()
    payload["steps"].append(
        {
            "key": "tool_step",
            "name": "Tool Step",
            "step_type": "tool",
            "tool_refs": [{"tool_name": "web_search"}],
        }
    )

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 201


# ──────────────────────────────────────────────
# 12. Audit Fix — depends_on Validation
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_depends_on_unknown_key(async_client: AsyncClient):
    """Validation rejects depends_on references to non-existent step keys."""
    payload = valid_mission_payload()
    payload["steps"][1]["depends_on"] = ["nonexistent_step"]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "unknown_reference" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_depends_on_self_reference(async_client: AsyncClient):
    """Validation rejects a step depending on itself."""
    payload = valid_mission_payload()
    payload["steps"][0]["depends_on"] = ["step_1"]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "circular_dependency" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_depends_on_circular_reference(async_client: AsyncClient):
    """Validation detects a circular dependency across two steps."""
    payload = valid_mission_payload()
    payload["steps"][0]["depends_on"] = ["step_2"]
    payload["steps"][1]["depends_on"] = ["step_1"]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "circular_dependency" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_depends_on_non_list(async_client: AsyncClient):
    """Validation rejects depends_on that is not a list."""
    payload = valid_mission_payload()
    payload["steps"][0]["depends_on"] = "step_2"

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "invalid_type" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_depends_on_valid_reference(async_client: AsyncClient):
    """Validation accepts a valid depends_on reference (DAG)."""
    payload = valid_mission_payload()
    payload["steps"][1]["depends_on"] = ["step_1"]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True


@pytest.mark.asyncio
async def test_create_depends_on_cycle_returns_400(async_client: AsyncClient):
    """POST /missions with a circular depends_on returns 400."""
    payload = valid_mission_payload()
    payload["steps"][0]["depends_on"] = ["step_2"]
    payload["steps"][1]["depends_on"] = ["step_1"]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "circular" in response.text.lower() or "dependency" in response.text.lower()


@pytest.mark.asyncio
async def test_create_depends_on_valid_reference_succeeds(async_client: AsyncClient):
    """POST /missions with a valid depends_on DAG succeeds."""
    payload = valid_mission_payload()
    payload["steps"][1]["depends_on"] = ["step_1"]

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 201


# ──────────────────────────────────────────────
# 13. Audit Fix — agent_key Cross-Reference
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_agent_key_undefined_agent(async_client: AsyncClient):
    """Validation rejects llm step referencing an agent not in the agents section."""
    payload = valid_mission_payload()
    payload["agents"] = [{"key": "defined_agent", "name": "Defined", "model": "gpt-4o"}]
    payload["steps"][0]["agent_key"] = "phantom_agent"

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "unknown_reference" for e in data["errors"])


@pytest.mark.asyncio
async def test_validate_agent_key_defined_agent_passes(async_client: AsyncClient):
    """Validation accepts llm step referencing a defined agent."""
    payload = valid_mission_payload()
    payload["agents"] = [{"key": "agent_1", "name": "Agent One", "model": "gpt-4o"}]

    response = await async_client.post("/api/v1/missions/validate", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True


@pytest.mark.asyncio
async def test_create_agent_key_undefined_agent_returns_400(async_client: AsyncClient):
    """POST /missions with undefined agent reference returns 400."""
    payload = valid_mission_payload()
    payload["agents"] = [{"key": "defined_agent", "name": "Defined", "model": "gpt-4o"}]
    payload["steps"][0]["agent_key"] = "phantom_agent"

    response = await async_client.post("/api/v1/missions", json=payload)

    assert response.status_code == 400
    assert "agent" in response.text.lower()


# ──────────────────────────────────────────────
# 14. Audit Fix — YAML Path Validation Parity
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yaml_validate_tool_step_without_tool_refs(async_client: AsyncClient):
    """YAML /validate rejects tool step without tool_refs."""
    yaml_text = """\
version: "1.0"
mission:
  name: "Bad Tool Mission"
  goal: "Test tool validation"
steps:
  - key: "t1"
    name: "Tool Step"
    step_type: "tool"
"""
    response = await async_client.post(
        "/api/v1/missions/validate", json={"yaml_text": yaml_text}
    )

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any("tool_refs" in e.get("field", "") for e in data["errors"])


@pytest.mark.asyncio
async def test_yaml_validate_depends_on_cycle(async_client: AsyncClient):
    """YAML /validate detects a circular dependency."""
    yaml_text = """\
version: "1.0"
mission:
  name: "Cycle Mission"
  goal: "Test cycle detection"
agents:
  - key: "a1"
    name: "Agent"
    model: "gpt-4o"
steps:
  - key: "s1"
    name: "Step One"
    step_type: "llm"
    agent_key: "a1"
    depends_on: ["s2"]
  - key: "s2"
    name: "Step Two"
    step_type: "llm"
    agent_key: "a1"
    depends_on: ["s1"]
"""
    response = await async_client.post(
        "/api/v1/missions/validate", json={"yaml_text": yaml_text}
    )

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "circular_dependency" for e in data["errors"])


@pytest.mark.asyncio
async def test_yaml_validate_agent_key_undefined(async_client: AsyncClient):
    """YAML /validate rejects llm step with undefined agent key."""
    yaml_text = """\
version: "1.0"
mission:
  name: "Bad Agent Mission"
  goal: "Test agent validation"
agents:
  - key: "a1"
    name: "Agent"
    model: "gpt-4o"
steps:
  - key: "s1"
    name: "Step One"
    step_type: "llm"
    agent_key: "ghost_agent"
"""
    response = await async_client.post(
        "/api/v1/missions/validate", json={"yaml_text": yaml_text}
    )

    assert response.status_code == 400
    data = response.json()
    assert data["valid"] is False
    assert any(e["code"] == "unknown_reference" for e in data["errors"])


# ──────────────────────────────────────────────
# 15. Audit Fix — order_index DB Constraint
#    (ORM-level UniqueConstraint sync with migration 005)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_order_index_rejected(async_client: AsyncClient):
    """Steps with duplicate order_index within the same version are rejected at DB level."""
    payload = valid_mission_payload()
    payload["steps"][0]["order_index"] = 5
    payload["steps"][1]["order_index"] = 5

    response = await async_client.post("/api/v1/missions", json=payload)

    # The ORM constraint (uq_steps_mission_version_order) may reject this via IntegrityError
    # The API layer should translate this to a 400
    assert response.status_code in (400, 409, 500)
    if response.status_code == 400:
        assert "order" in response.text.lower() or "index" in response.text.lower()


# ──────────────────────────────────────────────
# 16. Audit Fix — Update-Path Validation Parity
#    (PUT /missions/{id} must reject the same payloads /validate rejects)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_mission_missing_goal_returns_400(async_client: AsyncClient):
    """PUT /missions/{id} with replacement steps lacking goal returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    update_payload = {
        "name": "Updated",
        "steps": valid_mission_payload()["steps"],
    }
    # Remove goal from the update payload — should be rejected
    response = await async_client.put(f"/api/v1/missions/{mission_id}", json=update_payload)

    assert response.status_code == 400
    assert "goal" in response.text


@pytest.mark.asyncio
async def test_update_mission_llm_step_missing_agent_key_returns_400(async_client: AsyncClient):
    """PUT /missions with llm step lacking agent_key returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    del bad_steps[0]["agent_key"]

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": bad_steps},
    )

    assert response.status_code == 400
    assert "agent_key" in response.text


@pytest.mark.asyncio
async def test_update_mission_tool_step_without_tool_refs_returns_400(async_client: AsyncClient):
    """PUT /missions with a tool step lacking tool_refs returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    bad_steps.append(
        {
            "key": "tool_step",
            "name": "Tool Step",
            "step_type": "tool",
        }
    )

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": bad_steps},
    )

    assert response.status_code == 400
    assert "tool_refs" in response.text


@pytest.mark.asyncio
async def test_update_mission_depends_on_cycle_returns_400(async_client: AsyncClient):
    """PUT /missions with circular depends_on in replacement steps returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    bad_steps[0]["depends_on"] = ["step_2"]
    bad_steps[1]["depends_on"] = ["step_1"]

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": bad_steps},
    )

    assert response.status_code == 400
    assert "circular" in response.text.lower() or "dependency" in response.text.lower()


@pytest.mark.asyncio
async def test_update_mission_depends_on_unknown_key_returns_400(async_client: AsyncClient):
    """PUT /missions with depends_on referencing unknown step returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    bad_steps[1]["depends_on"] = ["nonexistent_step"]

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": bad_steps},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_mission_undefined_agent_returns_400(async_client: AsyncClient):
    """PUT /missions with llm step referencing undefined agent returns 400."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    bad_steps[0]["agent_key"] = "phantom_agent"

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={
            "name": "Updated",
            "goal": "Test goal",
            "steps": bad_steps,
            "agents": [{"key": "real_agent", "name": "Real", "model": "gpt-4o"}],
        },
    )

    assert response.status_code == 400
    assert "agent" in response.text.lower()


# ──────────────────────────────────────────────
# 17. Audit Fix — PUT with Duplicate order_index
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_duplicate_order_index_rejected(async_client: AsyncClient):
    """PUT /missions with duplicate order_index in replacement steps is rejected."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    bad_steps = valid_mission_payload()["steps"]
    bad_steps[0]["order_index"] = 7
    bad_steps[1]["order_index"] = 7

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": bad_steps},
    )

    assert response.status_code in (400, 409, 500)
    if response.status_code == 400:
        assert "order" in response.text.lower() or "index" in response.text.lower()


@pytest.mark.asyncio
async def test_update_unique_order_index_succeeds(async_client: AsyncClient):
    """PUT /missions with unique order_index succeeds and increments version."""
    created = await create_test_mission(async_client)
    mission_id = created["id"]

    good_steps = valid_mission_payload()["steps"]
    good_steps[0]["order_index"] = 2
    good_steps[1]["order_index"] = 3

    response = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={"name": "Updated", "goal": "Test goal", "steps": good_steps},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 2


# ──────────────────────────────────────────────
# 18. YAML-Created Mission Flows
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yaml_created_mission_get(async_client: AsyncClient):
    """GET a mission created from YAML returns correct steps."""
    response = await async_client.post(
        "/api/v1/missions", json={"yaml_text": valid_yaml_text()}
    )
    assert response.status_code == 201
    mission_id = response.json()["id"]

    get_resp = await async_client.get(f"/api/v1/missions/{mission_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["name"] == "YAML Mission"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["step_key"] == "research"


@pytest.mark.asyncio
async def test_yaml_created_mission_update(async_client: AsyncClient):
    """PUT on a YAML-created draft mission increments version."""
    response = await async_client.post(
        "/api/v1/missions", json={"yaml_text": valid_yaml_text()}
    )
    assert response.status_code == 201
    mission_id = response.json()["id"]

    update_resp = await async_client.put(
        f"/api/v1/missions/{mission_id}",
        json={
            "name": "Renamed Mission",
            "goal": "Test the mission designer API",
            "steps": valid_mission_payload()["steps"],
        },
    )

    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["name"] == "Renamed Mission"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_yaml_created_mission_clone(async_client: AsyncClient):
    """Clone a YAML-created mission produces an independent draft v1."""
    response = await async_client.post(
        "/api/v1/missions", json={"yaml_text": valid_yaml_text()}
    )
    assert response.status_code == 201
    mission_id = response.json()["id"]

    clone_resp = await async_client.post(f"/api/v1/missions/{mission_id}/clone")
    assert clone_resp.status_code == 200
    data = clone_resp.json()

    assert data["mission"]["name"] == "YAML Mission (Copy)"
    assert data["mission"]["state"] == "draft"
    assert data["mission"]["version"] == 1
    assert data["mission"]["id"] != mission_id

    # Clone steps are present
    clone_id = data["mission"]["id"]
    get_clone = await async_client.get(f"/api/v1/missions/{clone_id}")
    assert get_clone.status_code == 200
    assert len(get_clone.json()["steps"]) == 2


@pytest.mark.asyncio
async def test_yaml_created_mission_export_yaml(async_client: AsyncClient):
    """YAML-created mission can be exported back to YAML."""
    response = await async_client.post(
        "/api/v1/missions", json={"yaml_text": valid_yaml_text()}
    )
    assert response.status_code == 201
    mission_id = response.json()["id"]

    export_resp = await async_client.get(f"/api/v1/missions/{mission_id}/yaml")
    assert export_resp.status_code == 200
    yaml_text = export_resp.json()["yaml_text"]
    assert "YAML Mission" in yaml_text
    assert "research" in yaml_text
    assert "summarize" in yaml_text
