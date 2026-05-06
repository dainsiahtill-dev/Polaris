"""Integration tests for the complete conversation lifecycle.

Covers happy path, error paths, and filtering/pagination for
POST /v2/conversations and its sub-routes.

All database dependencies are mocked via FastAPI dependency overrides.
Auth is bypassed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.session.public import get_db as _original_get_db
from polaris.delivery.http.routers import conversations as conversations_router
from polaris.delivery.http.routers._shared import require_auth

_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


class _MockConversation:
    """A mock Conversation model that behaves like the real ORM class."""

    def __init__(self, **kwargs: Any) -> None:
        self.id: str = kwargs.get("id", "conv-1")
        self.title: str = kwargs.get("title", "Test Conversation")
        self.role: str = kwargs.get("role", "pm")
        self.workspace: str = kwargs.get("workspace", ".")
        self.context_config: str | None = kwargs.get("context_config")
        self.message_count: int = kwargs.get("message_count", 0)
        self.created_at: datetime = kwargs.get("created_at", _NOW)
        self.updated_at: datetime = kwargs.get("updated_at", _NOW)
        self.is_deleted: int = kwargs.get("is_deleted", 0)
        self.messages: list[_MockMessage] = kwargs.get("messages", [])

    def to_dict(
        self,
        include_messages: bool = False,
        message_limit: int = 1000,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "role": self.role,
            "workspace": self.workspace,
            "context_config": {} if self.context_config is None else {"cfg": True},
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "messages": ([m.to_dict() for m in self.messages[:message_limit]] if include_messages else None),
        }
        return result

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)


class _MockMessage:
    """A mock ConversationMessage model that behaves like the real ORM class."""

    def __init__(self, **kwargs: Any) -> None:
        self.id: str = kwargs.get("id", "msg-1")
        self.conversation_id: str = kwargs.get("conversation_id", "conv-1")
        self.sequence: int = kwargs.get("sequence", 1)
        self.role: str = kwargs.get("role", "user")
        self.content: str = kwargs.get("content", "hello")
        self.thinking: str | None = kwargs.get("thinking")
        self.meta: str | None = kwargs.get("meta")
        self.created_at: datetime = kwargs.get("created_at", _NOW)

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


def _mock_db_session(
    *,
    conversation: _MockConversation | None = None,
    conversations: list[_MockConversation] | None = None,
    messages: list[_MockMessage] | None = None,
    count: int = 0,
) -> MagicMock:
    """Build a mock SQLAlchemy session wired for the conversations router."""
    db = MagicMock()

    query_mock = MagicMock()
    db.query.return_value = query_mock

    filter_mock = MagicMock()
    query_mock.filter.return_value = filter_mock

    # Support chained .filter(...).filter(...)
    filter_mock.filter.return_value = filter_mock
    filter_mock.order_by.return_value = filter_mock
    filter_mock.offset.return_value = filter_mock
    filter_mock.limit.return_value = filter_mock

    if conversation is not None:
        filter_mock.first.return_value = conversation
    else:
        filter_mock.first.return_value = None

    if conversations is not None:
        filter_mock.all.return_value = conversations
    else:
        filter_mock.all.return_value = []

    filter_mock.count.return_value = count

    # Sub-query for messages
    msg_query_mock = MagicMock()
    msg_filter_mock = MagicMock()
    msg_query_mock.filter.return_value = msg_filter_mock
    msg_filter_mock.count.return_value = count
    msg_filter_mock.order_by.return_value = msg_filter_mock
    msg_filter_mock.offset.return_value = msg_filter_mock
    msg_filter_mock.limit.return_value = msg_filter_mock
    msg_filter_mock.all.return_value = messages or []
    msg_filter_mock.first.return_value = None

    def _query_side_effect(model_cls: type) -> MagicMock:
        # Heuristic: if querying ConversationMessage, return msg query mock
        if hasattr(model_cls, "__tablename__") and getattr(model_cls, "__tablename__", None) == "conversation_messages":
            return msg_query_mock
        return query_mock

    db.query.side_effect = _query_side_effect

    return db


def _build_client(db: MagicMock | None = None) -> TestClient:
    """Build a TestClient with the conversations router and mocked DB."""
    app = FastAPI()
    app.include_router(conversations_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[_original_get_db] = lambda: db or _mock_db_session()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


# ============================================================================
# Happy path lifecycle
# ============================================================================


class TestConversationLifecycleHappyPath:
    """End-to-end happy path for conversation CRUD lifecycle."""

    def test_create_conversation(self) -> None:
        """POST /v2/conversations creates a conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-new", title="新对话 - pm", role="pm")

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
            if hasattr(instance, "id") and getattr(instance, "id", None) is None:
                instance.id = "conv-new"
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
            json={"role": "pm", "title": "My conversation"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-new"
        assert payload["role"] == "pm"
        assert payload["title"] == "My conversation"
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_get_conversation_after_create(self) -> None:
        """GET /v2/conversations/{id} returns the created conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(
            id="conv-abc",
            role="architect",
            title="Architecture Chat",
        )

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-abc")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-abc"
        assert payload["role"] == "architect"
        assert payload["title"] == "Architecture Chat"

    def test_add_message_to_conversation(self) -> None:
        """POST /v2/conversations/{id}/messages adds a message."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-msg", role="pm")
        mock_msg = _MockMessage(
            id="msg-1",
            conversation_id="conv-msg",
            sequence=1,
            content="Hello world",
        )

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
            if hasattr(instance, "id") and getattr(instance, "id", None) is None:
                instance.id = "msg-1"
            if hasattr(instance, "created_at") and getattr(instance, "created_at", None) is None:
                instance.created_at = _NOW

        db.add.side_effect = capture_add
        client = _build_client(db)

        response = client.post(
            "/v2/conversations/conv-msg/messages",
            json={"role": "user", "content": "Hello world"},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["conversation_id"] == "conv-msg"
        assert payload["content"] == "Hello world"
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_list_messages_after_add(self) -> None:
        """GET /v2/conversations/{id}/messages returns added messages."""
        db = _mock_db_session()
        msg1 = _MockMessage(
            id="m1",
            conversation_id="conv-1",
            sequence=1,
            role="user",
            content="First message",
        )
        msg2 = _MockMessage(
            id="m2",
            conversation_id="conv-1",
            sequence=2,
            role="assistant",
            content="Second message",
        )

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [msg1, msg2]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-1/messages")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert len(payload) == 2
        assert payload[0]["sequence"] == 1
        assert payload[0]["content"] == "First message"
        assert payload[1]["sequence"] == 2
        assert payload[1]["role"] == "assistant"

    def test_add_multiple_messages(self) -> None:
        """POST /v2/conversations/{id}/messages/batch adds multiple messages."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-batch", role="qa")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            q.count.return_value = 0
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.post(
            "/v2/conversations/conv-batch/messages/batch",
            json=[
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
            ],
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["added_count"] == 3
        assert db.add.call_count == 3
        db.commit.assert_called_once()

    def test_list_conversations(self) -> None:
        """GET /v2/conversations returns paginated conversation list."""
        db = _mock_db_session()
        conv1 = _MockConversation(id="conv-1", role="pm", title="PM Chat")
        conv2 = _MockConversation(id="conv-2", role="qa", title="QA Chat")
        conv3 = _MockConversation(id="conv-3", role="architect", title="Arch Chat")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 3
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [conv1, conv2, conv3]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 3
        assert len(payload["conversations"]) == 3
        assert payload["conversations"][0]["id"] == "conv-1"
        assert payload["conversations"][1]["id"] == "conv-2"
        assert payload["conversations"][2]["id"] == "conv-3"

    def test_delete_conversation(self) -> None:
        """DELETE /v2/conversations/{id} soft-deletes the conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-del", role="pm")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.delete("/v2/conversations/conv-del")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["deleted_id"] == "conv-del"
        assert mock_conv.is_deleted == 1
        db.commit.assert_called_once()

    def test_get_conversation_after_delete_returns_404(self) -> None:
        """GET /v2/conversations/{id} returns 404 after deletion."""
        client = _build_client()

        response = client.get("/v2/conversations/deleted-conv")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"


# ============================================================================
# Error paths
# ============================================================================


class TestConversationLifecycleErrorPaths:
    """Error path coverage for conversation endpoints."""

    def test_create_conversation_invalid_data_returns_422(self) -> None:
        """POST /v2/conversations with missing required field returns 422."""
        client = _build_client()

        # Missing required 'role' field
        response = client.post("/v2/conversations", json={})

        assert response.status_code == 422

    def test_create_conversation_invalid_role_type_returns_422(self) -> None:
        """POST /v2/conversations with wrong type for role returns 422."""
        client = _build_client()

        response = client.post(
            "/v2/conversations",
            json={"role": 123, "title": "Test"},
        )

        assert response.status_code == 422

    def test_get_nonexistent_conversation_returns_404(self) -> None:
        """GET /v2/conversations/{id} for unknown id returns 404."""
        client = _build_client()

        response = client.get("/v2/conversations/does-not-exist")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"
        assert payload["detail"]["message"] == "Conversation not found"

    def test_add_message_to_nonexistent_conversation_returns_404(self) -> None:
        """POST /v2/conversations/{id}/messages for unknown id returns 404."""
        client = _build_client()

        response = client.post(
            "/v2/conversations/does-not-exist/messages",
            json={"role": "user", "content": "hello"},
        )

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    def test_delete_nonexistent_conversation_returns_404(self) -> None:
        """DELETE /v2/conversations/{id} for unknown id returns 404."""
        client = _build_client()

        response = client.delete("/v2/conversations/does-not-exist")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    def test_add_message_invalid_data_returns_422(self) -> None:
        """POST /v2/conversations/{id}/messages with invalid data returns 422."""
        client = _build_client()

        # Missing required 'content' field
        response = client.post(
            "/v2/conversations/conv-1/messages",
            json={"role": "user"},
        )

        assert response.status_code == 422

    def test_batch_add_messages_to_nonexistent_conversation_returns_404(
        self,
    ) -> None:
        """POST /v2/conversations/{id}/messages/batch for unknown id returns 404."""
        client = _build_client()

        response = client.post(
            "/v2/conversations/does-not-exist/messages/batch",
            json=[
                {"role": "user", "content": "msg1"},
            ],
        )

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"


# ============================================================================
# Filtering and pagination
# ============================================================================


class TestConversationLifecycleFilteringAndPagination:
    """Pagination and filter query parameter coverage."""

    def test_list_with_limit_and_offset(self) -> None:
        """GET /v2/conversations?limit=2&offset=1 returns paginated slice."""
        db = _mock_db_session()
        conv2 = _MockConversation(id="conv-2", role="pm")
        conv3 = _MockConversation(id="conv-3", role="qa")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 5
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [conv2, conv3]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations?limit=2&offset=1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 5
        assert len(payload["conversations"]) == 2

    def test_list_filter_by_role(self) -> None:
        """GET /v2/conversations?role=pm filters by role."""
        db = _mock_db_session()
        conv_pm = _MockConversation(id="conv-pm", role="pm")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 1
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [conv_pm]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations?role=pm")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1
        assert payload["conversations"][0]["role"] == "pm"

    def test_list_filter_by_workspace(self) -> None:
        """GET /v2/conversations?workspace=/path filters by workspace."""
        db = _mock_db_session()
        conv_ws = _MockConversation(id="conv-ws", role="architect", workspace="/tmp/ws")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 1
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [conv_ws]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations?workspace=/tmp/ws")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1
        assert payload["conversations"][0]["workspace"] == "/tmp/ws"

    def test_list_messages_with_limit_offset(self) -> None:
        """GET /v2/conversations/{id}/messages?limit=1&offset=1 paginates messages."""
        db = _mock_db_session()
        msg2 = _MockMessage(
            id="m2",
            conversation_id="conv-1",
            sequence=2,
            content="Second",
        )

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [msg2]
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-1/messages?limit=1&offset=1")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert len(payload) == 1
        assert payload[0]["sequence"] == 2

    def test_list_empty_conversations(self) -> None:
        """GET /v2/conversations returns empty list when no conversations exist."""
        db = _mock_db_session()

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 0
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = []
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 0
        assert payload["conversations"] == []

    def test_list_empty_messages(self) -> None:
        """GET /v2/conversations/{id}/messages returns empty list when no messages."""
        db = _mock_db_session()

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = []
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.get("/v2/conversations/conv-empty/messages")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert payload == []

    def test_hard_delete_conversation(self) -> None:
        """DELETE /v2/conversations/{id}?hard=true hard-deletes the conversation."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-hard", role="qa")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.delete("/v2/conversations/conv-hard?hard=true")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["deleted_id"] == "conv-hard"
        db.delete.assert_called_once_with(mock_conv)
        db.commit.assert_called_once()

    def test_update_conversation(self) -> None:
        """PUT /v2/conversations/{id} updates title and context_config."""
        db = _mock_db_session()
        mock_conv = _MockConversation(id="conv-upd", role="pm", title="Old Title")

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = mock_conv
            return q

        db.query.side_effect = _query
        client = _build_client(db)

        response = client.put(
            "/v2/conversations/conv-upd",
            json={"title": "New Title", "context_config": {"key": "value"}},
        )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["id"] == "conv-upd"
        assert mock_conv.title == "New Title"
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_update_nonexistent_conversation_returns_404(self) -> None:
        """PUT /v2/conversations/{id} for unknown id returns 404."""
        client = _build_client()

        response = client.put(
            "/v2/conversations/does-not-exist",
            json={"title": "New Title"},
        )

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    def test_delete_message(self) -> None:
        """DELETE /v2/conversations/{id}/messages/{msg_id} deletes a message."""
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
        """DELETE /v2/conversations/{id}/messages/{msg_id} for unknown msg returns 404."""
        client = _build_client()

        response = client.delete("/v2/conversations/conv-1/messages/missing")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["code"] == "MESSAGE_NOT_FOUND"
