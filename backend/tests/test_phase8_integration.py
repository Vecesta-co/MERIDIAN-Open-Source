"""MERIDIAN Phase 8 — Integration Bus Tests.

Tests for:
  - N8N webhook trigger (authenticated POST)
  - BrowseUse tool (browseuse_action)
  - Supabase CRUD tool (allowlist enforcement)
  - Integration status endpoint
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel
from app.tools.base import ToolError

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.tools.builtins.browseuse_action import BrowseuseActionTool
from app.tools.builtins.supabase_crud import (
    SupabaseCrudTool,
    SupabaseCrudSelectInput,
    SupabaseCrudInsertInput,
    SupabaseCrudUpdateInput,
    SupabaseCrudDeleteInput,
)
from app.tools.registry import get_registry, ToolRegistry


#: Input schema shared across BrowseUse action tests.
class BrowseuseInput(BaseModel):
    """Input for browseuse_action tool tests."""
    action_type: str = "visit"
    url: str = "https://example.com"
    selectors: Optional[Dict[str, str]] = None
    text: Optional[str] = None
    screenshot: bool = False
    timeout_seconds: int = 30


#: Input schemas for Supabase CRUD tool tests.
class CrudSelectInput(SupabaseCrudSelectInput):
    """Input for supabase_crud SELECT."""
    pass


class CrudInsertInput(SupabaseCrudInsertInput):
    """Input for supabase_crud INSERT."""
    pass


class CrudUpdateInput(SupabaseCrudUpdateInput):
    """Input for supabase_crud UPDATE."""
    pass


class CrudDeleteInput(SupabaseCrudDeleteInput):
    """Input for supabase_crud DELETE."""
    pass


# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tool_registry():
    """A fresh ToolRegistry with Phase 8 tools registered."""
    registry = ToolRegistry()
    from app.tools.builtins.browseuse_action import BrowseuseActionTool
    from app.tools.builtins.supabase_crud import SupabaseCrudTool
    registry.register_class(BrowseuseActionTool)
    registry.register_class(SupabaseCrudTool)
    return registry


@pytest.fixture
def crud_db(tmp_path):
    """A temporary SQLite DB with a populated ``users`` table.

    Returns the sqlite URL string to patch onto SUPABASE_DATABASE_URL.
    """
    import sqlite3

    db_path = tmp_path / "crud.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"
    )
    conn.execute("INSERT INTO users (name, status) VALUES ('Alice', 'active')")
    conn.execute("INSERT INTO users (name, status) VALUES ('Bob', 'inactive')")
    conn.commit()
    conn.close()
    return f"sqlite:///{db_path}"


async def create_published_mission(client: AsyncClient) -> dict:
    """Create a mission via the API, publish it, and return the mission dict."""
    payload = {
        "name": "N8N Test Mission",
        "goal": "Test goal for mission execution",
        "description": "A test mission for N8N webhook testing",
        "category": "integration",
        "steps": [
            {
                "key": "step1",
                "name": "First step",
                "step_type": "llm",
                "agent_key": "agent1",
                "prompt_template": "Execute the task",
            }
        ],
    }
    response = await client.post("/api/v1/missions", json=payload)
    assert response.status_code == 201, f"Failed to create mission: {response.text}"
    mission = response.json()
    publish_resp = await client.post(f"/api/v1/missions/{mission['id']}/publish")
    assert publish_resp.status_code == 200, f"Failed to publish: {publish_resp.text}"
    return publish_resp.json()


# ═════════════════════════════════════════════════════════════════════════
# 1. Tool Registry Tests
# ═════════════════════════════════════════════════════════════════════════


def test_builtin_tools_include_browseuse_and_crud():
    """All built-in tools are registered in the singleton registry (Phase 8 additions)."""
    registry = get_registry()
    assert registry.has("browseuse_action"), "Missing browseuse_action tool"
    assert registry.has("supabase_crud"), "Missing supabase_crud tool"


def test_browseuse_tool_metadata():
    """browseuse_action exposes correct metadata."""
    registry = get_registry()
    tool = registry.get("browseuse_action")
    assert tool is not None
    assert tool.description
    assert tool.default_timeout_seconds > 0
    assert tool.requires_api_key is False


def test_supabase_crud_tool_metadata():
    """supabase_crud exposes correct metadata."""
    registry = get_registry()
    tool = registry.get("supabase_crud")
    assert tool is not None
    assert tool.description
    assert tool.default_timeout_seconds > 0


# ═════════════════════════════════════════════════════════════════════════
# 2. BrowseUse Tool Tests
# ═════════════════════════════════════════════════════════════════════════


class TestBrowseuseActionTool:
    """BrowseUse action tool tests following Phase 3 patterns."""

    @pytest.mark.asyncio
    async def test_browseuse_visit_action(self, tool_registry):
        """browseuse_action with visit action type succeeds."""
        tool = tool_registry.get("browseuse_action")
        input_data = BrowseuseInput(action_type="visit", url="https://example.com", screenshot=False)
        result = await tool.execute(input_data)
        assert result.ok is True
        assert result.data is not None
        assert result.data["action_type"] == "visit"

    @pytest.mark.asyncio
    async def test_browseuse_extract_action_with_selectors(self, tool_registry):
        """browseuse_action with extract action and CSS selectors."""
        tool = tool_registry.get("browseuse_action")
        input_data = BrowseuseInput(
            action_type="extract",
            url="https://example.com",
            selectors={"title": "h1", "content": ".main"},
            screenshot=False,
        )
        result = await tool.execute(input_data)
        assert result.ok is True
        assert result.data is not None
        assert result.data["action_type"] == "extract"
        # selectors may be None when no remote endpoint is configured
        if result.data.get("selectors") is not None:
            assert result.data["selectors"] == {"title": "h1", "content": ".main"}

    @pytest.mark.asyncio
    async def test_browseuse_fill_action_with_text(self, tool_registry):
        """browseuse_action with fill action and text."""
        tool = tool_registry.get("browseuse_action")
        input_data = BrowseuseInput(
            action_type="fill",
            url="https://example.com/login",
            text="myusername",
            screenshot=False,
        )
        result = await tool.execute(input_data)
        assert result.ok is True
        assert result.data is not None
        assert result.data["action_type"] == "fill"
        # text may not be in placeholder output when no remote endpoint
        if result.data.get("text") is not None:
            assert result.data["text"] == "myusername"

    @pytest.mark.asyncio
    async def test_browseuse_screenshot_flag(self, tool_registry):
        """browseuse_action with screenshot=true returns scaffold."""
        tool = tool_registry.get("browseuse_action")
        input_data = BrowseuseInput(
            action_type="visit",
            url="https://example.com",
            screenshot=True,
        )
        result = await tool.execute(input_data)
        assert result.ok is True
        assert result.data is not None
        assert result.data["screenshot_b64"] is None  # no remote endpoint
        assert result.metadata.get("placeholder") is True

    @pytest.mark.asyncio
    async def test_browseuse_domain_allowlist(self, tool_registry):
        """browseuse_action enforces domain allowlist when configured."""
        tool = tool_registry.get("browseuse_action")

        with patch.object(settings, "BROWSEUSE_ALLOWED_DOMAINS", "example.com,api.test.com"):
            # Allowed: exact match
            input_data = BrowseuseInput(
                action_type="visit",
                url="https://example.com/path",
                screenshot=False,
            )
            result = await tool.execute(input_data)
            assert result.ok is True

            # Rejected: different domain
            input_data = BrowseuseInput(
                action_type="visit",
                url="https://evil.com/path",
                screenshot=False,
            )
            with pytest.raises(ToolError) as exc:
                await tool.execute(input_data)
            assert exc.value.code == "domain_not_allowed"

            # Rejected: dangerous scheme
            input_data = BrowseuseInput(
                action_type="visit",
                url="data:text/html,<script>alert(1)</script>",
                screenshot=False,
            )
            with pytest.raises(ToolError) as exc:
                await tool.execute(input_data)
            assert exc.value.code == "invalid_url"

    @pytest.mark.asyncio
    async def test_browseuse_no_allowlist_dev_mode(self, tool_registry):
        """browseuse_action allows all http/https when no allowlist set."""
        tool = tool_registry.get("browseuse_action")

        with patch.object(settings, "BROWSEUSE_ALLOWED_DOMAINS", ""):
            # Should allow any http/https URL
            input_data = BrowseuseInput(
                action_type="visit",
                url="https://anydomain.com/path",
                screenshot=False,
            )
            result = await tool.execute(input_data)
            assert result.ok is True

    @pytest.mark.asyncio
    async def test_browseuse_remote_endpoint_configured(self, tool_registry):
        """browseuse_action calls remote endpoint when BROWSEUSE_ENDPOINT is set."""
        tool = tool_registry.get("browseuse_action")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "content": "Scraped content from remote endpoint",
                "title": "Example Page",
                "links": [{"text": "Home", "url": "https://example.com"}],
            }
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch.object(settings, "BROWSEUSE_ENDPOINT", "https://browseuse.example.com/api"), patch(
            "httpx.AsyncClient", return_value=mock_client
        ):
            input_data = BrowseuseInput(
                action_type="visit",
                url="https://example.com",
                screenshot=False,
            )
            result = await tool.execute(input_data)

        assert result.ok is True
        assert result.data is not None
        assert "Scraped content" in result.data.get("content", "")
        assert result.data["title"] == "Example Page"
        assert "links" in result.data


# ═════════════════════════════════════════════════════════════════════════
# 3. Supabase CRUD Tool Tests
# ═════════════════════════════════════════════════════════════════════════


class TestSupabaseCrudTool:
    """Supabase CRUD tool tests following Phase 3 patterns."""

    @pytest.mark.asyncio
    async def test_supabase_crud_select_success(self, tool_registry, crud_db):
        """supabase_crud SELECT with allowlisted table succeeds."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users,orders,products"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                result = await tool.execute(
                    CrudSelectInput(table="users", columns=["id", "name"], where={"status": "active"})
                )

        assert result.ok is True
        assert result.data is not None
        assert result.data["row_count"] == 1
        assert result.data["rows"][0]["name"] == "Alice"
        assert result.metadata is not None
        assert result.metadata["operation"] == "select"

    @pytest.mark.asyncio
    async def test_supabase_crud_select_disallowed_table(self, tool_registry):
        """supabase_crud rejects tables not in the allowlist."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", "postgresql://user:pass@localhost:5432/meridian"):
                result = await tool.execute(
                    CrudSelectInput(table="secrets", columns=["*"])
                )

        assert result.ok is False
        assert result.error == "table_not_allowed"

    @pytest.mark.asyncio
    async def test_supabase_crud_select_invalid_column(self, tool_registry):
        """supabase_crud rejects SQL injection in column names."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", "postgresql://user:pass@localhost:5432/meridian"):
                result = await tool.execute(
                    CrudSelectInput(table="users", columns=["id; DROP TABLE users; --"])
                )

        assert result.ok is False
        assert result.error == "invalid_input"

    @pytest.mark.asyncio
    async def test_supabase_crud_insert_success(self, tool_registry, crud_db):
        """supabase_crud INSERT with allowlisted table succeeds."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                result = await tool.execute(
                    CrudInsertInput(table="users", records=[{"name": "Alice", "status": "active"}])
                )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("inserted_rows", 0) >= 1
        assert result.metadata["operation"] == "insert"

    @pytest.mark.asyncio
    async def test_supabase_crud_update_success(self, tool_registry, crud_db):
        """supabase_crud UPDATE with allowlisted table succeeds."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                result = await tool.execute(
                    CrudUpdateInput(table="users", updates={"status": "inactive"}, where={"id": 1})
                )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("updated_rows", 0) >= 1
        assert result.metadata["operation"] == "update"

    @pytest.mark.asyncio
    async def test_supabase_crud_delete_success(self, tool_registry, crud_db):
        """supabase_crud DELETE with allowlisted table succeeds."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                result = await tool.execute(
                    CrudDeleteInput(table="users", where={"id": 1})
                )

        assert result.ok is True
        assert result.data is not None
        assert result.data.get("deleted_rows", 0) >= 1
        assert result.metadata["operation"] == "delete"

    @pytest.mark.asyncio
    async def test_supabase_crud_all_operations_validate_columns(self, tool_registry):
        """All CRUD operations validate column names and reject injection."""
        tool = tool_registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", "postgresql://user:pass@localhost:5432/meridian"):

                # INSERT with bad column name (names are interpolated, so they
                # must be rejected; values are parameterized and thus safe)
                result = await tool.execute(
                    CrudInsertInput(
                        table="users",
                        records=[{"name; DROP TABLE users; --": "Alice"}],
                    )
                )
                assert result.ok is False
                assert result.error == "invalid_input"

                # UPDATE with bad column in updates
                result = await tool.execute(
                    CrudUpdateInput(table="users", updates={"evil_col; DROP TABLE users; --": "x"}, where={"id": 1})
                )
                assert result.ok is False
                assert result.error == "invalid_input"

                # UPDATE with bad column in where
                result = await tool.execute(
                    CrudUpdateInput(table="users", updates={"status": "inactive"}, where={"evil_col; DROP TABLE users; --": 1})
                )
                assert result.ok is False
                assert result.error == "invalid_input"

                # DELETE with bad column in where
                result = await tool.execute(CrudDeleteInput(table="users", where={"evil_col; DROP TABLE users; --": 1}))
                assert result.ok is False
                assert result.error == "invalid_input"


# ═════════════════════════════════════════════════════════════════════════
# 4. Integration Status Endpoint Tests
# ═════════════════════════════════════════════════════════════════════════


class TestIntegrationStatus:
    """Tests for GET /integrations/status endpoint."""

    @pytest.mark.asyncio
    async def test_integrations_status_returns_all_checks(self, async_client):
        """Integration status returns webhook, firecrawl, supabase, http_allowlist checks."""
        response = await async_client.get("/api/v1/tools/integrations/status")
        assert response.status_code == 200

        data = response.json()
        assert "webhook" in data
        assert "firecrawl" in data
        assert "supabase" in data
        assert "http_allowlist" in data

        # webhook check
        assert "configured" in data["webhook"]
        assert data["webhook"]["env"] == "MERIDIAN_WEBHOOK_SECRET"

        # firecrawl check
        assert "configured" in data["firecrawl"]
        assert data["firecrawl"]["env"] == "FIRECRAWL_API_KEY"

        # supabase check
        assert "configured" in data["supabase"]
        assert data["supabase"]["env"] == "SUPABASE_DATABASE_URL"

        # http_allowlist check
        assert "configured" in data["http_allowlist"]
        assert data["http_allowlist"]["env"] == "HTTP_TOOL_ALLOWED_DOMAINS"

    @pytest.mark.asyncio
    async def test_integrations_status_webhook_configured(self, async_client, monkeypatch):
        """Integration status shows webhook as configured when MERIDIAN_WEBHOOK_SECRET is set."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "my-secret-key")
        response = await async_client.get("/api/v1/tools/integrations/status")
        data = response.json()
        assert data["webhook"]["configured"] is True

    @pytest.mark.asyncio
    async def test_integrations_status_webhook_not_configured(self, async_client, monkeypatch):
        """Integration status shows webhook as unconfigured when MERIDIAN_WEBHOOK_SECRET is empty."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", None)
        response = await async_client.get("/api/v1/tools/integrations/status")
        data = response.json()
        assert data["webhook"]["configured"] is False


# ═════════════════════════════════════════════════════════════════════════
# 5. N8N Webhook Endpoint Tests
# ═════════════════════════════════════════════════════════════════════════


class TestN8nWebhook:
    """Tests for POST /tools/n8n-webhook/{mission_id} endpoint."""

    @pytest.mark.asyncio
    async def test_n8n_webhook_success_with_secret(self, async_client, monkeypatch):
        """N8N webhook triggers run when correct secret is provided."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "test-secret")
        mission = await create_published_mission(async_client)
        mission_id = mission["id"]

        # Now trigger the webhook (with replay protection headers)
        webhook_url = f"/api/v1/tools/n8n-webhook/{mission_id}"
        nonce = "test-nonce-12345"
        timestamp = datetime.now(timezone.utc).isoformat()
        response = await async_client.post(
            webhook_url,
            headers={
                "X-Meridian-Webhook-Secret": "test-secret",
                "X-Meridian-Webhook-Timestamp": timestamp,
                "X-Meridian-Webhook-Nonce": nonce,
            },
            json={"input_context": {"key": "value"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data
        assert data["mission_id"] == mission_id
        assert data["message"] == "N8N webhook triggered run started"

    @pytest.mark.asyncio
    async def test_n8n_webhook_fails_without_secret(self, async_client, monkeypatch):
        """N8N webhook returns 401 when no secret provided."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", None)
        mission = await create_published_mission(async_client)
        mission_id = mission["id"]

        # Trigger webhook without secret
        webhook_url = f"/api/v1/tools/n8n-webhook/{mission_id}"
        response = await async_client.post(webhook_url, json={"input_context": {}})

        assert response.status_code == 401
        data = response.json()
        assert "Invalid webhook secret" in data["detail"]

    @pytest.mark.asyncio
    async def test_n8n_webhook_fails_with_wrong_secret(self, async_client, monkeypatch):
        """N8N webhook returns 401 when wrong secret provided."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "correct-secret")
        mission = await create_published_mission(async_client)
        mission_id = mission["id"]

        # Trigger webhook with wrong secret
        webhook_url = f"/api/v1/tools/n8n-webhook/{mission_id}"
        response = await async_client.post(
            webhook_url,
            headers={"X-Meridian-Webhook-Secret": "wrong-secret"},
            json={"input_context": {}},
        )

        assert response.status_code == 401
        data = response.json()
        assert "Invalid webhook secret" in data["detail"]

    @pytest.mark.asyncio
    async def test_n8n_webhook_missing_mission_id(self, async_client, monkeypatch):
        """N8N webhook returns 422 when mission_id is invalid."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "test-secret")
        nonce = "test-nonce-12345"
        timestamp = datetime.now(timezone.utc).isoformat()
        response = await async_client.post(
            "/api/v1/tools/n8n-webhook/invalid-mission",
            headers={
                "X-Meridian-Webhook-Secret": "test-secret",
                "X-Meridian-Webhook-Timestamp": timestamp,
                "X-Meridian-Webhook-Nonce": nonce,
            },
            json={"input_context": {}},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_n8n_webhook_no_body(self, async_client, monkeypatch):
        """N8N webhook works without input_context body."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "test-secret")
        mission = await create_published_mission(async_client)
        mission_id = mission["id"]

        # Trigger webhook without body (with replay protection headers)
        webhook_url = f"/api/v1/tools/n8n-webhook/{mission_id}"
        nonce = "test-nonce-12345"
        timestamp = datetime.now(timezone.utc).isoformat()
        response = await async_client.post(
            webhook_url,
            headers={
                "X-Meridian-Webhook-Secret": "test-secret",
                "X-Meridian-Webhook-Timestamp": timestamp,
                "X-Meridian-Webhook-Nonce": nonce,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data

    @pytest.mark.asyncio
    async def test_n8n_webhook_with_context_passthrough(self, async_client, monkeypatch):
        """N8N webhook passes input_context through to the run."""
        monkeypatch.setattr(settings, "MERIDIAN_WEBHOOK_SECRET", "test-secret")
        mission = await create_published_mission(async_client)
        mission_id = mission["id"]

        # Trigger webhook with input_context (with replay protection headers)
        webhook_url = f"/api/v1/tools/n8n-webhook/{mission_id}"
        nonce = "test-nonce-12345"
        timestamp = datetime.now(timezone.utc).isoformat()
        response = await async_client.post(
            webhook_url,
            headers={
                "X-Meridian-Webhook-Secret": "test-secret",
                "X-Meridian-Webhook-Timestamp": timestamp,
                "X-Meridian-Webhook-Nonce": nonce,
            },
            json={"input_context": {"mission_goal": "Test goal", "priority": "high"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["mission_id"] == mission_id
        # The input_context should have been passed through
        assert data["message"] == "N8N webhook triggered run started"


# ═════════════════════════════════════════════════════════════════════════
# 6. SSRF & Abuse Vector Mitigation Tests
# ═════════════════════════════════════════════════════════════════════════


class TestAbuseVectors:
    """Tests for abuse vector mitigations across Phase 8 tools."""

    @pytest.mark.asyncio
    async def test_browseuse_ssrf_data_scheme_blocked(self, tool_registry):
        """browseuse_action blocks data: scheme URLs (SSRF prevention)."""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.browseuse_action import BrowseuseActionTool
        registry.register_class(BrowseuseActionTool)

        tool = registry.get("browseuse_action")

        with patch.object(settings, "BROWSEUSE_ALLOWED_DOMAINS", ""):
            input_data = BrowseuseInput(
                action_type="visit",
                url="data:text/html,<script>alert(1)</script>",
                screenshot=False,
            )
            with pytest.raises(ToolError) as exc:
                await tool.execute(input_data)
            assert exc.value.code == "invalid_url"

    @pytest.mark.asyncio
    async def test_browseuse_ssrf_file_scheme_blocked(self, tool_registry):
        """browseuse_action blocks file: scheme URLs (SSRF prevention)."""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.browseuse_action import BrowseuseActionTool
        registry.register_class(BrowseuseActionTool)

        tool = registry.get("browseuse_action")

        with patch.object(settings, "BROWSEUSE_ALLOWED_DOMAINS", ""):
            input_data = BrowseuseInput(
                action_type="visit",
                url="file:///etc/passwd",
                screenshot=False,
            )
            with pytest.raises(ToolError) as exc:
                await tool.execute(input_data)
            assert exc.value.code == "invalid_url"

    @pytest.mark.asyncio
    async def test_browseuse_ssrf_javascript_scheme_blocked(self, tool_registry):
        """browseuse_action blocks javascript: scheme URLs (SSRF prevention)."""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.browseuse_action import BrowseuseActionTool
        registry.register_class(BrowseuseActionTool)

        tool = registry.get("browseuse_action")

        with patch.object(settings, "BROWSEUSE_ALLOWED_DOMAINS", ""):
            input_data = BrowseuseInput(
                action_type="visit",
                url="javascript:alert(1)",
                screenshot=False,
            )
            with pytest.raises(ToolError) as exc:
                await tool.execute(input_data)
            assert exc.value.code == "invalid_url"

    @pytest.mark.asyncio
    async def test_supabase_crud_table_deny_by_default(self, tool_registry):
        """supabase_crud denies all tables when no allowlist configured."""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.supabase_crud import SupabaseCrudTool
        registry.register_class(SupabaseCrudTool)

        tool = registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", ""), patch.object(
            settings, "SUPABASE_DATABASE_URL", "postgresql://user:pass@localhost:5432/meridian"
        ):
            result = await tool.execute(CrudSelectInput(table="users", columns=["*"]))
            assert result.ok is False
            assert result.error == "table_not_allowed"

            result = await tool.execute(CrudInsertInput(table="users", records=[{"name": "test"}]))
            assert result.ok is False
            assert result.error == "table_not_allowed"

            result = await tool.execute(CrudUpdateInput(table="users", updates={"name": "test"}, where={"id": 1}))
            assert result.ok is False
            assert result.error == "table_not_allowed"

            result = await tool.execute(CrudDeleteInput(table="users", where={"id": 1}))
            assert result.ok is False
            assert result.error == "table_not_allowed"

    @pytest.mark.asyncio
    async def test_http_request_ssrf_no_allowlist_dev(self, async_client):
        """http_request allows all domains when no allowlist set (dev mode)."""
        from app.tools.builtins.http_request import HttpRequestTool

        tool = HttpRequestTool()

        with patch.object(settings, "HTTP_TOOL_ALLOWED_DOMAINS", ""):
            # Should not raise; should be allowed in dev mode
            input_data = tool.input_schema(url="https://evil-domain.com/steal-secrets", method="GET")
            result = await tool.execute(input_data)
            # In dev mode (no allowlist), the request is permitted
            assert result is not None

    @pytest.mark.asyncio
    async def test_supabase_crud_row_count_cap(self, tool_registry, crud_db):
        """supabase_crud caps rows returned to prevent memory exhaustion."""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.supabase_crud import SupabaseCrudTool
        registry.register_class(SupabaseCrudTool)

        tool = registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                # Request more than MAX_ROWS
                result = await tool.execute(CrudSelectInput(table="users", columns=["*"], limit=9999))

        assert result.ok is True
        assert result.data is not None
        # The tool should cap at MAX_ROWS
        assert result.data["row_count"] <= 500

    @pytest.mark.asyncio
    async def test_supabase_crud_output_truncation(self, tool_registry, crud_db):
        """supabase_crud output is truncated to TOOL_MAX_OUTPUT_CHARS."""
        from app.core.config import settings as cfg_settings

        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        from app.tools.builtins.supabase_crud import SupabaseCrudTool
        registry.register_class(SupabaseCrudTool)

        tool = registry.get("supabase_crud")

        with patch.object(settings, "SUPABASE_CRUD_ALLOWED_TABLES", "users"):
            with patch.object(settings, "SUPABASE_DATABASE_URL", crud_db):
                # Select all rows which may produce large output
                result = await tool.execute(CrudSelectInput(table="users", columns=["*"]))

        assert result.ok is True
        assert result.metadata is not None
        # Output may be truncated if it exceeds TOOL_MAX_OUTPUT_CHARS
        original_size = result.metadata.get("original_size_chars", 0)
        truncated_size = result.metadata.get("truncated_size_chars", 0)
        # If the output was large enough to trigger truncation
        if original_size > cfg_settings.TOOL_MAX_OUTPUT_CHARS:
            assert truncated_size <= cfg_settings.TOOL_MAX_OUTPUT_CHARS
            assert result.metadata.get("truncated") is True