"""Integration tests for the complete role session lifecycle.

Covers happy path, error paths, and list operations for the
/v2/roles/sessions API surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import role_session as role_session_router
from polaris.delivery.http.routers._shared import require_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session(
    session_id: str = "sess_123",
    role: str = "pm",
    state: str = "active",
    title: str = "Test Session",
) -> MagicMock:
    """Build a mock session with to_dict support."""
    now = datetime.now(timezone.utc)
    session = MagicMock()
    session.id = session_id
    session.role = role
    session.host_kind = "electron_workbench"
    session.session_type = "workbench"
    session.attachment_mode = "isolated"
    session.workspace = "."
    session.title = title
    session.context_config = None
    session.capability_profile = None
    session.state = state
    session.message_count = 0
    session.created_at = now
    session.updated_at = now
    session.is_deleted = 0
    session.attached_run_id = None
    session.attached_task_id = None
    session.to_dict.return_value = {
        "id": session_id,
        "role": role,
        "host_kind": "electron_workbench",
        "session_type": "workbench",
        "attachment_mode": "isolated",
        "workspace": ".",
        "title": title,
        "context_config": None,
        "capability_profile": None,
        "state": state,
        "message_count": 0,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    return session


def _make_mock_message(
    message_id: str = "msg_1",
    role: str = "user",
    content: str = "hello",
) -> MagicMock:
    """Build a mock ConversationMessage with to_dict support."""
    now = datetime.now(timezone.utc)
    msg = MagicMock()
    msg.id = message_id
    msg.conversation_id = "sess_123"
    msg.sequence = 0
    msg.role = role
    msg.content = content
    msg.thinking = None
    msg.meta = None
    msg.created_at = now
    msg.to_dict.return_value = {
        "id": message_id,
        "conversation_id": "sess_123",
        "sequence": 0,
        "role": role,
        "content": content,
        "thinking": None,
        "meta": {},
        "created_at": now.isoformat(),
    }
    return msg


def _build_app() -> FastAPI:
    """Build a FastAPI app with the role session router and auth bypassed."""
    app = FastAPI()
    app.include_router(role_session_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root="", qa_enabled=True),
    )
    return app


def _patch_service() -> Any:
    """Return a context manager patch for RoleSessionService."""
    return patch("polaris.delivery.http.routers.role_session.RoleSessionService")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Create an async test client with auth bypassed."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Happy Path Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSessionLifecycleHappyPath:
    """End-to-end happy path tests for the role session lifecycle."""

    async def test_create_session(self, client: AsyncClient) -> None:
        """POST /v2/roles/sessions creates a session."""
        mock_session = _make_mock_session("sess_new", "pm")

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.create_session.return_value = mock_session

            response = await client.post(
                "/v2/roles/sessions",
                json={
                    "role": "pm",
                    "host_kind": "electron_workbench",
                    "workspace": ".",
                    "title": "My PM Session",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["session"]["id"] == "sess_new"
        assert data["session"]["role"] == "pm"
        service.create_session.assert_called_once()

    async def test_get_session_after_creation(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions/{id} returns the created session."""
        mock_session = _make_mock_session("sess_new", "architect")

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_session.return_value = mock_session

            response = await client.get("/v2/roles/sessions/sess_new")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["session"]["id"] == "sess_new"
        assert data["session"]["role"] == "architect"
        service.get_session.assert_called_once_with("sess_new")

    async def test_add_message_to_session(self, client: AsyncClient) -> None:
        """POST /v2/roles/sessions/{id}/messages adds a message."""
        mock_session = _make_mock_session("sess_new", "pm")

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.add_message.return_value = mock_session

            response = await client.post(
                "/v2/roles/sessions/sess_new/messages",
                json={"role": "user", "content": "Hello PM", "thinking": None, "meta": {}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["session"]["id"] == "sess_new"
        service.add_message.assert_called_once_with(
            session_id="sess_new",
            role="user",
            content="Hello PM",
            thinking=None,
            meta={},
        )

    async def test_get_messages_after_adding(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions/{id}/messages returns added messages."""
        mock_session = _make_mock_session("sess_new", "pm")
        mock_messages = [
            _make_mock_message("msg_1", "user", "Hello PM"),
            _make_mock_message("msg_2", "assistant", "Hi there"),
        ]

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_session.return_value = mock_session
            service.get_messages.return_value = mock_messages

            response = await client.get("/v2/roles/sessions/sess_new/messages")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello PM"
        assert data["session"]["id"] == "sess_new"

    async def test_get_memory_state(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions/{id}/memory/state returns memory state."""
        mock_session = _make_mock_session("sess_new", "pm")

        with (
            _patch_service() as mock_cls,
            patch(
                "polaris.delivery.http.routers.role_session.RoleSessionContextMemoryService",
            ) as mock_mem_cls,
        ):
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_session.return_value = mock_session

            mock_result = MagicMock()
            mock_result.ok = True
            mock_result.payload = {"current_goal": "test goal", "active_entities": []}
            mock_mem_service = MagicMock()
            mock_mem_service.get_state.return_value = mock_result
            mock_mem_cls.return_value.__enter__.return_value = mock_mem_service

            response = await client.get(
                "/v2/roles/sessions/sess_new/memory/state",
                params={"path": "run_card"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["path"] == "run_card"
        assert data["value"]["current_goal"] == "test goal"

    async def test_control_session_pause_and_resume(self, client: AsyncClient) -> None:
        """PUT /v2/roles/sessions/{id} controls session state (pause/resume)."""
        paused_session = _make_mock_session("sess_new", "pm", state="paused")
        resumed_session = _make_mock_session("sess_new", "pm", state="active")

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.update_session.side_effect = [paused_session, resumed_session]

            # Pause
            response = await client.put(
                "/v2/roles/sessions/sess_new",
                json={"state": "paused"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["session"]["state"] == "paused"

            # Resume
            response = await client.put(
                "/v2/roles/sessions/sess_new",
                json={"state": "active"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["session"]["state"] == "active"

    async def test_delete_session(self, client: AsyncClient) -> None:
        """DELETE /v2/roles/sessions/{id} deletes the session."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.delete_session.return_value = True

            response = await client.delete("/v2/roles/sessions/sess_new")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        service.delete_session.assert_called_once_with("sess_new", soft=True)

    async def test_get_session_returns_404_after_deletion(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions/{id} returns 404 after the session is deleted."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_session.return_value = None

            response = await client.get("/v2/roles/sessions/sess_new")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Error Paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSessionLifecycleErrorPaths:
    """Error path tests for the role session API."""

    async def test_create_session_invalid_role_returns_400(self, client: AsyncClient) -> None:
        """POST /v2/roles/sessions with invalid role returns 400."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.create_session.side_effect = ValueError("invalid role")

            response = await client.post(
                "/v2/roles/sessions",
                json={"role": "invalid_role_xyz"},
            )

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "REQUEST_ERROR"

    async def test_get_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions/{id} for non-existent session returns 404."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_session.return_value = None

            response = await client.get("/v2/roles/sessions/does_not_exist")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SESSION_NOT_FOUND"

    async def test_add_message_to_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        """POST /v2/roles/sessions/{id}/messages for non-existent session returns 404."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.add_message.return_value = None

            response = await client.post(
                "/v2/roles/sessions/does_not_exist/messages",
                json={"role": "user", "content": "Hello"},
            )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SESSION_NOT_FOUND"

    async def test_delete_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        """DELETE /v2/roles/sessions/{id} for non-existent session returns 404."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.delete_session.return_value = False

            response = await client.delete("/v2/roles/sessions/does_not_exist")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# List Operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSessionListOperations:
    """List and filter tests for the role session API."""

    async def test_list_sessions(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions returns a list of sessions."""
        mock_sessions = [
            _make_mock_session("sess_1", "pm"),
            _make_mock_session("sess_2", "qa"),
            _make_mock_session("sess_3", "architect"),
        ]

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.return_value = mock_sessions

            response = await client.get("/v2/roles/sessions")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["sessions"]) == 3
        assert data["total"] == 3

    async def test_list_sessions_filter_by_role(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions?role=pm filters by role."""
        mock_sessions = [_make_mock_session("sess_1", "pm")]

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.return_value = mock_sessions

            response = await client.get("/v2/roles/sessions", params={"role": "pm"})

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["role"] == "pm"
        service.get_sessions.assert_called_once()
        call_kwargs = service.get_sessions.call_args.kwargs
        assert call_kwargs.get("role") == "pm"

    async def test_list_sessions_filter_by_status(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions?state_filter=active filters by status."""
        mock_sessions = [
            _make_mock_session("sess_1", "pm", state="active"),
            _make_mock_session("sess_2", "pm", state="active"),
        ]

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.return_value = mock_sessions

            response = await client.get(
                "/v2/roles/sessions",
                params={"state_filter": "active"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["sessions"]) == 2
        service.get_sessions.assert_called_once()
        call_kwargs = service.get_sessions.call_args.kwargs
        assert call_kwargs.get("state") == "active"

    async def test_list_sessions_combined_filters(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions with role and status filters combined."""
        mock_sessions = [_make_mock_session("sess_1", "architect", state="paused")]

        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.return_value = mock_sessions

            response = await client.get(
                "/v2/roles/sessions",
                params={"role": "architect", "state_filter": "paused", "limit": 10},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["role"] == "architect"
        assert data["sessions"][0]["state"] == "paused"

    async def test_list_sessions_empty_result(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions returns empty list when no sessions match."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.return_value = []

            response = await client.get(
                "/v2/roles/sessions",
                params={"role": "nonexistent_role"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["sessions"] == []
        assert data["total"] == 0

    async def test_list_sessions_request_error_returns_400(self, client: AsyncClient) -> None:
        """GET /v2/roles/sessions returns 400 when service raises an error."""
        with _patch_service() as mock_cls:
            service = MagicMock()
            mock_cls.return_value.__enter__.return_value = service
            service.get_sessions.side_effect = RuntimeError("db connection failed")

            response = await client.get("/v2/roles/sessions")

        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "REQUEST_ERROR"
