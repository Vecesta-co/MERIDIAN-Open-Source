"""
MERIDIAN Phase 1 — Live Server Verification Script.

Verifies all 8 mission endpoints against a running uvicorn server
via HTTP (equivalent to curl), demonstrating real DB persistence.
"""

import uuid

import httpx

BASE_URL = "http://127.0.0.1:8000"
OK = "\033[92m"
FAIL = "\033[91m"
END = "\033[0m"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"{OK}  PASS{END}  {name}")
    else:
        failed += 1
        print(f"{FAIL}  FAIL{END}  {name}  {detail}")


def main():
    client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    # 1. Health
    print("\n[1] Health endpoint")
    r = client.get("/health")
    body = r.json()
    check("GET /health -> 200", r.status_code == 200)
    check("response has status=healthy", body.get("status") == "healthy", str(body))
    check("database_connected=true", body.get("database_connected") is True, str(body))
    check("version present", "version" in body)

    # 2. Create mission via JSON
    print("\n[2] Create mission (JSON)")
    payload = {
        "name": "Live Curl Test Mission",
        "goal": "Verify all Phase 1 endpoints live",
        "steps": [
            {
                "key": "research",
                "name": "Research",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Research the topic: {{topic}}",
                "max_retries": 2,
                "timeout_seconds": 120,
            },
            {
                "key": "summarize",
                "name": "Summarize",
                "step_type": "llm",
                "agent_key": "agent_1",
                "prompt_template": "Summarize: {{research.output}}",
                "order_index": 1,
            },
        ],
    }
    r = client.post("/api/v1/missions", json=payload)
    body = r.json()
    check("POST /api/v1/missions (JSON) -> 201", r.status_code == 201, str(r.status_code))
    mission_id = body.get("id")
    check("returns mission id", mission_id is not None, str(body))
    check("initial version = 1", body.get("version") == 1, str(body.get("version")))
    check("state = draft", body.get("state") == "draft", str(body.get("state")))
    steps = body.get("steps", [])
    check("2 steps returned", len(steps) == 2, str(len(steps)))
    check("step has agent_key", steps[0].get("agent_key") == "agent_1", str(steps[0]))

    # 3. Create mission via YAML
    print("\n[3] Create mission (YAML)")
    from pathlib import Path

    yaml_path = Path(__file__).parent / "curl_test_mission.yaml"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    r = client.post("/api/v1/missions", json={"yaml_text": yaml_text})
    body = r.json()
    check("POST /api/v1/missions (YAML) -> 201", r.status_code == 201, str(r.status_code))
    yaml_mission_id = body.get("id")
    check("YAML mission created", yaml_mission_id is not None, str(body))
    check("YAML mission has 1 step", len(body.get("steps", [])) == 1, str(body.get("steps")))

    # 4. GET mission by id (JSON-created)
    print("\n[4] Get mission detail")
    r = client.get(f"/api/v1/missions/{mission_id}")
    body = r.json()
    check("GET /api/v1/missions/{id} -> 200", r.status_code == 200, str(r.status_code))
    check("name matches", body.get("name") == "Live Curl Test Mission", str(body.get("name")))
    check("version = 1", body.get("version") == 1)
    check("steps ordered", body.get("steps", [{}])[0].get("step_key") == "research", str(body.get("steps")))
    check("step has agent_key", body.get("steps", [{}])[0].get("agent_key") == "agent_1", str(body.get("steps", [{}])[0]))
    check("step has prompt_template", "prompt_template" in body.get("steps", [{}])[0], str(body.get("steps", [{}])[0]))

    # 5. List missions
    print("\n[5] List missions")
    r = client.get("/api/v1/missions")
    body = r.json()
    check("GET /api/v1/missions -> 200", r.status_code == 200, str(r.status_code))
    items = body if isinstance(body, list) else body.get("items", [])
    check("at least 2 missions in list", len(items) >= 2, f"count={len(items)}")
    check("list contains created mission", any(m.get("id") == mission_id for m in items), str(items))

    # 6. Update mission (draft) -> version increments to 2
    print("\n[6] Update (draft) -> version increment")
    update_payload = {
        "name": "Live Curl Test Mission Updated",
        "steps": [
            {"key": "only_step", "name": "Only Step", "step_type": "llm", "agent_key": "agent_1", "prompt_template": "Do work"},
        ],
    }
    r = client.put(f"/api/v1/missions/{mission_id}", json=update_payload)
    body = r.json()
    check("PUT /api/v1/missions/{id} -> 200", r.status_code == 200, str(r.status_code))
    check("version incremented to 2", body.get("version") == 2, str(body.get("version")))
    check("name updated", body.get("name") == "Live Curl Test Mission Updated", str(body.get("name")))
    check("steps replaced (1)", len(body.get("steps", [])) == 1)

    # 7. Publish mission
    print("\n[7] Publish")
    r = client.post(f"/api/v1/missions/{mission_id}/publish")
    body = r.json()
    check("POST /publish -> 200", r.status_code == 200, str(r.status_code))
    check("state = published", body.get("state") == "published", str(body.get("state")))

    # 8. Attempt update on published -> 403
    print("\n[8] Update published -> 403")
    r = client.put(f"/api/v1/missions/{mission_id}", json={"name": "Should Fail", "steps": []})
    check("PUT published -> 403", r.status_code == 403, str(r.status_code))

    # 9. Clone mission
    print("\n[9] Clone")
    r = client.post(f"/api/v1/missions/{mission_id}/clone")
    body = r.json()
    cloned = body.get("mission", body)
    check("POST /clone -> 200", r.status_code == 200, str(r.status_code))
    check("clone state = draft", cloned.get("state") == "draft", str(cloned.get("state")))
    check("clone version = 1", cloned.get("version") == 1, str(cloned.get("version")))
    check("clone name has Copy", "Copy" in (cloned.get("name") or ""), str(cloned.get("name")))
    check("clone has independent id", cloned.get("id") != mission_id, str(cloned.get("id")))

    # 10. Export YAML
    print("\n[10] Export YAML")
    r = client.get(f"/api/v1/missions/{mission_id}/yaml")
    body = r.json()
    check("GET /{id}/yaml -> 200", r.status_code == 200, str(r.status_code))
    yaml_text = body.get("yaml_text", "")
    check("yaml contains mission name", "Live Curl Test Mission Updated" in yaml_text, yaml_text[:200])
    check("yaml contains goal", "Verify all Phase 1 endpoints live" in yaml_text, yaml_text[:200])
    check("yaml contains step key", "only_step" in yaml_text, yaml_text[:300])
    check("yaml contains agent_key", "agent_1" in yaml_text, yaml_text[:300])
    check("yaml contains step_type", "llm" in yaml_text, yaml_text[:300])

    # 11. Validate valid
    print("\n[11] Validate")
    r = client.post(
        "/api/v1/missions/validate",
        json={
            "name": "Valid Mission",
            "goal": "Goal",
            "steps": [{"key": "s1", "step_type": "llm", "agent_key": "a"}],
        },
    )
    body = r.json()
    check("POST /validate (valid) -> 200", r.status_code == 200, str(r.status_code))
    check("valid=true", body.get("valid") is True, str(body))

    # 12. Validate invalid (duplicate keys)
    r = client.post(
        "/api/v1/missions/validate",
        json={
            "name": "Bad",
            "goal": "G",
            "steps": [
                {"key": "dup", "step_type": "llm", "agent_key": "a"},
                {"key": "dup", "step_type": "tool"},
            ],
        },
    )
    body = r.json()
    check("POST /validate (duplicate keys) -> 400", r.status_code == 400, str(r.status_code))
    check("valid=false", body.get("valid") is False, str(body))
    check("error has duplicate_key", any(e.get("code") == "duplicate_key" for e in body.get("errors", [])), str(body))

    # 13. Validate YAML
    r = client.post("/api/v1/missions/validate", json={"yaml_text": yaml_text})
    body = r.json()
    check("POST /validate (valid YAML) -> 200", r.status_code == 200, str(r.status_code))
    check("valid YAML accepted", body.get("valid") is True, str(body))

    # 14. GET nonexistent mission -> 404
    print("\n[14] Edge cases")
    r = client.get(f"/api/v1/missions/{uuid.uuid4()}")
    check("GET nonexistent -> 404", r.status_code == 404, str(r.status_code))

    # 15. Clone nonexistent -> 404
    r = client.post(f"/api/v1/missions/{uuid.uuid4()}/clone")
    check("Clone nonexistent -> 404", r.status_code == 404, str(r.status_code))

    # 16. Export YAML nonexistent -> 404
    r = client.get(f"/api/v1/missions/{uuid.uuid4()}/yaml")
    check("YAML export nonexistent -> 404", r.status_code == 404, str(r.status_code))

    client.close()

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    if failed == 0:
        print(f"{OK}ALL LIVE ENDPOINT CHECKS PASSED{END}")
    else:
        print(f"{FAIL}SOME CHECKS FAILED — INVESTIGATE{END}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
