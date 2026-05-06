"""Tests for Polaris v2 Agent router.

Covers v2 agent endpoints that delegate to legacy handlers.
External services are mocked to avoid runtime and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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


def _make_fake_session(session_id: str = "sess-123") -> MagicMock:
    session = MagicMock()
    session.id = session_id
    session.role = "assistant"
    session.host_kind = "api_server"
    session.session_type = "standalone"
    session.workspace = "."
    session.title = "Agent assistant session"
    session.state = "active"
    session.message_count = 0
    session.context_config = '{"agent_router_v1": true, "workspace": "."}'
    session.capability_profile = None
    session.created_at.isoformat.return_value = "2024-01-01T00:00:00"
    session.updated_at.isoformat.return_value = "2024-01-01T00:00:00"
    session.to_dict.return_value = {
        "id": session_id,
        "role": "assistant",
        "host_kind": "api_server",
        "session_type": "standalone",
        "workspace": ".",
        "title": "Agent assistant session",
        "state": "active",
        "message_count": 0,
        "context_config": {"agent_router_v1": True, "workspace": "."},
        "capability_profile": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    return session


def _make_fake_message(message_id: str = "msg-1", role: str = "user", content: str = "hello") -> MagicMock:
    msg = MagicMock()
    msg.id = message_id
    msg.conversation_id = "sess-123"
    msg.sequence = 0
    msg.role = role
    msg.content = content
    msg.thinking = None
    msg.meta = None
    msg.to_dict.return_value = {
        "id": message_id,
        "conversation_id": "sess-123",
        "sequence": 0,
        "role": role,
        "content": content,
        "thinking": None,
        "meta": {},
        "created_at": "2024-01-01T00:00:00",
    }
    return msg


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_agent_sessions(client: AsyncClient) -> None:
    """V2 list sessions should return 200 with mocked sessions."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_sessions.return_value = [fake_session]
        mock_service.get_messages.return_value = [fake_message]

        response = await client.get("/v2/agent/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "sess-123"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_agent_session(client: AsyncClient) -> None:
    """V2 get session should return 200 for existing session."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = fake_session
        mock_service.get_messages.return_value = [fake_message]

        response = await client.get("/v2/agent/sessions/sess-123")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess-123"
        assert data["role"] == "assistant"


@pytest.mark.asyncio
async def test_v2_get_agent_session_not_found(client: AsyncClient) -> None:
    """V2 get session should return 404 for missing session."""
    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/agent/sessions/missing-id")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_search_agent_session_memory(client: AsyncClient) -> None:
    """V2 memory search should return 200 with results."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.search_memory.return_value = MagicMock(
            ok=True,
            payload=[{"id": "item-1", "content": "test"}],
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/search",
            params={"q": "test query"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["total"] == 1
        assert data["items"][0]["id"] == "item-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/artifacts/{artifact_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_artifact(client: AsyncClient) -> None:
    """V2 read artifact should return 200 with artifact data."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.read_artifact.return_value = MagicMock(
            ok=True,
            payload={"artifact_id": "art-1", "content": "artifact content"},
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/artifacts/art-1",
            params={"start_line": 1, "end_line": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["artifact"]["artifact_id"] == "art-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/episodes/{episode_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_episode(client: AsyncClient) -> None:
    """V2 read episode should return 200 with episode data."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.read_episode.return_value = MagicMock(
            ok=True,
            payload={"episode_id": "ep-1", "intent": "test"},
            error_code=None,
            error_message=None,
        )

        response = await client.get("/v2/agent/sessions/sess-123/memory/episodes/ep-1")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["episode"]["episode_id"] == "ep-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_state(client: AsyncClient) -> None:
    """V2 read memory state should return 200 with state value."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.get_state.return_value = MagicMock(
            ok=True,
            payload={"current_goal": "test goal"},
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/state",
            params={"path": "run_card"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["path"] == "run_card"
        assert data["value"]["current_goal"] == "test goal"


# ---------------------------------------------------------------------------
# POST /v2/agent/sessions/{session_id}/messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_send_agent_message(client: AsyncClient) -> None:
    """V2 send message should return 200 with reply."""
    with patch(
        "polaris.delivery.http.routers.agent._execute_agent_message",
        new_callable=AsyncMock,
        return_value={
            "ok": True,
            "session_id": "sess-123",
            "reply": "Hello back",
            "reasoning_summary": "thinking...",
            "tool_calls": [],
            "error": None,
        },
    ) as mock_execute:
        response = await client.post(
            "/v2/agent/sessions/sess-123/messages",
            json={"message": "hello", "role": "assistant"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["reply"] == "Hello back"
        assert data["session_id"] == "sess-123"
        mock_execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /v2/agent/sessions/{session_id}/messages/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_send_agent_message_stream(client: AsyncClient) -> None:
    """V2 stream message should return 200 with SSE headers."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    async def _mock_stream(*, output_queue, **kwargs):
        await output_queue.put({"type": "done"})

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent._stream_agent_response",
            side_effect=_mock_stream,
        ),
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        response = await client.post(
            "/v2/agent/sessions/sess-123/messages/stream",
            json={"message": "hello", "role": "assistant"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"


# ---------------------------------------------------------------------------
# DELETE /v2/agent/sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_delete_agent_session(client: AsyncClient) -> None:
    """V2 delete session should return 200 for existing session."""
    fake_session = _make_fake_session("sess-123")

    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = fake_session
        mock_service.delete_session.return_value = True

        response = await client.delete("/v2/agent/sessions/sess-123")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "sess-123" in data["message"]


@pytest.mark.asyncio
async def test_v2_delete_agent_session_not_found(client: AsyncClient) -> None:
    """V2 delete session should return 404 for missing session."""
    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.delete("/v2/agent/sessions/missing-id")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v2/agent/turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_agent_turn(client: AsyncClient) -> None:
    """V2 agent turn should return 200 with reply."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleRuntimeService.execute_role_session",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                ok=True,
                output="Turn reply",
                thinking="thinking...",
                tool_calls=(),
                status="ok",
                error_message=None,
            ),
        ),
        patch(
            "polaris.delivery.http.routers.agent._project_agent_turn",
            return_value=((("user", "hello"),), {"role": "assistant"}),
        ),
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        response = await client.post(
            "/v2/agent/turn",
            json={"message": "hello", "role": "assistant", "stream": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["reply"] == "Turn reply"


@pytest.mark.asyncio
async def test_v2_agent_turn_stream(client: AsyncClient) -> None:
    """V2 agent turn with stream=True should return stream URL."""
    with patch(
        "polaris.delivery.http.routers.agent._get_or_create_session",
        return_value={
            "session_id": "sess-new",
            "role": "assistant",
            "workspace": ".",
        },
    ):
        response = await client.post(
            "/v2/agent/turn",
            json={"message": "hello", "role": "assistant", "stream": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["stream_url"] == "/v2/agent/sessions/sess-new/messages/stream"
