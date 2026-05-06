"""Tests for Polaris v2 Ollama router.

Covers GET /v2/ollama/models and POST /v2/ollama/stop.
External services are mocked to avoid Ollama dependencies.
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
# GET /v2/ollama/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_ollama_models_success(client: AsyncClient) -> None:
    """GET /v2/ollama/models should return list of models."""
    with patch(
        "polaris.delivery.http.routers.ollama.list_ollama_models",
        return_value=["llama2", "mistral"],
    ) as mock_list:
        response = await client.get("/v2/ollama/models")
        assert response.status_code == 200
        data = response.json()
        assert data["models"] == ["llama2", "mistral"]
        mock_list.assert_called_once()


@pytest.mark.asyncio
async def test_v2_ollama_models_empty(client: AsyncClient) -> None:
    """GET /v2/ollama/models should handle empty model list."""
    with patch(
        "polaris.delivery.http.routers.ollama.list_ollama_models",
        return_value=[],
    ) as mock_list:
        response = await client.get("/v2/ollama/models")
        assert response.status_code == 200
        data = response.json()
        assert data["models"] == []
        mock_list.assert_called_once()


# ---------------------------------------------------------------------------
# POST /v2/ollama/stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_ollama_stop_success(client: AsyncClient) -> None:
    """POST /v2/ollama/stop should return stop confirmation."""
    with patch(
        "polaris.delivery.http.routers.ollama.ollama_stop",
        return_value={"stopped": True, "models": ["llama2"]},
    ) as mock_stop:
        response = await client.post("/v2/ollama/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["stopped"] is True
        assert data["models"] == ["llama2"]
        mock_stop.assert_called_once()


@pytest.mark.asyncio
async def test_v2_ollama_stop_failure(client: AsyncClient) -> None:
    """POST /v2/ollama/stop should handle stop failure gracefully."""
    with patch(
        "polaris.delivery.http.routers.ollama.ollama_stop",
        return_value={"stopped": False, "error": "not running"},
    ) as mock_stop:
        response = await client.post("/v2/ollama/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["stopped"] is False
        assert data["error"] == "not running"
        mock_stop.assert_called_once()
