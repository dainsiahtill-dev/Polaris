"""Tests for Polaris v2 Role Chat router.

Covers role chat endpoints: ping, status, roles list, llm-events,
cache-stats, and cache-clear. External services are mocked to avoid
LLM provider and storage dependencies.
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
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Role Chat Ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_ping(client: AsyncClient) -> None:
    """Role chat ping should return ok with supported roles."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm", "architect", "director", "qa", "chief_engineer"],
    ):
        response = await client.get("/v2/role/chat/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "pm" in data["supported_roles"]
        assert data["supported_roles"] == ["pm", "architect", "director", "qa", "chief_engineer"]


@pytest.mark.asyncio
async def test_role_chat_ping_empty_roles(client: AsyncClient) -> None:
    """Role chat ping should handle empty roles list gracefully."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=[],
    ):
        response = await client.get("/v2/role/chat/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["supported_roles"] == []


# ---------------------------------------------------------------------------
# Role Chat Status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_status_not_configured(client: AsyncClient) -> None:
    """Role chat status for unconfigured role should report not ready."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={"roles": {}, "providers": {}},
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert "PM role not configured" in data["error"]


@pytest.mark.asyncio
async def test_role_chat_status_configured(client: AsyncClient) -> None:
    """Role chat status for configured role should report ready."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"pm": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "openai", "model": "gpt-4", "profile": "default"},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["configured"] is True
        assert data["llm_test_ready"] is True
        assert data["role_config"]["provider_id"] == "openai"
        assert data["role_config"]["model"] == "gpt-4"
        assert data["provider_type"] == "openai"


@pytest.mark.asyncio
async def test_role_chat_status_missing_provider(client: AsyncClient) -> None:
    """Role chat status should report not ready when provider is missing."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "missing_provider", "model": "gpt-4"},
                },
                "providers": {},
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert "Provider 'missing_provider' not found" in data["error"]


@pytest.mark.asyncio
async def test_role_chat_status_missing_model(client: AsyncClient) -> None:
    """Role chat status should report not ready when model is empty."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "pm": {"provider_id": "openai", "model": ""},
                },
                "providers": {
                    "openai": {"type": "openai"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert "provider or model not set" in data["error"]


@pytest.mark.asyncio
async def test_role_chat_status_exception(client: AsyncClient) -> None:
    """Role chat status should handle exceptions gracefully."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            side_effect=RuntimeError("config load failed"),
        ),
    ):
        response = await client.get("/v2/role/pm/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False
        assert data["llm_test_ready"] is False
        assert "config load failed" in data["message"]


# ---------------------------------------------------------------------------
# List Supported Roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_supported_roles(client: AsyncClient) -> None:
    """List supported roles endpoint should return all roles."""
    with patch(
        "polaris.delivery.http.routers.role_chat.get_registered_roles",
        return_value=["pm", "architect", "director", "qa", "chief_engineer"],
    ):
        response = await client.get("/v2/role/chat/roles")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5
        assert "pm" in data["roles"]
        assert "chief_engineer" in data["roles"]


# ---------------------------------------------------------------------------
# Role Chat Status - Additional Roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_chat_status_architect(client: AsyncClient) -> None:
    """Role chat status for architect should work when configured."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"architect": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "anthropic", "model": "claude-3"},
                },
                "providers": {
                    "anthropic": {"type": "anthropic"},
                },
            },
        ),
    ):
        response = await client.get("/v2/role/architect/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["role_config"]["provider_id"] == "anthropic"


@pytest.mark.asyncio
async def test_role_chat_status_director_not_configured(client: AsyncClient) -> None:
    """Role chat status for director should report not ready when missing."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={
                "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                "providers": {"openai": {"type": "openai"}},
            },
        ),
    ):
        response = await client.get("/v2/role/director/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert "DIRECTOR role not configured" in data["error"]


@pytest.mark.asyncio
async def test_role_chat_status_with_test_index_only(client: AsyncClient) -> None:
    """Test index ready should not override missing config."""
    with (
        patch(
            "polaris.delivery.http.routers.role_chat.load_llm_test_index",
            return_value={"roles": {"qa": {"ready": True}}},
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.llm_config.load_llm_config",
            return_value={"roles": {}, "providers": {}},
        ),
    ):
        response = await client.get("/v2/role/qa/chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert data["configured"] is False


# ---------------------------------------------------------------------------
# Broken Import Endpoints (Production Bug Documentation)
# ---------------------------------------------------------------------------
# NOTE: role_chat.py imports from ..roles.events and ..roles.kernel_components
# which do not exist. These endpoints will fail at runtime with ModuleNotFoundError.


@pytest.mark.xfail(raises=ModuleNotFoundError, reason="Production bug: polaris.delivery.http.roles does not exist")
@pytest.mark.asyncio
async def test_role_llm_events_broken_import(client: AsyncClient) -> None:
    """Role LLM events endpoint has a production bug: missing module."""
    response = await client.get("/v2/role/pm/llm-events")
    assert response.status_code == 500


@pytest.mark.xfail(raises=ModuleNotFoundError, reason="Production bug: polaris.delivery.http.roles does not exist")
@pytest.mark.asyncio
async def test_role_all_llm_events_broken_import(client: AsyncClient) -> None:
    """All LLM events endpoint has a production bug: missing module."""
    response = await client.get("/v2/role/llm-events")
    assert response.status_code == 500


@pytest.mark.xfail(raises=ModuleNotFoundError, reason="Production bug: polaris.delivery.http.roles does not exist")
@pytest.mark.asyncio
async def test_role_cache_stats_broken_import(client: AsyncClient) -> None:
    """Role cache-stats endpoint has a production bug: missing module."""
    response = await client.get("/v2/role/cache-stats")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_role_cache_clear_rejects_forged_admin_role(client: AsyncClient) -> None:
    """Cache clear must be denied before role headers can influence RBAC."""
    response = await client.post("/v2/role/cache-clear", headers={"X-User-Role": "admin"})
    assert response.status_code == 403
    assert response.json()["detail"] == "role 'viewer' not authorized for this resource"
