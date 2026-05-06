"""Tests for Polaris system v2 endpoints.

Covers GET /v2/health, GET /v2/settings, POST /v2/settings,
GET /v2/ready, GET /v2/live, GET /v2/state/snapshot, POST /v2/app/shutdown.
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
    settings.to_payload.return_value = {
        "workspace": ".",
        "ramdisk_root": "",
        "model": "gpt-4",
        "pm_backend": "auto",
        "pm_model": None,
        "director_model": None,
        "pm_show_output": False,
        "pm_runs_director": False,
        "pm_director_show_output": False,
        "pm_director_timeout": 0,
        "pm_director_iterations": 1,
        "pm_director_match_mode": "run_id",
        "pm_agents_approval_mode": "auto_accept",
        "pm_agents_approval_timeout": 30,
        "pm_max_failures": 3,
        "pm_max_blocked": 3,
        "pm_max_same": 3,
        "pm_blocked_strategy": "auto",
        "pm_blocked_degrade_max_retries": 0,
        "director_iterations": 1,
        "director_execution_mode": "parallel",
        "director_max_parallel_tasks": 4,
        "director_ready_timeout_seconds": 30,
        "director_claim_timeout_seconds": 30,
        "director_phase_timeout_seconds": 60,
        "director_complete_timeout_seconds": 300,
        "director_task_timeout_seconds": 300,
        "director_forever": False,
        "director_show_output": False,
        "audit_llm_enabled": True,
        "audit_llm_role": "qa",
        "audit_llm_timeout": 180,
        "audit_llm_prefer_local_ollama": True,
        "audit_llm_allow_remote_fallback": True,
        "debug_tracing": False,
        "nats_enabled": False,
        "nats_required": False,
        "nats_url": "",
        "nats_stream_name": "",
        "json_log_path": None,
    }
    settings.apply_update = MagicMock()
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


@pytest.mark.asyncio
async def test_v2_health_lancedb_failure(client: AsyncClient) -> None:
    """Health endpoint should handle lancedb failure gracefully."""
    with (
        patch(
            "polaris.delivery.http.routers.system.get_lancedb_status",
            return_value={"ok": False, "error": "lancedb down"},
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
        assert data["ok"] is False
        assert data["lancedb_error"] == "lancedb down"


# ---------------------------------------------------------------------------
# GET /v2/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_settings(client: AsyncClient, mock_settings: Settings) -> None:
    """Settings endpoint should return current settings payload."""
    response = await client.get("/v2/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "."
    assert data["model"] == "gpt-4"
    mock_settings.to_payload.assert_called()


# ---------------------------------------------------------------------------
# POST /v2/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_update_settings(client: AsyncClient, mock_settings: Settings) -> None:
    """Update settings should apply changes and return updated payload."""
    with (
        patch(
            "polaris.delivery.http.routers.system.validate_workspace",
            return_value="/new/workspace",
        ),
        patch(
            "polaris.delivery.http.routers.system.workspace_has_docs",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.system.clear_workspace_status",
        ),
        patch(
            "polaris.delivery.http.routers.system.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.system.set_debug_tracing_enabled",
        ),
        patch(
            "polaris.delivery.http.routers.system.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.system.rebind_director_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.events.typed.get_default_adapter",
            return_value=None,
        ),
    ):
        container = MagicMock()
        pm_service = MagicMock()
        pm_service.get_status.return_value = {"running": False}
        pm_service.refresh_storage_layout = MagicMock()
        director_service = MagicMock()
        director_service.get_status = AsyncMock(return_value={"state": "idle"})

        async def resolve_async(cls):
            if cls.__name__ == "PMService":
                return pm_service
            if cls.__name__ == "DirectorService":
                return director_service
            return MagicMock()

        container.resolve_async = resolve_async
        container.resolve = MagicMock(return_value=None)
        mock_container.return_value = container

        response = await client.post(
            "/v2/settings",
            json={"workspace": "/new/workspace"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["workspace"] == "."
        mock_settings.apply_update.assert_called_once()


@pytest.mark.asyncio
async def test_v2_update_settings_pm_running_conflict(client: AsyncClient) -> None:
    """Update settings with workspace change while PM running should return 409."""
    with (
        patch(
            "polaris.delivery.http.routers.system.validate_workspace",
            return_value="/new/workspace",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        container = MagicMock()
        pm_service = MagicMock()
        pm_service.get_status.return_value = {"running": True}

        async def resolve_async(cls):
            if cls.__name__ == "PMService":
                return pm_service
            return MagicMock()

        container.resolve_async = resolve_async
        mock_container.return_value = container

        response = await client.post(
            "/v2/settings",
            json={"workspace": "/new/workspace"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "PM_RUNNING"


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


@pytest.mark.asyncio
async def test_v2_ready_false(client: AsyncClient) -> None:
    """Ready probe should return ready=False when lancedb is down."""
    with patch(
        "polaris.delivery.http.routers.system.get_lancedb_status",
        return_value={"ok": False},
    ):
        response = await client.get("/v2/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False


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


# ---------------------------------------------------------------------------
# GET /v2/state/snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_state_snapshot(client: AsyncClient) -> None:
    """State snapshot should return snapshot data."""
    with (
        patch(
            "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            return_value=MagicMock(workspace=".", runtime_root="/tmp/runtime"),
        ),
        patch(
            "polaris.delivery.http.routers.system.build_snapshot",
            return_value={"ok": True, "workspace": ".", "cache_root": "/tmp/runtime"},
        ),
    ):
        response = await client.get("/v2/state/snapshot")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["workspace"] == "."


# ---------------------------------------------------------------------------
# POST /v2/app/shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_app_shutdown(client: AsyncClient) -> None:
    """Shutdown endpoint should stop services and return termination status."""
    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.routers.system.terminate_external_loop_pm_processes",
            return_value=[1234],
        ),
        patch(
            "polaris.delivery.http.routers.system.clear_stop_flag",
        ) as mock_clear_stop,
        patch(
            "polaris.delivery.http.routers.system.clear_director_stop_flag",
        ) as mock_clear_director_stop,
    ):
        container = MagicMock()
        pm_service = MagicMock()
        pm_service.get_status.return_value = {"running": True}
        pm_service.stop = AsyncMock()
        director_service = MagicMock()
        director_service.get_status = AsyncMock(return_value={"state": "RUNNING"})
        director_service.stop = AsyncMock()

        async def resolve_async(cls):
            if cls.__name__ == "PMService":
                return pm_service
            if cls.__name__ == "DirectorService":
                return director_service
            return MagicMock()

        container.resolve_async = resolve_async
        mock_container.return_value = container

        response = await client.post("/v2/app/shutdown")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pm_running"] is True
        assert data["director_running"] is True
        assert data["pm_terminated"] is True
        assert data["director_terminated"] is True
        assert data["pm_external_terminated_pids"] == [1234]
        pm_service.stop.assert_awaited_once()
        director_service.stop.assert_awaited_once()
        mock_clear_stop.assert_called_once()
        mock_clear_director_stop.assert_called_once()


@pytest.mark.asyncio
async def test_v2_app_shutdown_no_services_running(client: AsyncClient) -> None:
    """Shutdown endpoint should handle case where no services are running."""
    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.routers.system.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.routers.system.clear_stop_flag",
        ),
        patch(
            "polaris.delivery.http.routers.system.clear_director_stop_flag",
        ),
    ):
        container = MagicMock()
        pm_service = MagicMock()
        pm_service.get_status.return_value = {"running": False}
        director_service = MagicMock()
        director_service.get_status = AsyncMock(return_value={"state": "idle"})

        async def resolve_async(cls):
            if cls.__name__ == "PMService":
                return pm_service
            if cls.__name__ == "DirectorService":
                return director_service
            return MagicMock()

        container.resolve_async = resolve_async
        mock_container.return_value = container

        response = await client.post("/v2/app/shutdown")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pm_running"] is False
        assert data["director_running"] is False
        assert data["pm_terminated"] is False
        assert data["director_terminated"] is False
