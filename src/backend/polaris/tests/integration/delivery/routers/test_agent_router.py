"""Contract tests for polaris.delivery.http.routers.agent module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import agent as agent_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


def _mock_session_service(session_payload: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock RoleSessionService context manager."""
    service = MagicMock()
    if session_payload is not None:
        mock_session = MagicMock()
        mock_session.id = session_payload.get("id", "sess-1")
        mock_session.to_dict.return_value = session_payload
        service.get_session.return_value = mock_session
        service.get_messages.return_value = []
    else:
        service.get_session.return_value = None
    return service


class TestAgentRouter:
    """Contract tests for the agent router."""

    def test_list_agent_sessions_happy_path(self) -> None:
        """GET /agent/sessions returns 200 with session list."""
        client = _build_client()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_session.to_dict.return_value = {
            "id": "sess-1",
            "role": "assistant",
            "context_config": {"agent_router_v1": True},
            "workspace": ".",
            "message_count": 0,
            "created_at": "2026-04-24T00:00:00",
            "updated_at": "2026-04-24T00:00:00",
            "title": "Agent assistant session",
        }

        service = MagicMock()
        service.get_sessions.return_value = [mock_session]
        service.get_messages.return_value = []

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "sessions" in payload
        assert payload["total"] >= 0

    def test_get_agent_session_not_found(self) -> None:
        """GET /agent/sessions/{session_id} returns 404 for missing session."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions/missing-id")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_agent_session_not_found(self) -> None:
        """DELETE /agent/sessions/{session_id} returns ok=False for missing session."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.delete("/agent/sessions/missing-id")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is False

    def test_agent_turn_happy_path(self) -> None:
        """POST /agent/turn returns 200 with reply."""
        client = _build_client()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_session.to_dict.return_value = {
            "id": "sess-1",
            "role": "assistant",
            "context_config": {"agent_router_v1": True},
            "workspace": ".",
            "message_count": 0,
            "created_at": "2026-04-24T00:00:00",
            "updated_at": "2026-04-24T00:00:00",
            "title": "Agent assistant session",
        }

        session_service = MagicMock()
        session_service.get_session.return_value = mock_session
        session_service.get_messages.return_value = []
        session_service.add_message.return_value = None

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.output = "Hello from agent"
        mock_result.thinking = "thinking..."
        mock_result.tool_calls = []
        mock_result.error_message = None
        mock_result.status = "completed"

        projection = MagicMock()
        projection.changed = False
        projection.recent_messages = []
        projection.persisted_context_config = {}
        projection.prompt_context = {}

        original_project = agent_router._AGENT_CONTINUITY_ENGINE.project
        agent_router._AGENT_CONTINUITY_ENGINE.project = lambda *args, **kwargs: projection
        try:
            with (
                patch(
                    "polaris.delivery.http.routers.agent.RoleSessionService",
                    return_value=session_service,
                ) as mock_cls,
                patch.object(
                    agent_router._ROLE_RUNTIME,
                    "execute_role_session",
                    new_callable=AsyncMock,
                    return_value=mock_result,
                ),
                patch(
                    "polaris.delivery.http.routers.agent.SessionContinuityRequest",
                    side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
                ),
                patch(
                    "polaris.delivery.http.routers.agent.ExecuteRoleSessionCommandV1",
                    side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
                ),
            ):
                mock_cls.return_value.__enter__ = lambda self: session_service
                mock_cls.return_value.__exit__ = lambda self, *args: None
                response = client.post(
                    "/agent/turn",
                    json={"message": "hi", "role": "assistant"},
                )
        finally:
            agent_router._AGENT_CONTINUITY_ENGINE.project = original_project

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "reply" in payload
        assert "session_id" in payload

    def test_agent_turn_validation_error(self) -> None:
        """POST /agent/turn with invalid payload returns 422."""
        client = _build_client()
        response = client.post("/agent/turn", json={"message": ""})
        assert response.status_code == 422

    def test_send_agent_message_happy_path(self) -> None:
        """POST /agent/sessions/{session_id}/messages returns 200 with reply."""
        client = _build_client()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_session.to_dict.return_value = {
            "id": "sess-1",
            "role": "assistant",
            "context_config": {"agent_router_v1": True},
            "workspace": ".",
            "message_count": 0,
            "created_at": "2026-04-24T00:00:00",
            "updated_at": "2026-04-24T00:00:00",
            "title": "Agent assistant session",
        }

        session_service = MagicMock()
        session_service.get_session.return_value = mock_session
        session_service.get_messages.return_value = []
        session_service.add_message.return_value = None

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.output = "Reply"
        mock_result.thinking = None
        mock_result.tool_calls = []
        mock_result.error_message = None
        mock_result.status = "completed"

        projection = MagicMock()
        projection.changed = False
        projection.recent_messages = []
        projection.persisted_context_config = {}
        projection.prompt_context = {}

        original_project = agent_router._AGENT_CONTINUITY_ENGINE.project
        agent_router._AGENT_CONTINUITY_ENGINE.project = lambda *args, **kwargs: projection
        try:
            with (
                patch(
                    "polaris.delivery.http.routers.agent.RoleSessionService",
                    return_value=session_service,
                ) as mock_cls,
                patch.object(
                    agent_router._ROLE_RUNTIME,
                    "execute_role_session",
                    new_callable=AsyncMock,
                    return_value=mock_result,
                ),
                patch(
                    "polaris.delivery.http.routers.agent.SessionContinuityRequest",
                    side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
                ),
                patch(
                    "polaris.delivery.http.routers.agent.ExecuteRoleSessionCommandV1",
                    side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
                ),
            ):
                mock_cls.return_value.__enter__ = lambda self: session_service
                mock_cls.return_value.__exit__ = lambda self, *args: None
                response = client.post(
                    "/agent/sessions/sess-1/messages",
                    json={"message": "hello", "role": "assistant"},
                )
        finally:
            agent_router._AGENT_CONTINUITY_ENGINE.project = original_project

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    def test_search_agent_session_memory_not_found(self) -> None:
        """GET /agent/sessions/{session_id}/memory/search returns 404 for missing session."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions/missing-id/memory/search?q=test")

        assert response.status_code == 404

    def test_read_agent_session_memory_artifact_not_found(self) -> None:
        """GET /agent/sessions/{session_id}/memory/artifacts/{artifact_id} returns 404."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions/missing-id/memory/artifacts/art-1")

        assert response.status_code == 404

    def test_read_agent_session_memory_episode_not_found(self) -> None:
        """GET /agent/sessions/{session_id}/memory/episodes/{episode_id} returns 404."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions/missing-id/memory/episodes/ep-1")

        assert response.status_code == 404

    def test_read_agent_session_memory_state_not_found(self) -> None:
        """GET /agent/sessions/{session_id}/memory/state returns 404 for missing session."""
        client = _build_client()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            response = client.get("/agent/sessions/missing-id/memory/state?path=test")

        assert response.status_code == 404
