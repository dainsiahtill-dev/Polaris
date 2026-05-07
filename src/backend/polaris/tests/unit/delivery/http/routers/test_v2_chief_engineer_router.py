"""Tests for Polaris v2 Chief Engineer router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings


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
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.cells.runtime.state_owner.public.service import AppState
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.app_state = AppState(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch("polaris.bootstrap.assembly.assemble_core_services"),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch("polaris.delivery.http.app_factory.sync_process_settings_environment"),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_list_chief_engineer_blueprints(client: AsyncClient) -> None:
    """Chief Engineer blueprints list should expose persisted blueprint summaries."""
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-1"]
    persistence.load.return_value = {
        "blueprint_id": "bp-1",
        "title": "Director TaskBoard",
        "summary": "Build real task board",
        "target_files": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
        "updated_at": "2026-05-07T07:16:25Z",
    }

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ):
        response = await client.get("/v2/chief-engineer/blueprints")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["blueprints"][0]["blueprint_id"] == "bp-1"
    assert data["blueprints"][0]["title"] == "Director TaskBoard"
    assert data["blueprints"][0]["target_files"] == ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"]
    assert data["blueprints"][0]["source"] == "runtime/blueprints"


@pytest.mark.asyncio
async def test_get_chief_engineer_blueprint_rejects_invalid_id(client: AsyncClient) -> None:
    """Blueprint detail endpoint should reject unsafe ids before touching persistence."""
    with patch("polaris.delivery.http.v2.chief_engineer.BlueprintPersistence") as persistence_cls:
        response = await client.get("/v2/chief-engineer/blueprints/bad$id")

    assert response.status_code == 400
    persistence_cls.assert_not_called()
