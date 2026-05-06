"""Tests for Polaris unified role chat endpoints.

Covers POST /v2/role/{role}/chat and POST /v2/role/{role}/chat/stream.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
# POST /v2/role/{role}/chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_success(client: AsyncClient) -> None:
    """Non-streaming role chat should return ok with response."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director", "qa", "chief_engineer"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response",
            new_callable=AsyncMock,
            return_value={
                "response": "Hello from PM",
                "thinking": "thinking...",
                "role": "pm",
                "model": "gpt-4",
                "provider": "openai",
            },
        ) as mock_generate,
    ):
        response = await client.post("/v2/role/pm/chat", json={"message": "hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["response"] == "Hello from PM"
        assert data["role"] == "pm"
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_role_chat_empty_message(client: AsyncClient) -> None:
    """Empty message should return 400 INVALID_REQUEST."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
    ):
        response = await client.post("/v2/role/pm/chat", json={"message": ""})
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"
        assert "message is required" in data["error"]["message"]


@pytest.mark.asyncio
async def test_role_chat_unsupported_role(client: AsyncClient) -> None:
    """Unsupported role should return 400 UNSUPPORTED_ROLE."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect"],
        ),
    ):
        response = await client.post("/v2/role/unknown/chat", json={"message": "hello"})
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "UNSUPPORTED_ROLE"
        assert "unknown" in data["error"]["message"]


@pytest.mark.asyncio
async def test_role_chat_generation_error(client: AsyncClient) -> None:
    """Generation failure should return 500 GENERATION_FAILED."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response",
            new_callable=AsyncMock,
            side_effect=RuntimeError("model timeout"),
        ),
    ):
        response = await client.post("/v2/role/pm/chat", json={"message": "hello"})
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "GENERATION_FAILED"
        assert "model timeout" in data["error"]["message"]


@pytest.mark.asyncio
async def test_role_chat_architect_success(client: AsyncClient) -> None:
    """Architect role chat should work."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director", "qa", "chief_engineer"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response",
            new_callable=AsyncMock,
            return_value={
                "response": "Design proposal",
                "role": "architect",
                "model": "claude-3",
                "provider": "anthropic",
            },
        ),
    ):
        response = await client.post("/v2/role/architect/chat", json={"message": "design this"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["response"] == "Design proposal"
        assert data["role"] == "architect"


@pytest.mark.asyncio
async def test_role_chat_with_context(client: AsyncClient) -> None:
    """Role chat should pass context to generate_role_response."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response",
            new_callable=AsyncMock,
            return_value={"response": "ok", "role": "pm", "model": "x", "provider": "y"},
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/role/pm/chat",
            json={"message": "hello", "context": {"session_id": "abc123"}},
        )
        assert response.status_code == 200
        assert mock_generate.await_args is not None
        call_kwargs = mock_generate.await_args.kwargs
        assert call_kwargs.get("context") == {"session_id": "abc123"}


# ---------------------------------------------------------------------------
# POST /v2/role/{role}/chat/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_stream_success(client: AsyncClient) -> None:
    """Streaming role chat should return SSE response headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


@pytest.mark.asyncio
async def test_role_chat_stream_empty_message(client: AsyncClient) -> None:
    """Empty message on stream should return SSE error event."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
    ):
        response = await client.post(
            "/v2/role/pm/chat/stream",
            json={"message": ""},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: error" in body
        assert "message is required" in body


@pytest.mark.asyncio
async def test_role_chat_stream_unsupported_role(client: AsyncClient) -> None:
    """Unsupported role on stream should return 400 (raised before SSE starts)."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
    ):
        response = await client.post(
            "/v2/role/unknown/chat/stream",
            json={"message": "hello"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "UNSUPPORTED_ROLE"
        assert "unknown" in data["error"]["message"]


@pytest.mark.asyncio
async def test_role_chat_stream_llm_not_ready(client: AsyncClient) -> None:
    """LLM not ready on stream should return SSE error event."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
            side_effect=StructuredHTTPException(
                status_code=409,
                code="RUNTIME_ROLES_NOT_READY",
                message="PM LLM not ready",
            ),
        ),
    ):
        response = await client.post(
            "/v2/role/pm/chat/stream",
            json={"message": "hello"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        body = response.text
        assert "event: error" in body
        assert "PM LLM not ready" in body


@pytest.mark.asyncio
async def test_role_chat_stream_architect(client: AsyncClient) -> None:
    """Architect streaming role chat should return SSE response headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")
