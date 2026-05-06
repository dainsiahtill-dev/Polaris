"""Tests for Polaris v2 Role Session router.

Covers role session endpoints: create, list, get, update, delete,
messages list, message send, artifacts list, and audit log.
External services are mocked to avoid database and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

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
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session(session_id: str = "sess_123", role: str = "pm") -> MagicMock:
    """Build a mock Conversation session with to_dict support."""
    session = MagicMock()
    session.id = session_id
    session.role = role
    session.host_kind = "electron_workbench"
    session.session_type = "workbench"
    session.attachment_mode = "isolated"
    session.workspace = "."
    session.title = f"{role} session"
    session.context_config = "{}"
    session.capability_profile = None
    session.state = "active"
    session.message_count = 0
    session.created_at = datetime.now(timezone.utc)
    session.updated_at = datetime.now(timezone.utc)
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
        "title": f"{role} session",
        "context_config": {},
        "capability_profile": None,
        "state": "active",
        "message_count": 0,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }
    return session


def _make_mock_message(message_id: str = "msg_1", role: str = "user", content: str = "hello") -> MagicMock:
    """Build a mock ConversationMessage with to_dict support."""
    msg = MagicMock()
    msg.id = message_id
    msg.conversation_id = "sess_123"
    msg.sequence = 0
    msg.role = role
    msg.content = content
    msg.thinking = None
    msg.meta = None
    msg.created_at = datetime.now(timezone.utc)
    msg.to_dict.return_value = {
        "id": message_id,
        "conversation_id": "sess_123",
        "sequence": 0,
        "role": role,
        "content": content,
        "thinking": None,
        "meta": {},
        "created_at": msg.created_at.isoformat(),
    }
    return msg


# ---------------------------------------------------------------------------
# Create Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session(client: AsyncClient) -> None:
    """POST /v2/roles/sessions should create a session and return it."""
    mock_session = _make_mock_session(session_id="sess_new", role="architect")

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.create_session.return_value = mock_session

        response = await client.post(
            "/v2/roles/sessions",
            json={
                "role": "architect",
                "host_kind": "electron_workbench",
                "workspace": ".",
                "title": "Arch Session",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"]["id"] == "sess_new"
    assert data["session"]["role"] == "architect"
    mock_service.create_session.assert_called_once()


@pytest.mark.asyncio
async def test_create_session_request_error(client: AsyncClient) -> None:
    """POST /v2/roles/sessions should return 400 on service error."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.create_session.side_effect = ValueError("invalid role")

        response = await client.post(
            "/v2/roles/sessions",
            json={"role": "unknown"},
        )

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "REQUEST_ERROR"


# ---------------------------------------------------------------------------
# List Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions(client: AsyncClient) -> None:
    """GET /v2/roles/sessions should return a list of sessions."""
    mock_sessions = [
        _make_mock_session("sess_1", "pm"),
        _make_mock_session("sess_2", "qa"),
    ]

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_sessions.return_value = mock_sessions

        response = await client.get("/v2/roles/sessions?role=pm&limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["sessions"]) == 2
    assert data["total"] == 2
    mock_service.get_sessions.assert_called_once()


@pytest.mark.asyncio
async def test_list_sessions_request_error(client: AsyncClient) -> None:
    """GET /v2/roles/sessions should return 400 on service error."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_sessions.side_effect = RuntimeError("db down")

        response = await client.get("/v2/roles/sessions")

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "REQUEST_ERROR"


# ---------------------------------------------------------------------------
# Get Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id} should return session details."""
    mock_session = _make_mock_session("sess_abc", "director")

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        response = await client.get("/v2/roles/sessions/sess_abc")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"]["id"] == "sess_abc"
    assert data["session"]["role"] == "director"
    mock_service.get_session.assert_called_once_with("sess_abc")


@pytest.mark.asyncio
async def test_get_session_not_found(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id} should return 404 when missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/roles/sessions/sess_missing")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Update Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_session(client: AsyncClient) -> None:
    """PUT /v2/roles/sessions/{session_id} should update and return the session."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.title = "Updated Title"
    mock_session.to_dict.return_value = {
        **mock_session.to_dict.return_value,
        "title": "Updated Title",
    }

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.update_session.return_value = mock_session

        response = await client.put(
            "/v2/roles/sessions/sess_abc",
            json={"title": "Updated Title", "state": "archived"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"]["title"] == "Updated Title"
    mock_service.update_session.assert_called_once_with(
        session_id="sess_abc",
        title="Updated Title",
        context_config=None,
        capability_profile=None,
        state="archived",
    )


@pytest.mark.asyncio
async def test_update_session_not_found(client: AsyncClient) -> None:
    """PUT /v2/roles/sessions/{session_id} should return 404 when missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.update_session.return_value = None

        response = await client.put(
            "/v2/roles/sessions/sess_missing",
            json={"title": "New Title"},
        )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Delete Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session(client: AsyncClient) -> None:
    """DELETE /v2/roles/sessions/{session_id} should soft-delete and return ok."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.delete_session.return_value = True

        response = await client.delete("/v2/roles/sessions/sess_abc")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    mock_service.delete_session.assert_called_once_with("sess_abc", soft=True)


@pytest.mark.asyncio
async def test_delete_session_not_found(client: AsyncClient) -> None:
    """DELETE /v2/roles/sessions/{session_id} should return 404 when missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.delete_session.return_value = False

        response = await client.delete("/v2/roles/sessions/sess_missing")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_session_hard(client: AsyncClient) -> None:
    """DELETE /v2/roles/sessions/{session_id}?soft=false should hard-delete."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.delete_session.return_value = True

        response = await client.delete("/v2/roles/sessions/sess_abc?soft=false")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    mock_service.delete_session.assert_called_once_with("sess_abc", soft=False)


# ---------------------------------------------------------------------------
# Get Messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_messages(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/messages should return messages."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_messages = [
        _make_mock_message("msg_1", "user", "Hello"),
        _make_mock_message("msg_2", "assistant", "Hi there"),
    ]

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session
        mock_service.get_messages.return_value = mock_messages

        response = await client.get("/v2/roles/sessions/sess_abc/messages?limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["session"]["id"] == "sess_abc"
    mock_service.get_messages.assert_called_once_with("sess_abc", limit=10, offset=0)


@pytest.mark.asyncio
async def test_get_messages_session_not_found(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/messages should return 404 when session missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/roles/sessions/sess_missing/messages")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Send Message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message(client: AsyncClient) -> None:
    """POST /v2/roles/sessions/{session_id}/messages should add a message."""
    mock_session = _make_mock_session("sess_abc", "pm")

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.add_message.return_value = mock_session

        response = await client.post(
            "/v2/roles/sessions/sess_abc/messages",
            json={"role": "user", "content": "Hello PM", "thinking": None, "meta": {}},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session"]["id"] == "sess_abc"
    mock_service.add_message.assert_called_once_with(
        session_id="sess_abc",
        role="user",
        content="Hello PM",
        thinking=None,
        meta={},
    )


@pytest.mark.asyncio
async def test_send_message_session_not_found(client: AsyncClient) -> None:
    """POST /v2/roles/sessions/{session_id}/messages should return 404 when session missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.add_message.return_value = None

        response = await client.post(
            "/v2/roles/sessions/sess_missing/messages",
            json={"role": "user", "content": "Hello"},
        )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Get Artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_artifacts(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/artifacts should return artifacts."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_artifact = MagicMock()
    mock_artifact.to_dict.return_value = {
        "id": "art_1",
        "type": "code",
        "content": "print('hello')",
        "metadata": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": "sess_abc",
    }

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = [mock_artifact]

        response = await client.get("/v2/roles/sessions/sess_abc/artifacts")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["artifacts"]) == 1
    assert data["artifacts"][0]["type"] == "code"
    mock_artifact_service.list_artifacts.assert_called_once_with("sess_abc", None)


@pytest.mark.asyncio
async def test_get_artifacts_session_not_found(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/artifacts should return 404 when session missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/roles/sessions/sess_missing/artifacts")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_artifacts_with_type_filter(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/artifacts?artifact_type=code should filter."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_artifact = MagicMock()
    mock_artifact.to_dict.return_value = {
        "id": "art_1",
        "type": "code",
        "content": "x",
        "metadata": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": "sess_abc",
    }

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = [mock_artifact]

        response = await client.get("/v2/roles/sessions/sess_abc/artifacts?artifact_type=code")

    assert response.status_code == 200
    mock_artifact_service.list_artifacts.assert_called_once_with("sess_abc", "code")


# ---------------------------------------------------------------------------
# Get Audit Log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/audit should return audit events."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_events = [
        {"id": "evt_1", "type": "message_sent", "timestamp": datetime.now(timezone.utc).isoformat()},
    ]

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = mock_events

        response = await client.get("/v2/roles/sessions/sess_abc/audit?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["audit_events"]) == 1
    assert data["audit_events"][0]["type"] == "message_sent"
    mock_audit_service.get_events.assert_called_once_with("sess_abc", None, 5, 0)


@pytest.mark.asyncio
async def test_get_audit_session_not_found(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/audit should return 404 when session missing."""
    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/roles/sessions/sess_missing/audit")

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_audit_with_event_type_filter(client: AsyncClient) -> None:
    """GET /v2/roles/sessions/{session_id}/audit?event_type=message_sent should filter."""
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_events = [
        {"id": "evt_1", "type": "message_sent", "timestamp": datetime.now(timezone.utc).isoformat()},
    ]

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = mock_events

        response = await client.get("/v2/roles/sessions/sess_abc/audit?event_type=message_sent&limit=2&offset=1")

    assert response.status_code == 200
    mock_audit_service.get_events.assert_called_once_with("sess_abc", "message_sent", 2, 1)
