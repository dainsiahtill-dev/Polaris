"""Tests for Polaris primary v2 endpoints.

Covers GET /v2/health, GET /v2/ready, GET /v2/live.
External services are mocked to avoid storage and runtime dependencies.
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
# GET /v2/health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_health_success(client: AsyncClient) -> None:
    """Health endpoint should return 200 with expected fields."""
    with (
        patch(
            "polaris.delivery.http.routers.system.get_lancedb_status",
            return_value={"ok": True, "python": "3.11"},
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        container = MagicMock()
        pm_service = MagicMock()
        pm_service.get_status.return_value = {"status": "idle", "running": False}
        director_service = MagicMock()
        director_service.get_status = AsyncMock(return_value={"status": "idle", "state": "idle"})

        async def resolve_async(cls):
            if cls.__name__ == "PMService":
                return pm_service
            if cls.__name__ == "DirectorService":
                return director_service
            return MagicMock()

        container.resolve_async = resolve_async
        mock_container.return_value = container

        response = await client.get("/v2/health")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["version"] == "0.1"
        assert "timestamp" in data
        assert data["lancedb_ok"] is True
        assert data["python"] == "3.11"
        assert "pm" in data
        assert "director" in data


# ---------------------------------------------------------------------------
# GET /v2/ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_ready_true(client: AsyncClient) -> None:
    """Ready probe should return ready=True when lancedb is ok."""
    with patch(
        "polaris.delivery.http.routers.system.get_lancedb_status",
        return_value={"ok": True},
    ):
        response = await client.get("/v2/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True


# ---------------------------------------------------------------------------
# GET /v2/live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_live(client: AsyncClient) -> None:
    """Live probe should always return live=True."""
    response = await client.get("/v2/live")
    assert response.status_code == 200
    data = response.json()
    assert data["live"] is True
