"""Tests for Polaris v2 memos router.

Covers GET /v2/memos/list.
External services are mocked to avoid storage dependencies.
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
# GET /v2/memos/list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_memos_list_success(client: AsyncClient) -> None:
    """GET /v2/memos/list should return memos."""
    with patch(
        "polaris.delivery.http.routers.memos.list_memos",
        return_value={"memos": [{"id": "m1", "text": "memo1"}], "total": 1},
    ) as mock_list:
        response = await client.get("/v2/memos/list?limit=50")
        assert response.status_code == 200
        data = response.json()
        assert data["memos"] == [{"id": "m1", "text": "memo1"}]
        assert data["total"] == 1
        mock_list.assert_called_once_with(".", "", 50)


@pytest.mark.asyncio
async def test_v2_memos_list_default_limit(client: AsyncClient) -> None:
    """GET /v2/memos/list should use default limit when not provided."""
    with patch(
        "polaris.delivery.http.routers.memos.list_memos",
        return_value={"memos": [], "total": 0},
    ) as mock_list:
        response = await client.get("/v2/memos/list")
        assert response.status_code == 200
        mock_list.assert_called_once_with(".", "", 200)
