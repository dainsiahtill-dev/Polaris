"""Tests for Polaris v2 Conversations router.

Covers CRUD endpoints for /v2/conversations and message sub-routes.
All database dependencies are mocked to avoid real storage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.roles.session.public import get_db as _original_get_db
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.routers._shared import require_auth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
def db_holder() -> dict[str, MagicMock | None]:
    """Hold the active mock database for FastAPI dependency overrides."""
    return {"db": None}


@pytest.fixture
def set_db(db_holder: dict[str, MagicMock | None]) -> Callable[[MagicMock], None]:
    """Set the mock database session used by the test app."""

    def _set_db(db: MagicMock) -> None:
        db_holder["db"] = db

    return _set_db


@pytest.fixture
async def client(
    mock_settings: Settings,
    mock_app_state: AppState,
    db_holder: dict[str, MagicMock | None],
) -> AsyncIterator[AsyncClient]:
    """Create an async test client with only the conversations router."""
    from polaris.delivery.http.error_handlers import setup_exception_handlers
    from polaris.delivery.http.routers.conversations import router as conversations_router

    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(conversations_router)
    app.dependency_overrides[require_auth] = lambda: None

    def _get_test_db() -> MagicMock:
        if db_holder["db"] is None:
            return _mock_db_session()
        return db_holder["db"]

    app.dependency_overrides[_original_get_db] = _get_test_db

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()
    app.state.settings = mock_settings
    app.state.app_state = mock_app_state

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_conversation(conv_id: str = "conv-1", role: str = "pm") -> MagicMock:
    """Build a mock Conversation instance with to_dict."""
    mock = MagicMock()
    mock.id = conv_id
    mock.title = f"Test {role} conversation"
    mock.role = role
    mock.workspace = "."
    mock.context_config = None
    mock.message_count = 0
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.is_deleted = 0
    mock.messages = []
    mock.to_dict.return_value = {
        "id": conv_id,
        "title": f"Test {role} conversation",
        "role": role,
        "workspace": ".",
        "context_config": {},
        "message_count": 0,
        "created_at": mock.created_at.isoformat(),
        "updated_at": mock.updated_at.isoformat(),
        "messages": [],
    }
    return mock


def _make_mock_message(msg_id: str = "msg-1", conversation_id: str = "conv-1") -> MagicMock:
    """Build a mock ConversationMessage instance with to_dict."""
    mock = MagicMock()
    mock.id = msg_id
    mock.conversation_id = conversation_id
    mock.sequence = 1
    mock.role = "user"
    mock.content = "hello"
    mock.thinking = None
    mock.meta = None
    mock.created_at = datetime.now(timezone.utc)
    mock.to_dict.return_value = {
        "id": msg_id,
        "conversation_id": conversation_id,
        "sequence": 1,
        "role": "user",
        "content": "hello",
        "thinking": None,
        "meta": {},
        "created_at": mock.created_at.isoformat(),
    }
    return mock


def _mock_db_session(
    *,
    conversation: MagicMock | None = None,
    conversations: list[MagicMock] | None = None,
    messages: list[MagicMock] | None = None,
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

    def _query_side_effect(model_cls: type) -> MagicMock:
        # Heuristic: if querying ConversationMessage, return msg query mock
        if hasattr(model_cls, "__tablename__") and getattr(model_cls, "__tablename__", None) == "conversation_messages":
            return msg_query_mock
        return query_mock

    db.query.side_effect = _query_side_effect

    return db


# ---------------------------------------------------------------------------
# POST /v2/conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_success(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Creating a conversation without initial message should succeed."""
    mock_conv = _make_mock_conversation(conv_id="conv-new", role="pm")
    mock_db = _mock_db_session(conversation=mock_conv)
    set_db(mock_db)

    with (
        patch(
            "polaris.delivery.http.routers.conversations.Conversation",
            new=MagicMock(return_value=mock_conv),
        ),
        patch(
            "polaris.delivery.http.routers.conversations.ConversationMessage",
            new=MagicMock(),
        ),
    ):
        response = await client.post(
            "/v2/conversations",
            json={"role": "pm", "title": "My conversation"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "conv-new"
        assert data["role"] == "pm"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_create_conversation_with_initial_message(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Creating a conversation with an initial message should include it."""
    mock_conv = _make_mock_conversation(conv_id="conv-msg", role="architect")
    mock_msg = _make_mock_message(msg_id="msg-1", conversation_id="conv-msg")
    mock_db = _mock_db_session(conversation=mock_conv)
    set_db(mock_db)

    with (
        patch(
            "polaris.delivery.http.routers.conversations.Conversation",
            new=MagicMock(return_value=mock_conv),
        ),
        patch(
            "polaris.delivery.http.routers.conversations.ConversationMessage",
            new=MagicMock(return_value=mock_msg),
        ),
    ):
        response = await client.post(
            "/v2/conversations",
            json={
                "role": "architect",
                "title": "Design chat",
                "initial_message": {
                    "role": "user",
                    "content": "Design a system",
                    "thinking": None,
                    "meta": {"model": "gpt-4"},
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "conv-msg"
        assert mock_db.add.call_count == 2
        mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# GET /v2/conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_success(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Listing conversations should return paginated results."""
    conv1 = _make_mock_conversation(conv_id="conv-1", role="pm")
    conv2 = _make_mock_conversation(conv_id="conv-2", role="qa")
    mock_db = _mock_db_session(conversations=[conv1, conv2], count=2)
    set_db(mock_db)

    response = await client.get("/v2/conversations")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["conversations"]) == 2
    assert data["conversations"][0]["id"] == "conv-1"


@pytest.mark.asyncio
async def test_list_conversations_with_filters(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Listing with role and workspace filters should pass them to the query."""
    conv = _make_mock_conversation(conv_id="conv-pm", role="pm")
    mock_db = _mock_db_session(conversations=[conv], count=1)
    set_db(mock_db)

    response = await client.get("/v2/conversations?role=pm&workspace=.")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["conversations"][0]["role"] == "pm"


# ---------------------------------------------------------------------------
# GET /v2/conversations/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation_success(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Fetching an existing conversation should return its details."""
    mock_conv = _make_mock_conversation(conv_id="conv-abc", role="director")
    mock_db = _mock_db_session(conversation=mock_conv)
    set_db(mock_db)

    response = await client.get("/v2/conversations/conv-abc")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "conv-abc"
    assert data["role"] == "director"


@pytest.mark.asyncio
async def test_get_conversation_not_found(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Fetching a missing conversation should return 404."""
    mock_db = _mock_db_session(conversation=None)
    set_db(mock_db)

    response = await client.get("/v2/conversations/nonexistent")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "CONVERSATION_NOT_FOUND"


# ---------------------------------------------------------------------------
# DELETE /v2/conversations/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation_soft(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Soft-deleting a conversation should set is_deleted."""
    mock_conv = _make_mock_conversation(conv_id="conv-del", role="pm")
    mock_db = _mock_db_session(conversation=mock_conv)
    set_db(mock_db)

    response = await client.delete("/v2/conversations/conv-del")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["deleted_id"] == "conv-del"
    assert mock_conv.is_deleted == 1
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_conversation_hard(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Hard-deleting a conversation should call db.delete."""
    mock_conv = _make_mock_conversation(conv_id="conv-hard", role="qa")
    mock_db = _mock_db_session(conversation=mock_conv)
    set_db(mock_db)

    response = await client.delete("/v2/conversations/conv-hard?hard=true")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    mock_db.delete.assert_called_once_with(mock_conv)
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_conversation_not_found(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Deleting a missing conversation should return 404."""
    mock_db = _mock_db_session(conversation=None)
    set_db(mock_db)

    response = await client.delete("/v2/conversations/missing")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "CONVERSATION_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v2/conversations/{id}/messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_message_success(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Adding a message to an existing conversation should succeed."""
    mock_conv = _make_mock_conversation(conv_id="conv-msg", role="pm")
    mock_msg = _make_mock_message(msg_id="msg-new", conversation_id="conv-msg")
    mock_db = _mock_db_session(conversation=mock_conv, count=0)
    set_db(mock_db)
    message_model = MagicMock(return_value=mock_msg)
    message_model.__tablename__ = "conversation_messages"
    message_model.conversation_id = "conversation_id"

    with patch(
        "polaris.delivery.http.routers.conversations.ConversationMessage",
        new=message_model,
    ):
        response = await client.post(
            "/v2/conversations/conv-msg/messages",
            json={"role": "user", "content": "new message"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "msg-new"
        assert data["content"] == "hello"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_add_message_conversation_not_found(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Adding a message to a missing conversation should return 404."""
    mock_db = _mock_db_session(conversation=None)
    set_db(mock_db)

    response = await client.post(
        "/v2/conversations/missing/messages",
        json={"role": "user", "content": "msg"},
    )
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "CONVERSATION_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/conversations/{id}/messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_success(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Listing messages should return ordered message list."""
    msg1 = _make_mock_message(msg_id="m1", conversation_id="conv-1")
    msg1.sequence = 1
    msg1.to_dict.return_value = {
        "id": "m1",
        "conversation_id": "conv-1",
        "sequence": 1,
        "role": "user",
        "content": "hello",
        "thinking": None,
        "meta": {},
        "created_at": msg1.created_at.isoformat(),
    }
    msg2 = _make_mock_message(msg_id="m2", conversation_id="conv-1")
    msg2.sequence = 2
    msg2.to_dict.return_value = {
        "id": "m2",
        "conversation_id": "conv-1",
        "sequence": 2,
        "role": "assistant",
        "content": "hi there",
        "thinking": None,
        "meta": {},
        "created_at": msg2.created_at.isoformat(),
    }
    mock_db = _mock_db_session(messages=[msg1, msg2])
    set_db(mock_db)

    response = await client.get("/v2/conversations/conv-1/messages")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["sequence"] == 1
    assert data[1]["sequence"] == 2


@pytest.mark.asyncio
async def test_list_messages_empty(
    client: AsyncClient,
    set_db: Callable[[MagicMock], None],
) -> None:
    """Listing messages for a conversation with no messages should return empty list."""
    mock_db = _mock_db_session(messages=[])
    set_db(mock_db)

    response = await client.get("/v2/conversations/conv-empty/messages")
    assert response.status_code == 200
    data = response.json()
    assert data == []
