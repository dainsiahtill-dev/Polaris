"""Tests for Polaris unified role chat endpoints.

Covers POST /v2/role/{role}/chat and POST /v2/role/{role}/chat/stream.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
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
async def test_role_chat_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Non-streaming role chat should generate against workspace_path before workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"

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
        response = await client.post("/v2/role/pm/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert mock_generate.await_args is not None
    assert mock_generate.await_args.kwargs["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_role_chat_status_reads_test_index_from_active_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Role status should read config and test evidence from workspace_path."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"

    config_payload = {
        "providers": {"openai_compat": {"type": "openai_compat"}},
        "roles": {
            "pm": {
                "provider_id": "openai_compat",
                "model": "qwen3-max",
                "profile": "pm",
            },
        },
    }
    index_payload = {
        "roles": {
            "pm": {
                "ready": True,
                "provider_id": "openai_compat",
                "model": "qwen3-max",
            },
        },
    }

    with (
        patch(
            "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
            new_callable=AsyncMock,
            return_value=index_payload,
        ) as mock_index,
        patch(
            "polaris.delivery.http.routers.role_chat._load_llm_config_async",
            new_callable=AsyncMock,
            return_value=config_payload,
        ) as mock_config,
    ):
        response = await client.get("/v2/role/pm/chat/status")

    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["llm_test_ready"] is True
    mock_index.assert_awaited_once_with("C:/Temp/Product")
    assert mock_config.await_args is not None
    assert mock_config.await_args.args[0] == "C:/Temp/Product"


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
    """PM streaming role chat should return framed SSE chunks."""

    async def _fake_streaming_response(**kwargs: Any) -> None:
        output_queue = kwargs["output_queue"]
        await output_queue.put({"type": "thinking_chunk", "data": {"content": "plan"}})
        await output_queue.put({"type": "content_chunk", "data": {"content": "Hello"}})
        await output_queue.put({"type": "complete", "data": {"response": "Hello from PM"}})

    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director", "qa", "chief_engineer"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response_streaming",
            new_callable=AsyncMock,
            side_effect=_fake_streaming_response,
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/role/pm/chat/stream",
            json={"message": "hello", "context": {"source": "desktop"}},
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: thinking_chunk" in body
    assert '"content": "plan"' in body
    assert "event: content_chunk" in body
    assert '"content": "Hello"' in body
    assert "event: complete" in body
    assert '"response": "Hello from PM"' in body
    mock_generate.assert_awaited_once()
    assert mock_generate.await_args is not None
    assert mock_generate.await_args.kwargs["role"] == "pm"
    assert mock_generate.await_args.kwargs["context"] == {"source": "desktop"}


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
async def test_role_chat_stream_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Streaming role chat should generate against workspace_path before workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"

    async def _fake_streaming_response(**kwargs: Any) -> None:
        output_queue = kwargs["output_queue"]
        await output_queue.put({"type": "complete", "data": {"ok": True}})

    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response_streaming",
            new_callable=AsyncMock,
            side_effect=_fake_streaming_response,
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/role/pm/chat/stream",
            json={"message": "hello"},
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert "event: complete" in response.text
    assert mock_generate.await_args is not None
    assert mock_generate.await_args.kwargs["workspace"] == "C:/Temp/Product"


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
@pytest.mark.parametrize("role", ["director", "chief_engineer"])
async def test_role_chat_stream_engineering_roles(client: AsyncClient, role: str) -> None:
    """Director and Chief Engineer streaming role chat should return framed SSE chunks."""

    async def _fake_streaming_response(**kwargs: Any) -> None:
        output_queue = kwargs["output_queue"]
        await output_queue.put({"type": "content_chunk", "data": {"content": f"{role} chunk"}})
        await output_queue.put({"type": "complete", "data": {"response": f"{role} complete"}})

    with (
        patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director", "qa", "chief_engineer"],
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.ensure_required_roles_ready",
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.generate_role_response_streaming",
            new_callable=AsyncMock,
            side_effect=_fake_streaming_response,
        ) as mock_generate,
    ):
        response = await client.post(
            f"/v2/role/{role}/chat/stream",
            json={"message": f"{role} hello"},
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    body = response.text
    assert "event: content_chunk" in body
    assert f'"content": "{role} chunk"' in body
    assert "event: complete" in body
    assert f'"response": "{role} complete"' in body
    mock_generate.assert_awaited_once()
    assert mock_generate.await_args is not None
    assert mock_generate.await_args.kwargs["role"] == role
