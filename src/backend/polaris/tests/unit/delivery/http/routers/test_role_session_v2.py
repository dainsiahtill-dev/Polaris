"""Tests for Polaris v2 Role Session router.

Covers role session endpoints: create, list, get, update, delete,
messages list, message send, artifacts list, and audit log.
External services are mocked to avoid database and storage dependencies.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
async def test_create_session_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Session creation must use the desktop active workspace when payload omits one."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_session = _make_mock_session(session_id="sess_active", role="pm")

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.create_session.return_value = mock_session

        response = await client.post(
            "/v2/roles/sessions",
            json={
                "role": "pm",
                "host_kind": "electron_workbench",
                "title": "PM Session",
            },
        )

    assert response.status_code == 200
    mock_service_cls.assert_called_once_with(workspace="C:/Temp/Product")
    assert mock_service.create_session.call_args.kwargs["workspace"] == "C:/Temp/Product"


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
async def test_list_sessions_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Session list filters must use the active workspace, not stale repo settings."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"

    with patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_sessions.return_value = [_make_mock_session("sess_1", "pm")]

        response = await client.get("/v2/roles/sessions?role=pm&limit=10")

    assert response.status_code == 200
    mock_service_cls.assert_called_once_with(workspace="C:/Temp/Product")
    assert mock_service.get_sessions.call_args.kwargs["workspace"] == "C:/Temp/Product"


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
    mock_session.to_dict.return_value = {
        **mock_session.to_dict.return_value,
        "message_count": 12,
    }
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
    assert data["total"] == 12
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
    assert data["total"] == 1
    mock_artifact_service.list_artifacts.assert_called_once_with("sess_abc", None)


@pytest.mark.asyncio
async def test_get_artifacts_uses_stored_session_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Artifact lookup must follow the selected session workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.workspace = "C:/Temp/Product"

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = []

        response = await client.get("/v2/roles/sessions/sess_abc/artifacts")

    assert response.status_code == 200
    mock_service_cls.assert_called_once_with(workspace="C:/Active/Workspace")
    mock_artifact_cls.assert_called_once_with(Path("C:/Temp/Product"))


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
        mock_audit_service.get_event_count.return_value = 9

        response = await client.get("/v2/roles/sessions/sess_abc/audit?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert len(data["audit_events"]) == 1
    assert data["audit_events"][0]["type"] == "message_sent"
    assert data["total"] == 9
    mock_audit_service.get_events.assert_called_once_with("sess_abc", None, 5, 0)
    mock_audit_service.get_event_count.assert_called_once_with("sess_abc", None)


@pytest.mark.asyncio
async def test_get_audit_uses_stored_session_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Audit lookup must follow the selected session workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.workspace = "C:/Temp/Product"

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = []

        response = await client.get("/v2/roles/sessions/sess_abc/audit")

    assert response.status_code == 200
    mock_service_cls.assert_called_once_with(workspace="C:/Active/Workspace")
    mock_audit_cls.assert_called_once_with(Path("C:/Temp/Product"))


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


@pytest.mark.asyncio
async def test_append_audit_event_uses_stored_session_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """POST /v2/roles/sessions/{id}/audit/events should append a session audit event."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.workspace = "C:/Temp/Product"
    mock_event = {
        "id": "evt_1",
        "type": "message_sent",
        "details": {"message_id": "msg_1"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.append_audit_event.return_value = mock_event

        response = await client.post(
            "/v2/roles/sessions/sess_abc/audit/events",
            json={"event_type": "message_sent", "details": {"message_id": "msg_1"}},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["event"]["type"] == "message_sent"
    mock_service_cls.assert_called_once_with(workspace="C:/Active/Workspace")
    mock_audit_cls.assert_called_once_with(Path("C:/Temp/Product"))
    mock_audit_service.append_audit_event.assert_called_once_with(
        session_id="sess_abc",
        event_type="message_sent",
        details={"message_id": "msg_1"},
    )


@pytest.mark.asyncio
async def test_export_audit_log_uses_stored_session_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """POST /v2/roles/sessions/{id}/audit/export should export audit without starting workflow."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.workspace = "C:/Temp/Product"

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_event_count.return_value = 3
        mock_audit_service.export_audit_log.return_value = Path("C:/Temp/Product/.polaris/exports/role_sessions/sess_abc.audit.json")

        response = await client.post("/v2/roles/sessions/sess_abc/audit/export")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session_id"] == "sess_abc"
    assert data["event_count"] == 3
    assert data["export_path"].endswith("sess_abc.audit.json")
    mock_service_cls.assert_called_once_with(workspace="C:/Active/Workspace")
    mock_audit_cls.assert_called_once_with(Path("C:/Temp/Product"))
    export_target = mock_audit_service.export_audit_log.call_args.args[1]
    assert isinstance(export_target, Path)
    assert export_target.name == "sess_abc.audit.json"


# ---------------------------------------------------------------------------
# Export To Workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_to_pm_workflow_uses_stored_session_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM export must hand off the selected session workspace to orchestration."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_abc", "pm")
    mock_session.workspace = "C:/Temp/Product"

    mock_artifact = MagicMock()
    mock_artifact.id = "art_1"
    mock_artifact.type = "directive"
    mock_artifact.content = "Implement the desktop workflow"
    mock_artifact.metadata = {}

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
        patch("polaris.infrastructure.storage.LocalFileSystemAdapter"),
        patch("polaris.kernelone.fs.KernelFileSystem") as mock_kernel_fs_cls,
        patch("polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService") as mock_command_cls,
        patch("polaris.delivery.http.routers.role_session.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = [mock_artifact]

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = []

        mock_kernel_fs = MagicMock()
        mock_kernel_fs_cls.return_value = mock_kernel_fs
        mock_kernel_fs.to_workspace_relative_path.return_value = ".polaris/exports/export.json"

        mock_command = MagicMock()
        mock_command.execute_pm_run = AsyncMock(return_value=SimpleNamespace(run_id="pm-run-1"))
        mock_command_cls.return_value = mock_command

        response = await client.post(
            "/v2/roles/sessions/sess_abc/actions/export-to-workflow",
            json={
                "target": "pm",
                "export_kind": "session_bundle",
                "include_audit_log": False,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "pm-run-1"
    mock_roles_ready.assert_called_once()
    assert mock_roles_ready.call_args.kwargs["default_roles"] == ["pm"]
    assert mock_roles_ready.call_args.kwargs["force_first"] == "pm"
    mock_artifact_cls.assert_called_once_with(Path("C:/Temp/Product"))
    mock_audit_cls.assert_called_once_with(Path("C:/Temp/Product"))
    assert mock_command.execute_pm_run.await_args.kwargs["workspace"] == "C:/Temp/Product"
    mock_kernel_fs.workspace_write_text.assert_called_once()


@pytest.mark.asyncio
async def test_export_to_director_workflow_includes_role_session_messages(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director export should preserve desktop dialogue messages when artifacts are absent."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_ce", "chief_engineer")
    mock_session.workspace = "C:/Temp/Product"
    mock_messages = [
        _make_mock_message("msg_user", "user", "Review PM-7 and hand off the backend cache repair."),
        _make_mock_message("msg_assistant", "assistant", "Director should execute PM-7 with cache tests."),
    ]

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
        patch("polaris.infrastructure.storage.LocalFileSystemAdapter"),
        patch("polaris.kernelone.fs.KernelFileSystem") as mock_kernel_fs_cls,
        patch("polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService") as mock_command_cls,
        patch("polaris.delivery.http.routers.role_session.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session
        mock_service.get_messages.return_value = mock_messages

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = []

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = [{"event_type": "message_sent"}]

        mock_kernel_fs = MagicMock()
        mock_kernel_fs_cls.return_value = mock_kernel_fs
        mock_kernel_fs.to_workspace_relative_path.return_value = ".polaris/exports/export.json"

        mock_command = MagicMock()
        mock_command.execute_director_run = AsyncMock(return_value=SimpleNamespace(run_id="director-run-1"))
        mock_command_cls.return_value = mock_command

        response = await client.post(
            "/v2/roles/sessions/sess_ce/actions/export-to-workflow",
            json={
                "target": "director",
                "export_kind": "session_bundle",
                "include_audit_log": True,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "director-run-1"
    mock_roles_ready.assert_called_once()
    assert mock_roles_ready.call_args.kwargs["default_roles"] == ["director"]
    assert mock_roles_ready.call_args.kwargs["force_first"] == "director"
    assert data["artifact_count"] == 0
    assert data["message_count"] == 2
    mock_service.get_messages.assert_called_once_with("sess_ce", limit=200, offset=0)
    director_options = mock_command.execute_director_run.await_args.kwargs["options"]
    assert "Review PM-7" in director_options["task_filter"]
    assert director_options["export_bundle_path"].endswith(".polaris\\exports\\export.json") or director_options[
        "export_bundle_path"
    ].endswith(".polaris/exports/export.json")
    export_payload = json.loads(mock_kernel_fs.workspace_write_text.call_args.args[1])
    assert export_payload["message_count"] == 2
    assert export_payload["messages"][0]["content"] == "Review PM-7 and hand off the backend cache repair."
    assert export_payload["event_count"] == 1
    assert export_payload["audit_events"][0]["event_type"] == "message_sent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "missing_role"),
    [("pm", "pm"), ("director", "director"), ("factory", "pm")],
)
async def test_export_to_role_workflow_blocks_when_runtime_role_not_ready(
    client: AsyncClient,
    mock_settings: Settings,
    target: str,
    missing_role: str,
) -> None:
    """Role-session workflow export should fail closed before creating role runs."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_blocked", target)
    mock_session.workspace = "C:/Temp/Product"

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
        patch("polaris.kernelone.fs.KernelFileSystem") as mock_kernel_fs_cls,
        patch("polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService") as mock_command_cls,
        patch("polaris.delivery.http.routers.role_session.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session
        mock_service.get_messages.return_value = []
        mock_roles_ready.side_effect = StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": [missing_role],
                "missing_roles": [missing_role],
            },
        )

        response = await client.post(
            "/v2/roles/sessions/sess_blocked/actions/export-to-workflow",
            json={
                "target": target,
                "export_kind": "session_bundle",
                "include_audit_log": False,
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == [missing_role]
    mock_artifact_cls.assert_not_called()
    mock_audit_cls.assert_not_called()
    mock_kernel_fs_cls.assert_not_called()
    mock_command_cls.assert_not_called()


@pytest.mark.asyncio
async def test_export_to_factory_workflow_persists_export_metadata(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Factory export should persist RoleSession lineage on the created run."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Active/Workspace"
    mock_session = _make_mock_session("sess_pm", "pm")
    mock_session.workspace = "C:/Temp/Product"
    mock_messages = [
        _make_mock_message("msg_user", "user", "Build the PM Director desktop handoff."),
    ]

    with (
        patch("polaris.delivery.http.routers.role_session.RoleSessionService") as mock_service_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionArtifactService") as mock_artifact_cls,
        patch("polaris.delivery.http.routers.role_session.RoleSessionAuditService") as mock_audit_cls,
        patch("polaris.infrastructure.storage.LocalFileSystemAdapter"),
        patch("polaris.kernelone.fs.KernelFileSystem") as mock_kernel_fs_cls,
        patch("polaris.cells.factory.pipeline.public.service.FactoryRunService") as mock_factory_cls,
        patch("polaris.delivery.http.routers.factory._schedule_factory_run_task") as mock_schedule_factory_run,
        patch("polaris.delivery.http.routers.role_session.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = mock_session
        mock_service.get_messages.return_value = mock_messages

        mock_artifact_service = MagicMock()
        mock_artifact_cls.return_value = mock_artifact_service
        mock_artifact_service.list_artifacts.return_value = []

        mock_audit_service = MagicMock()
        mock_audit_cls.return_value = mock_audit_service
        mock_audit_service.get_events.return_value = []

        mock_kernel_fs = MagicMock()
        mock_kernel_fs_cls.return_value = mock_kernel_fs
        mock_kernel_fs.to_workspace_relative_path.return_value = ".polaris/exports/export.json"

        mock_factory = MagicMock()
        mock_factory.create_run = AsyncMock(return_value=SimpleNamespace(id="factory-run-1"))
        mock_factory.start_run = AsyncMock()
        mock_factory.update_run_metadata = AsyncMock()
        mock_factory_cls.return_value = mock_factory

        response = await client.post(
            "/v2/roles/sessions/sess_pm/actions/export-to-workflow",
            json={
                "target": "factory",
                "export_kind": "session_bundle",
                "include_audit_log": False,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "factory-run-1"
    mock_roles_ready.assert_called_once()
    assert mock_roles_ready.call_args.kwargs["default_roles"] == ["pm", "chief_engineer", "director", "qa"]
    assert mock_roles_ready.call_args.kwargs["force_roles"] == ["pm", "chief_engineer", "director", "qa"]
    mock_factory_cls.assert_called_once_with(workspace=Path("C:/Temp/Product"))
    mock_factory.create_run.assert_awaited_once()
    config = mock_factory.create_run.await_args.args[0]
    assert "chief_engineer_review" in config.stages
    mock_factory.start_run.assert_awaited_once_with("factory-run-1")
    mock_factory.update_run_metadata.assert_awaited_once()
    metadata = mock_factory.update_run_metadata.await_args.args[1]
    assert metadata["export_session_id"] == "sess_pm"
    assert metadata["export_bundle_path"].endswith(".polaris\\exports\\export.json") or metadata[
        "export_bundle_path"
    ].endswith(".polaris/exports/export.json")
    assert "Build the PM Director desktop handoff." in metadata["directive"]
    assert metadata["input_source"] == "role_session"
    assert metadata["factory_start_request"]["input_source"] == "role_session"
    mock_schedule_factory_run.assert_called_once()
    assert mock_schedule_factory_run.call_args.args[1] == "factory-run-1"
