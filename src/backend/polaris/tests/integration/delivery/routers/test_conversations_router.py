"""Contract tests for polaris.delivery.http.routers.conversations module."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.session.public import get_db as _original_get_db
from polaris.delivery.http.routers import conversations as conversations_router
from polaris.delivery.http.routers._shared import require_auth


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


def _build_client(db: MagicMock | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(conversations_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[_original_get_db] = lambda: db or _mock_db_session()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


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


class TestConversationsRouter:
    """Contract tests for the conversations router."""

    def test_create_conversation_happy_path(self) -> None:
        """POST /v2/conversations returns 200 with created conversation."""
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
            # Simulate SQLAlchemy setting attributes after flush/commit
            if hasattr(instance, "id") and instance.id is None:
                instance.id = "conv-1"
            if hasattr(instance, "created_at") and getattr(instance, "created_at", None) is None:
                instance.created_at = _NOW
            if hasattr(instance, "updated_at") and getattr(instance, "updated_at", None) is None:
                instance.updated_at = _NOW
            if hasattr(instance, "message_count") and getattr(instance, "message_count", None) is None:
                instance.message_count = 0

        db.add.side_effect = capture_add
        client = _build_client(db)

        response = client.post(
            "/v2/conversations",
            json={"role": "pm", "title": "Test Conversation"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-1"
        assert payload["role"] == "pm"

    def test_create_conversation_validation_error(self) -> None:
        """POST /v2/conversations with missing role returns 422."""
        client = _build_client()
        response = client.post("/v2/conversations", json={})
        assert response.status_code == 422

    def test_list_conversations_happy_path(self) -> None:
        """GET /v2/conversations returns 200 with conversation list."""
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
        client = _build_client(db)

        response = client.get("/v2/conversations")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1
        assert len(payload["conversations"]) == 1

    def test_get_conversation_happy_path(self) -> None:
        """GET /v2/conversations/{id} returns 200 for existing conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1", role="pm")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-1"

    def test_get_conversation_not_found(self) -> None:
        """GET /v2/conversations/{id} returns 404 for missing conversation."""
        client = _build_client()
        response = client.get("/v2/conversations/missing-id")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_conversation_happy_path(self) -> None:
        """DELETE /v2/conversations/{id} returns 200 with ok flag."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.delete("/v2/conversations/conv-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["deleted_id"] == "conv-1"

    def test_add_message_happy_path(self) -> None:
        """POST /v2/conversations/{id}/messages returns 200 with created message."""
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
        client = _build_client(db)

        response = client.post(
            "/v2/conversations/conv-1/messages",
            json={"role": "user", "content": "hello"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["conversation_id"] == "conv-1"

    def test_add_message_not_found(self) -> None:
        """POST /v2/conversations/{id}/messages returns 404 for missing conversation."""
        client = _build_client()
        response = client.post(
            "/v2/conversations/missing-id/messages",
            json={"role": "user", "content": "hello"},
        )

        assert response.status_code == 404

    def test_list_messages_happy_path(self) -> None:
        """GET /v2/conversations/{id}/messages returns 200 with message list."""
        db = _mock_db_session()
        mock_msg = _MockMessage(id="msg-1", conversation_id="conv-1")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_msg]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-1/messages")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert len(payload) == 1
        assert payload[0]["content"] == "hello"

    def test_add_messages_batch_happy_path(self) -> None:
        """POST /v2/conversations/{id}/messages/batch returns 200 with count."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-1")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            q.count.return_value = 0
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.post(
            "/v2/conversations/conv-1/messages/batch",
            json=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["added_count"] == 2

    def test_delete_message_happy_path(self) -> None:
        """DELETE /v2/conversations/{id}/messages/{msg_id} returns 200."""
        db = _mock_db_session()
        mock_msg = _MockMessage(id="msg-1", conversation_id="conv-1")
        mock_conv = _MockConversation(id="conv-1")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            if getattr(model, "__name__", None) == "ConversationMessage":
                q.first.return_value = mock_msg
            else:
                q.first.return_value = mock_conv
            q.count.return_value = 0
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.delete("/v2/conversations/conv-1/messages/msg-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["deleted_id"] == "msg-1"

    def test_delete_message_not_found(self) -> None:
        """DELETE /v2/conversations/{id}/messages/{msg_id} returns 404."""
        client = _build_client()
        response = client.delete("/v2/conversations/conv-1/messages/missing")

        assert response.status_code == 404
