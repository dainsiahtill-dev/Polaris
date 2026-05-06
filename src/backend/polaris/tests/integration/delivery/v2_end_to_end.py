"""Integration tests for critical v2 end-to-end flows.

Covers four critical flows:
1. Role Chat Flow
2. Session Flow
3. Conversation Flow
4. Factory Run Flow

All business logic is mocked at the service boundary.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from polaris.cells.roles.session.public import get_db as _original_get_db
from polaris.delivery.http.routers import (
    conversations as conversations_router,
    factory as factory_router,
    role_chat as role_chat_router,
    role_session as role_session_router,
)
from polaris.delivery.http.routers._shared import require_auth

# ============================================================================
# App builder
# ============================================================================


def _build_app() -> FastAPI:
    """Build a FastAPI app with all four v2 routers and auth bypassed."""
    app = FastAPI()
    app.include_router(role_chat_router.router)
    app.include_router(role_session_router.router)
    app.include_router(conversations_router.router)
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root="", qa_enabled=True),
    )
    return app


# ============================================================================
# Role Chat Flow
# ============================================================================


@pytest.mark.asyncio
class TestRoleChatFlow:
    """End-to-end tests for the Role Chat flow."""

    async def test_list_roles(self) -> None:
        """GET /v2/role/chat/roles returns supported roles."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "chief_engineer", "director", "qa"],
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/chat/roles")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["count"] == 5
        assert "pm" in payload["roles"]

    async def test_pm_chat_status_ready(self) -> None:
        """GET /v2/role/pm/chat/status returns ready when configured."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={"roles": {"pm": {"ready": True}}},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={
                    "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                    "providers": {"openai": {"type": "openai"}},
                },
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True
        assert payload["configured"] is True
        assert payload["role"] == "pm"
        assert payload["role_config"]["provider_id"] == "openai"
        assert payload["role_config"]["model"] == "gpt-4"

    async def test_pm_chat_status_not_configured(self) -> None:
        """GET /v2/role/pm/chat/status returns not configured when role missing."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={"roles": {}, "providers": {}},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.get_registered_roles",
                return_value=["pm", "architect"],
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is False
        assert payload["configured"] is False
        assert "PM role not configured" in payload["error"]

    async def test_role_chat_message(self) -> None:
        """POST /v2/role/pm/chat returns AI response."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.get_registered_roles",
                return_value=["pm", "architect", "director", "qa"],
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
                return_value=None,
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.generate_role_response",
                new_callable=AsyncMock,
                return_value={
                    "response": "Hello, I am PM",
                    "thinking": "Thinking about project management...",
                    "role": "pm",
                    "model": "gpt-4",
                    "provider": "openai",
                },
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/role/pm/chat",
                    json={"message": "What is the project status?"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["response"] == "Hello, I am PM"
        assert payload["role"] == "pm"
        assert payload["model"] == "gpt-4"

    async def test_role_chat_unsupported_role(self) -> None:
        """POST /v2/role/unknown/chat returns 400 for unsupported role."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect"],
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/role/unknown/chat",
                    json={"message": "hello"},
                )

        assert response.status_code == 400
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "UNSUPPORTED_ROLE"

    async def test_role_chat_missing_message(self) -> None:
        """POST /v2/role/pm/chat returns 400 when message is empty."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect"],
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/role/pm/chat",
                    json={"message": ""},
                )

        assert response.status_code == 400
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "INVALID_REQUEST"


# ============================================================================
# Session Flow
# ============================================================================


@pytest.mark.asyncio
class TestSessionFlow:
    """End-to-end tests for the Session flow."""

    def _mock_session(self, session_id: str = "sess-1", role: str = "pm") -> MagicMock:
        """Return a mock session object."""
        session = MagicMock()
        session.id = session_id
        session.role = role
        session.title = f"Test {role} session"
        session.workspace = "."
        session.host_kind = "electron_workbench"
        session.session_type = "workbench"
        session.attachment_mode = "isolated"
        session.context_config = None
        session.capability_profile = None
        session.state = "active"
        session.message_count = 0
        session.created_at = "2026-04-24T00:00:00"
        session.updated_at = "2026-04-24T00:00:00"
        session.to_dict.return_value = {
            "id": session_id,
            "role": role,
            "title": f"Test {role} session",
            "workspace": ".",
            "host_kind": "electron_workbench",
            "session_type": "workbench",
            "attachment_mode": "isolated",
            "context_config": None,
            "capability_profile": None,
            "state": "active",
            "message_count": 0,
            "created_at": "2026-04-24T00:00:00",
            "updated_at": "2026-04-24T00:00:00",
        }
        return session

    async def test_create_session(self) -> None:
        """POST /v2/roles/sessions creates a new session."""
        app = _build_app()
        service = MagicMock()
        mock_session = self._mock_session("sess-1", "pm")
        service.create_session.return_value = mock_session

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/roles/sessions",
                    json={"role": "pm", "title": "My PM Session"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["session"]["id"] == "sess-1"
        assert payload["session"]["role"] == "pm"

    async def test_get_session(self) -> None:
        """GET /v2/roles/sessions/{id} returns session details."""
        app = _build_app()
        service = MagicMock()
        mock_session = self._mock_session("sess-1", "architect")
        service.get_session.return_value = mock_session

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/roles/sessions/sess-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["session"]["id"] == "sess-1"
        assert payload["session"]["role"] == "architect"

    async def test_get_session_not_found(self) -> None:
        """GET /v2/roles/sessions/{id} returns 404 for missing session."""
        app = _build_app()
        service = MagicMock()
        service.get_session.return_value = None

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/roles/sessions/missing-id")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "SESSION_NOT_FOUND"

    async def test_send_message_to_session(self) -> None:
        """POST /v2/roles/sessions/{id}/messages adds a message."""
        app = _build_app()
        service = MagicMock()
        mock_session = self._mock_session("sess-1", "pm")
        service.get_session.return_value = mock_session
        service.add_message.return_value = mock_session

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/roles/sessions/sess-1/messages",
                    json={"role": "user", "content": "Hello PM"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["session"]["id"] == "sess-1"

    async def test_delete_session(self) -> None:
        """DELETE /v2/roles/sessions/{id} deletes a session."""
        app = _build_app()
        service = MagicMock()
        service.delete_session.return_value = True

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/v2/roles/sessions/sess-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    async def test_delete_session_not_found(self) -> None:
        """DELETE /v2/roles/sessions/{id} returns 404 for missing session."""
        app = _build_app()
        service = MagicMock()
        service.delete_session.return_value = False

        with patch(
            "polaris.delivery.http.routers.role_session.RoleSessionService",
            return_value=service,
        ) as mock_cls:
            mock_cls.return_value.__enter__ = lambda self: service
            mock_cls.return_value.__exit__ = lambda self, *args: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/v2/roles/sessions/missing-id")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "SESSION_NOT_FOUND"


# ============================================================================
# Conversation Flow
# ============================================================================


def _mock_db_session() -> MagicMock:
    """Return a mock DB session with basic query support."""
    db = MagicMock()
    query_mock = MagicMock()
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []
    db.query.return_value = query_mock
    db.add.return_value = None
    db.flush.return_value = None
    db.commit.return_value = None
    db.refresh.return_value = None
    db.delete.return_value = None
    return db


_NOW = datetime(2026, 4, 24, 0, 0, 0)


class _MockConversation:
    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "conv-1")
        self.title = kwargs.get("title", "Test")
        self.role = kwargs.get("role", "pm")
        self.workspace = kwargs.get("workspace", ".")
        self.context_config = kwargs.get("context_config")
        self.message_count = kwargs.get("message_count", 0)
        self.created_at = _NOW
        self.updated_at = _NOW
        self.is_deleted = 0

    def to_dict(self, include_messages: bool = False, message_limit: int = 1000) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "role": self.role,
            "workspace": self.workspace,
            "context_config": {} if self.context_config is None else {"cfg": True},
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "messages": [] if include_messages else None,
        }

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)


class _MockMessage:
    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "msg-1")
        self.conversation_id = kwargs.get("conversation_id", "conv-1")
        self.sequence = kwargs.get("sequence", 1)
        self.role = kwargs.get("role", "user")
        self.content = kwargs.get("content", "hello")
        self.thinking = kwargs.get("thinking")
        self.meta = kwargs.get("meta")
        self.created_at = _NOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "meta": {} if self.meta is None else {"meta": True},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)


class TestConversationFlow:
    """End-to-end tests for the Conversation flow."""

    def _build_client(self, db: MagicMock | None = None) -> TestClient:
        app = _build_app()
        app.dependency_overrides[_original_get_db] = lambda: db or _mock_db_session()
        return TestClient(app)

    def test_create_conversation(self) -> None:
        """POST /v2/conversations creates a new conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1", title="新对话 - pm", role="pm")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            q.count.return_value = 0
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_conv]
            return q

        db.query.side_effect = _query

        def capture_add(instance: Any) -> None:
            if hasattr(instance, "id") and instance.id is None:
                instance.id = "conv-1"
            if hasattr(instance, "created_at") and getattr(instance, "created_at", None) is None:
                instance.created_at = _NOW
            if hasattr(instance, "updated_at") and getattr(instance, "updated_at", None) is None:
                instance.updated_at = _NOW
            if hasattr(instance, "message_count") and getattr(instance, "message_count", None) is None:
                instance.message_count = 0

        db.add.side_effect = capture_add
        client = self._build_client(db)

        response = client.post(
            "/v2/conversations",
            json={"role": "pm", "title": "Test Conversation"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-1"
        assert payload["role"] == "pm"

    def test_list_conversations(self) -> None:
        """GET /v2/conversations returns conversation list."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1", role="pm")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 1
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_conv]
            return q

        db.query.side_effect = _query
        client = self._build_client(db)

        response = client.get("/v2/conversations")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1
        assert len(payload["conversations"]) == 1

    def test_add_message_to_conversation(self) -> None:
        """POST /v2/conversations/{id}/messages adds a message."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1")
        mock_msg = _MockMessage(id="msg-1", conversation_id="conv-1")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            q.count.return_value = 0
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_msg]
            return q

        db.query.side_effect = _query

        def capture_add(instance: Any) -> None:
            if hasattr(instance, "id") and instance.id is None:
                instance.id = "msg-1"
            if hasattr(instance, "created_at") and getattr(instance, "created_at", None) is None:
                instance.created_at = _NOW

        db.add.side_effect = capture_add
        client = self._build_client(db)

        response = client.post(
            "/v2/conversations/conv-1/messages",
            json={"role": "user", "content": "hello"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["conversation_id"] == "conv-1"
        assert payload["content"] == "hello"

    def test_list_messages_in_conversation(self) -> None:
        """GET /v2/conversations/{id}/messages returns message list."""
        db = _mock_db_session()
        mock_msg = _MockMessage(id="msg-1", conversation_id="conv-1", content="hello world")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_msg]
            return q

        db.query.side_effect = _query
        client = self._build_client(db)

        response = client.get("/v2/conversations/conv-1/messages")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert len(payload) == 1
        assert payload[0]["content"] == "hello world"

    def test_add_message_conversation_not_found(self) -> None:
        """POST /v2/conversations/{id}/messages returns 404 for missing conversation."""
        client = self._build_client()
        response = client.post(
            "/v2/conversations/missing-id/messages",
            json={"role": "user", "content": "hello"},
        )

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"


# ============================================================================
# Factory Run Flow
# ============================================================================


def _mock_factory_run(run_id: str = "run-1", status: Any = None) -> MagicMock:
    """Return a mock FactoryRun."""
    run = MagicMock()
    run.id = run_id
    run.status = status or MagicMock()
    run.config.stages = ["pm_planning", "quality_gate"]
    run.stages_completed = []
    run.stages_failed = []
    run.metadata = {}
    run.recovery_point = None
    run.created_at = "2026-04-24T00:00:00"
    run.started_at = None
    run.updated_at = None
    run.completed_at = None
    return run


def _make_mock_factory_service() -> MagicMock:
    """Return a mock FactoryRunService with async methods."""
    service = MagicMock()
    service.list_runs = AsyncMock(return_value=[])
    service.get_run = AsyncMock(return_value=None)
    service.create_run = AsyncMock(return_value=_mock_factory_run("run-1"))
    service.start_run = AsyncMock(return_value=_mock_factory_run("run-1"))
    service.get_run_events = AsyncMock(return_value=[])
    service.cancel_run = AsyncMock(return_value=_mock_factory_run("run-1"))
    service.store = MagicMock()
    service.store.get_run_dir.return_value = MagicMock(
        __truediv__=lambda self, other: MagicMock(
            exists=lambda: True,
            iterdir=lambda: [
                MagicMock(
                    is_file=lambda: True,
                    name="artifact.txt",
                    relative_to=lambda p: "artifact.txt",
                    stat=lambda: MagicMock(st_size=100),
                ),
            ],
        ),
    )
    return service


class TestFactoryRunFlow:
    """End-to-end tests for the Factory Run flow."""

    def _build_client(self) -> TestClient:
        app = _build_app()
        return TestClient(app)

    def test_create_factory_run(self) -> None:
        """POST /v2/factory/runs creates and starts a factory run."""
        client = self._build_client()
        mock_service = _make_mock_factory_service()
        mock_run = _mock_factory_run("run-1")
        mock_service.create_run.return_value = mock_run
        mock_service.start_run.return_value = mock_run

        with (
            patch(
                "polaris.delivery.http.routers.factory.FactoryRunService",
                return_value=mock_service,
            ),
            patch(
                "polaris.delivery.http.routers.factory.sync_process_settings_environment",
            ),
            patch(
                "polaris.delivery.http.routers.factory.save_persisted_settings",
            ),
            patch(
                "polaris.delivery.http.routers.factory.create_task_with_context",
            ),
            patch(
                "polaris.delivery.http.routers.factory._check_docs_ready",
                return_value=True,
            ),
        ):
            response = client.post(
                "/v2/factory/runs",
                json={
                    "directive": "Implement login feature",
                    "workspace": ".",
                    "start_from": "pm",
                    "run_director": False,
                    "loop": False,
                    "director_iterations": 1,
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_get_factory_run_status(self) -> None:
        """GET /v2/factory/runs/{id} returns run status."""
        client = self._build_client()
        mock_service = _make_mock_factory_service()
        mock_run = _mock_factory_run("run-1")
        mock_service.get_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/run-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_get_factory_run_status_not_found(self) -> None:
        """GET /v2/factory/runs/{id} returns 404 for missing run."""
        client = self._build_client()
        mock_service = _make_mock_factory_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/missing-id")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "RUN_NOT_FOUND"

    def test_cancel_factory_run(self) -> None:
        """POST /v2/factory/runs/{id}/control cancels a run."""
        client = self._build_client()
        mock_service = _make_mock_factory_service()
        mock_run = _mock_factory_run("run-1")
        mock_service.get_run.return_value = mock_run
        mock_service.cancel_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.post(
                "/v2/factory/runs/run-1/control",
                json={"action": "cancel", "reason": "User requested cancellation"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_cancel_factory_run_not_found(self) -> None:
        """POST /v2/factory/runs/{id}/control returns 404 for missing run."""
        client = self._build_client()
        mock_service = _make_mock_factory_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.post(
                "/v2/factory/runs/missing-id/control",
                json={"action": "cancel", "reason": "test"},
            )

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "RUN_NOT_FOUND"

    def test_create_factory_run_validation_error(self) -> None:
        """POST /v2/factory/runs with invalid payload returns 422."""
        client = self._build_client()
        response = client.post("/v2/factory/runs", json={})
        assert response.status_code == 422
