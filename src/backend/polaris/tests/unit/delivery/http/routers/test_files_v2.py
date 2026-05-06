"""Tests for Polaris files v2 endpoints.

Covers GET /v2/files/read.
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
# GET /v2/files/read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_files_read_success(client: AsyncClient) -> None:
    """Reading an existing file should return file metadata and content."""
    with (
        patch(
            "polaris.delivery.http.routers.files.build_cache_root",
            return_value="/tmp/cache",
        ),
        patch(
            "polaris.delivery.http.routers.files.resolve_safe_path",
            return_value="/workspace/test.txt",
        ),
        patch(
            "polaris.delivery.http.routers.files.read_file_tail",
            return_value="hello world",
        ),
        patch(
            "polaris.delivery.http.routers.files.format_mtime",
            return_value="2024-01-01T00:00:00Z",
        ),
    ):
        response = await client.get("/v2/files/read", params={"path": "test.txt"})
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "/workspace/test.txt"
        assert data["rel_path"] == "test.txt"
        assert data["mtime"] == "2024-01-01T00:00:00Z"
        assert data["content"] == "hello world"


@pytest.mark.asyncio
async def test_v2_files_read_not_found(client: AsyncClient) -> None:
    """Reading a missing file should return 404 when read_file_tail raises."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with (
        patch(
            "polaris.delivery.http.routers.files.build_cache_root",
            return_value="/tmp/cache",
        ),
        patch(
            "polaris.delivery.http.routers.files.resolve_safe_path",
            return_value="/workspace/missing.txt",
        ),
        patch(
            "polaris.delivery.http.routers.files.read_file_tail",
            side_effect=StructuredHTTPException(
                status_code=404,
                code="FILE_NOT_FOUND",
                message="file not found",
            ),
        ),
    ):
        response = await client.get("/v2/files/read", params={"path": "missing.txt"})
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "FILE_NOT_FOUND"
