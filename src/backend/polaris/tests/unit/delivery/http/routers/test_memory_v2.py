"""Tests for Polaris v2 memory router.

Covers GET /memory/v2/state and DELETE /memory/v2/memories/{memory_id}.
External services are mocked to avoid memory store dependencies.
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
# GET /memory/v2/state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_memory_state_success(client: AsyncClient) -> None:
    """GET /memory/v2/state should return memory state."""
    mock_mem_store = MagicMock()
    mock_mem_store.memories = [MagicMock(), MagicMock()]
    mock_mem_store.count_recent_errors.return_value = 1

    mock_ref_store = MagicMock()
    mock_ref_store.get_last_reflection_step.return_value = 5
    mock_ref_store.reflections = [MagicMock()]

    with (
        patch(
            "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_memory_store",
            return_value=mock_mem_store,
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_reflection_store",
            return_value=mock_ref_store,
        ),
    ):
        response = await client.get("/memory/v2/state")
        assert response.status_code == 200
        data = response.json()
        assert data["last_reflection_step"] == 5
        assert data["recent_error_count"] == 1
        assert data["total_memories"] == 2
        assert data["total_reflections"] == 1


@pytest.mark.asyncio
async def test_v2_memory_state_not_initialized(client: AsyncClient) -> None:
    """GET /memory/v2/state should 503 when memory store is not initialized."""
    with (
        patch(
            "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_memory_store",
            return_value=None,
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_reflection_store",
            return_value=None,
        ),
    ):
        response = await client.get("/memory/v2/state")
        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == "MEMORY_STORE_NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# DELETE /memory/v2/memories/{memory_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_memory_delete_success(client: AsyncClient) -> None:
    """DELETE /memory/v2/memories/{memory_id} should delete memory."""
    mock_mem_store = MagicMock()
    mock_mem_store.delete.return_value = True

    with (
        patch(
            "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_memory_store",
            return_value=mock_mem_store,
        ),
    ):
        response = await client.delete("/memory/v2/memories/mem-123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["id"] == "mem-123"
        mock_mem_store.delete.assert_called_once_with("mem-123")


@pytest.mark.asyncio
async def test_v2_memory_delete_not_found(client: AsyncClient) -> None:
    """DELETE /memory/v2/memories/{memory_id} should 404 when memory not found."""
    mock_mem_store = MagicMock()
    mock_mem_store.delete.return_value = False

    with (
        patch(
            "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_memory_store",
            return_value=mock_mem_store,
        ),
    ):
        response = await client.delete("/memory/v2/memories/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "MEMORY_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_memory_delete_store_not_initialized(client: AsyncClient) -> None:
    """DELETE /memory/v2/memories/{memory_id} should 503 when store not initialized."""
    with (
        patch(
            "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
        ),
        patch(
            "polaris.delivery.http.routers.memory.get_memory_store",
            return_value=None,
        ),
    ):
        response = await client.delete("/memory/v2/memories/mem-123")
        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == "MEMORY_STORE_NOT_INITIALIZED"
