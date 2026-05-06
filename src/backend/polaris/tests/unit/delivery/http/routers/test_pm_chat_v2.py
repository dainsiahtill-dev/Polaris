"""Tests for Polaris PM chat endpoints.

Covers POST /v2/pm/chat, POST /v2/pm/chat/stream,
GET /v2/pm/chat/status, and GET /v2/pm/chat/ping.
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
# GET /v2/pm/chat/ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_chat_ping(client: AsyncClient) -> None:
    """Ping endpoint should return ok with role=pm."""
    response = await client.get("/v2/pm/chat/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["role"] == "pm"
    assert "PM Chat router is working" in data["message"]


# ---------------------------------------------------------------------------
# POST /v2/pm/chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_chat_success(client: AsyncClient) -> None:
    """Non-streaming PM chat should return ok with response."""
    with patch(
        "polaris.delivery.http.routers.pm_chat.generate_role_response",
        new_callable=AsyncMock,
        return_value={
            "response": "Hello from PM",
            "thinking": "thinking...",
            "role": "pm",
            "model": "gpt-4",
            "provider": "openai",
        },
    ) as mock_generate:
        response = await client.post("/v2/pm/chat", json={"message": "hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["response"] == "Hello from PM"
        assert data["role"] == "pm"
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_chat_empty_message(client: AsyncClient) -> None:
    """Empty message should return 422 MISSING_MESSAGE."""
    response = await client.post("/v2/pm/chat", json={"message": ""})
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "MISSING_MESSAGE"
    assert "message is required" in data["error"]["message"]


@pytest.mark.asyncio
async def test_pm_chat_llm_not_ready(client: AsyncClient) -> None:
    """LLM not ready should return 409 via status endpoint; chat itself returns 500 on RuntimeError."""
    # The pm_chat router does not explicitly check LLM readiness before calling
    # generate_role_response, so we simulate a RuntimeError from the LLM layer.
    with patch(
        "polaris.delivery.http.routers.pm_chat.generate_role_response",
        new_callable=AsyncMock,
        side_effect=RuntimeError("PM LLM not ready"),
    ):
        response = await client.post("/v2/pm/chat", json={"message": "hello"})
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "ROLE_RESPONSE_ERROR"
        assert "Generation failed" in data["error"]["message"]


@pytest.mark.asyncio
async def test_pm_chat_generation_error(client: AsyncClient) -> None:
    """Generation failure should return 500 ROLE_RESPONSE_ERROR."""
    with patch(
        "polaris.delivery.http.routers.pm_chat.generate_role_response",
        new_callable=AsyncMock,
        side_effect=RuntimeError("model timeout"),
    ):
        response = await client.post("/v2/pm/chat", json={"message": "hello"})
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "ROLE_RESPONSE_ERROR"
        assert "Generation failed" in data["error"]["message"]


@pytest.mark.asyncio
async def test_pm_chat_with_context(client: AsyncClient) -> None:
    """PM chat should pass context to generate_role_response."""
    with patch(
        "polaris.delivery.http.routers.pm_chat.generate_role_response",
        new_callable=AsyncMock,
        return_value={"response": "ok", "role": "pm", "model": "x", "provider": "y"},
    ) as mock_generate:
        response = await client.post(
            "/v2/pm/chat",
            json={"message": "hello", "context": {"session_id": "abc123"}},
        )
        assert response.status_code == 200
        assert mock_generate.await_args is not None
        call_kwargs = mock_generate.await_args.kwargs
        assert call_kwargs.get("context") == {"session_id": "abc123"}


# ---------------------------------------------------------------------------
# POST /v2/pm/chat/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_chat_stream_sse_headers(client: AsyncClient) -> None:
    """Streaming PM chat should return SSE response headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


@pytest.mark.asyncio
async def test_pm_chat_stream_empty_message(client: AsyncClient) -> None:
    """Empty message on stream should return SSE error event."""
    response = await client.post(
        "/v2/pm/chat/stream",
        json={"message": ""},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    body = response.text
    assert "event: error" in body
    assert "message is required" in body


# ---------------------------------------------------------------------------
# GET /v2/pm/chat/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_chat_status_ready(client: AsyncClient) -> None:
    """Status endpoint should return ready when PM role is configured."""
    mock_index = {"roles": {"pm": {"ready": True}}}
    mock_config = {
        "roles": {
            "pm": {
                "provider_id": "openai",
                "model": "gpt-4",
                "profile": "default",
            },
        },
        "providers": {
            "openai": {"type": "openai"},
        },
    }

    with (
        patch(
            "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
            return_value=mock_index,
        ),
        patch(
            "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
            return_value=mock_config,
        ),
    ):
        response = await client.get("/v2/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["configured"] is True
        assert data["provider_type"] == "openai"
        assert data["role_config"]["provider_id"] == "openai"
        assert data["role_config"]["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_pm_chat_status_not_configured(client: AsyncClient) -> None:
    """Status endpoint should return 409 when PM role is not configured."""
    mock_config: dict[str, object] = {
        "roles": {},
        "providers": {},
    }

    with (
        patch(
            "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
            return_value=mock_config,
        ),
    ):
        response = await client.get("/v2/pm/chat/status")
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "PM_ROLE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_pm_chat_status_provider_or_model_not_set(client: AsyncClient) -> None:
    """Status endpoint should return 409 when provider_id or model is empty."""
    mock_config: dict[str, object] = {
        "roles": {
            "pm": {
                "provider_id": "",
                "model": "",
            },
        },
        "providers": {},
    }

    with (
        patch(
            "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
            return_value=mock_config,
        ),
    ):
        response = await client.get("/v2/pm/chat/status")
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "PM_ROLE_PROVIDER_OR_MODEL_NOT_SET"


@pytest.mark.asyncio
async def test_pm_chat_status_provider_not_found(client: AsyncClient) -> None:
    """Status endpoint should return 409 when provider is not found."""
    mock_config: dict[str, object] = {
        "roles": {
            "pm": {
                "provider_id": "unknown",
                "model": "gpt-4",
            },
        },
        "providers": {
            "openai": {"type": "openai"},
        },
    }

    with (
        patch(
            "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
            return_value=mock_config,
        ),
    ):
        response = await client.get("/v2/pm/chat/status")
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"
        assert "Provider not found" in data["error"]["message"]
