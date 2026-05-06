"""Tests for Polaris court v2 endpoints.

Covers GET /v2/court/topology, GET /v2/court/state, GET /v2/court/actors/{role_id},
GET /v2/court/scenes/{scene_id}, and GET /v2/court/mapping.
External services are mocked to avoid storage and runtime dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
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
    settings.workspace = Path(".")
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
# GET /v2/court/topology
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_court_topology(client: AsyncClient) -> None:
    """Topology endpoint should return nodes, count, total, and scenes."""
    response = await client.get("/v2/court/topology")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert isinstance(data["nodes"], list)
    assert "count" in data
    assert "total" in data
    assert "scenes" in data
    assert data["total"] == len(data["nodes"])
    assert data["count"] <= data["total"]


# ---------------------------------------------------------------------------
# GET /v2/court/state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_court_state_no_engine_status(client: AsyncClient) -> None:
    """State endpoint should return court state even when engine status is absent."""
    with (
        patch(
            "polaris.delivery.http.routers.court._get_engine_status",
            return_value=None,
        ),
        patch(
            "polaris.delivery.http.routers.court._get_pm_status",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_director_status",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        response = await client.get("/v2/court/state")
        assert response.status_code == 200
        data = response.json()
        assert "phase" in data
        assert "current_scene" in data
        assert "actors" in data
        assert "recent_events" in data
        assert "updated_at" in data


@pytest.mark.asyncio
async def test_v2_court_state_with_engine_status(client: AsyncClient) -> None:
    """State endpoint should map engine status to court state."""
    engine_status = {
        "phase": "building",
        "running": True,
        "roles": {
            "gongbu_shangshu": {"status": "running", "running": True},
        },
    }
    with (
        patch(
            "polaris.delivery.http.routers.court._get_engine_status",
            return_value=engine_status,
        ),
        patch(
            "polaris.delivery.http.routers.court._get_pm_status",
            new_callable=AsyncMock,
            return_value={"tasks": []},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_director_status",
            new_callable=AsyncMock,
            return_value={"state": "idle"},
        ),
    ):
        response = await client.get("/v2/court/state")
        assert response.status_code == 200
        data = response.json()
        assert data["phase"] == "build"
        assert "actors" in data
        assert data["current_scene"] == "construction_site"


# ---------------------------------------------------------------------------
# GET /v2/court/actors/{role_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_court_actor_found(client: AsyncClient) -> None:
    """Actor detail should return role info when role exists."""
    with (
        patch(
            "polaris.delivery.http.routers.court._get_engine_status",
            return_value=None,
        ),
        patch(
            "polaris.delivery.http.routers.court._get_pm_status",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_director_status",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        response = await client.get("/v2/court/actors/emperor")
        assert response.status_code == 200
        data = response.json()
        assert data["role_id"] == "emperor"
        assert "topology" in data


@pytest.mark.asyncio
async def test_v2_court_actor_not_found(client: AsyncClient) -> None:
    """Actor detail should return 404 when role does not exist."""
    with (
        patch(
            "polaris.delivery.http.routers.court._get_engine_status",
            return_value=None,
        ),
        patch(
            "polaris.delivery.http.routers.court._get_pm_status",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "polaris.delivery.http.routers.court._get_director_status",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        response = await client.get("/v2/court/actors/nonexistent_role")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "ROLE_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/court/scenes/{scene_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_court_scene_found(client: AsyncClient) -> None:
    """Scene detail should return scene config when scene exists."""
    response = await client.get("/v2/court/scenes/taiji_hall")
    assert response.status_code == 200
    data = response.json()
    assert data["scene_id"] == "taiji_hall"
    assert "scene_name" in data
    assert "camera_position" in data
    assert "focus_roles" in data
    assert "transitions" in data


@pytest.mark.asyncio
async def test_v2_court_scene_not_found(client: AsyncClient) -> None:
    """Scene detail should return 404 when scene does not exist."""
    response = await client.get("/v2/court/scenes/nonexistent_scene")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "SCENE_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/court/mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_court_mapping(client: AsyncClient) -> None:
    """Mapping endpoint should return tech_to_court, court_roles, and version."""
    response = await client.get("/v2/court/mapping")
    assert response.status_code == 200
    data = response.json()
    assert "tech_to_court" in data
    assert isinstance(data["tech_to_court"], dict)
    assert "court_roles" in data
    assert isinstance(data["court_roles"], list)
    assert "version" in data
    assert data["version"] == "1.0"
    assert "description" in data
