"""Tests for Polaris runtime v2 endpoints.

Covers GET /v2/runtime/storage/layout, POST /v2/runtime/clear,
GET /v2/runtime/migration/status, and POST /v2/runtime/reset/tasks.
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
    # Pre-patch broken imports before create_app triggers router imports.
    # conversations.py imports response models that don't exist in common.py yet.
    # We inject minimal Pydantic models so FastAPI route registration succeeds.
    import polaris.delivery.http.schemas.common as _common_mod
    from pydantic import BaseModel

    class _ConversationListResponse(BaseModel):
        conversations: list = []
        total: int = 0

    class _ConversationResponse(BaseModel):
        model_config = {"extra": "allow"}

    class _ConversationDeleteResponse(BaseModel):
        ok: bool = True
        deleted_id: str = ""

    class _MessageResponse(BaseModel):
        model_config = {"extra": "allow"}

    class _MessageBatchResponse(BaseModel):
        ok: bool = True
        added_count: int = 0

    class _MessageDeleteResponse(BaseModel):
        ok: bool = True
        deleted_id: str = ""

    _common_mod.ConversationListResponse = _ConversationListResponse  # type: ignore[attr-defined]
    _common_mod.ConversationResponse = _ConversationResponse  # type: ignore[attr-defined]
    _common_mod.ConversationDeleteResponse = _ConversationDeleteResponse  # type: ignore[attr-defined]
    _common_mod.MessageResponse = _MessageResponse  # type: ignore[attr-defined]
    _common_mod.MessageBatchResponse = _MessageBatchResponse  # type: ignore[attr-defined]
    _common_mod.MessageDeleteResponse = _MessageDeleteResponse  # type: ignore[attr-defined]

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
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# GET /v2/runtime/storage/layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_storage_layout_success(client: AsyncClient) -> None:
    """Storage layout endpoint should return 200 with expected fields."""
    mock_roots = MagicMock()
    mock_roots.workspace_abs = "/workspace"
    mock_roots.workspace_key = "workspace-abc123"
    mock_roots.storage_layout_mode = "project_local"
    mock_roots.runtime_mode = "system_cache"
    mock_roots.home_root = "/home"
    mock_roots.global_root = "/home/global"
    mock_roots.projects_root = "/workspace/.polaris"
    mock_roots.project_root = "/workspace/.polaris"
    mock_roots.config_root = "/home/global/config"
    mock_roots.workspace_persistent_root = "/workspace/.polaris"
    mock_roots.project_persistent_root = "/workspace/.polaris"
    mock_roots.runtime_base = "/cache"
    mock_roots.runtime_root = "/cache/runtime"
    mock_roots.runtime_project_root = "/cache/runtime/workspace-abc123"
    mock_roots.history_root = "/workspace/.polaris/history"

    with (
        patch(
            "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            return_value=mock_roots,
        ),
        patch(
            "polaris.delivery.http.routers.runtime.resolve_global_path",
            side_effect=lambda p: f"/home/global/{p}",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.resolve_workspace_persistent_path",
            side_effect=lambda _w, p: f"/workspace/.polaris/{p}",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.polaris_home",
            return_value="/home",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.resolve_env_str",
            return_value="",
        ),
    ):
        response = await client.get("/v2/runtime/storage/layout")
        assert response.status_code == 200
        data = response.json()
        assert data["workspace"] == "/workspace"
        assert data["workspace_key"] == "workspace-abc123"
        assert data["storage_layout_mode"] == "project_local"
        assert data["runtime_mode"] == "system_cache"
        assert data["migration_version"] == 2
        assert "classification" in data
        assert "policies" in data
        assert "paths" in data
        assert "env" in data


# ---------------------------------------------------------------------------
# POST /v2/runtime/clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_clear_success(client: AsyncClient) -> None:
    """Clear endpoint should return 200 with cleared paths."""
    with (
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="/cache/runtime",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={
                "cleared_paths": ["/cache/runtime/logs/pm.process.log"],
                "failed_paths": [],
                "cleared_count": 1,
                "failed_count": 0,
            },
        ) as mock_clear,
    ):
        response = await client.post("/v2/runtime/clear", json={"scope": "pm"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["scope"] == "pm"
        assert data["cleared_count"] == 1
        mock_clear.assert_called_once()


@pytest.mark.asyncio
async def test_v2_runtime_clear_invalid_scope(client: AsyncClient) -> None:
    """Clear endpoint with invalid scope should return 422."""
    response = await client.post("/v2/runtime/clear", json={"scope": "invalid"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_v2_runtime_clear_default_scope(client: AsyncClient) -> None:
    """Clear endpoint without scope should default to 'all'."""
    with (
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="/cache/runtime",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ) as mock_clear,
    ):
        response = await client.post("/v2/runtime/clear", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["scope"] == "all"
        mock_clear.assert_called_once()
        call_args = mock_clear.call_args
        assert call_args[0][2] == "all"


# ---------------------------------------------------------------------------
# GET /v2/runtime/migration/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_migration_status_no_version_file(client: AsyncClient) -> None:
    """Migration status should return defaults when no version file exists."""
    mock_roots = MagicMock()
    mock_roots.workspace_persistent_root = "/workspace/.polaris"
    mock_roots.history_root = "/workspace/.polaris/history"

    with (
        patch(
            "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            return_value=mock_roots,
        ),
    ):
        response = await client.get("/v2/runtime/migration/status")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 1
        assert data["cutover_at"] is None
        assert data["backup_path"] == ""
        assert data["archived_counts"] == {"runs": 0, "tasks": 0, "factory": 0}
        assert data["strict_mode"] is False


@pytest.mark.asyncio
async def test_v2_runtime_migration_status_with_version_file(client: AsyncClient, tmp_path: Path) -> None:
    """Migration status should read version file when present."""
    workspace_root = tmp_path / "workspace" / ".polaris"
    meta_dir = workspace_root / "meta"
    meta_dir.mkdir(parents=True)
    version_file = meta_dir / "storage_layout.version.json"
    version_file.write_text(
        '{"version": 2, "cutover_at": "2024-01-01T00:00:00Z", "strict_mode": true}',
        encoding="utf-8",
    )

    # Create backup directory
    backup_dir = workspace_root / "protocol_backup_20240101"
    backup_dir.mkdir()

    # Create history directories
    history_root = workspace_root / "history"
    (history_root / "runs" / "run1").mkdir(parents=True)
    (history_root / "runs" / "run2").mkdir(parents=True)
    (history_root / "tasks" / "task1").mkdir(parents=True)

    mock_roots = MagicMock()
    mock_roots.workspace_persistent_root = str(workspace_root)
    mock_roots.history_root = str(history_root)

    with (
        patch(
            "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            return_value=mock_roots,
        ),
    ):
        response = await client.get("/v2/runtime/migration/status")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 2
        assert data["cutover_at"] == "2024-01-01T00:00:00Z"
        assert data["strict_mode"] is True
        assert data["archived_counts"] == {"runs": 2, "tasks": 1, "factory": 0}
        assert backup_dir.name in data["backup_path"]


# ---------------------------------------------------------------------------
# POST /v2/runtime/reset/tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_success(client: AsyncClient) -> None:
    """Reset tasks endpoint should stop services and reset runtime records."""
    pm_service = MagicMock()
    pm_service.get_status.return_value = {"running": True}
    pm_service.stop = MagicMock()

    director_service = MagicMock()
    director_service.get_status.return_value = {"state": "RUNNING"}
    director_service.stop = MagicMock()

    container = MagicMock()

    def resolve_side_effect(cls):
        if cls.__name__ == "PMService":
            return pm_service
        if cls.__name__ == "DirectorService":
            return director_service
        return MagicMock()

    container.resolve = resolve_side_effect

    def _mock_get_container():
        return container

    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new=_mock_get_container,
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="/cache/runtime",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
            return_value=[1234],
        ) as mock_terminate_pm,
        patch(
            "polaris.delivery.http.routers.runtime.build_director_runtime_status",
            return_value={"running": True, "pid": 5678},
        ),
        patch(
            "polaris.delivery.http.routers.runtime.terminate_pid",
            return_value=True,
        ) as mock_terminate_pid,
        patch(
            "polaris.delivery.http.routers.runtime.clear_stop_flag",
        ) as mock_clear_stop,
        patch(
            "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
        ) as mock_clear_director_stop,
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_records",
            return_value={
                "cleared_paths": ["/cache/runtime/state.json"],
                "failed_paths": [],
                "cleared_count": 1,
                "failed_count": 0,
            },
        ) as mock_reset,
    ):
        response = await client.post("/v2/runtime/reset/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pm_running"] is True
        assert data["pm_external_terminated_pids"] == [1234]
        assert data["director_running"] is True
        assert data["director_external_pid"] == 5678
        assert data["director_external_terminated"] is True
        assert data["cleared_count"] == 1
        pm_service.stop.assert_called_once()
        director_service.stop.assert_called_once()
        mock_terminate_pm.assert_called_once()
        mock_terminate_pid.assert_called_once_with(5678)
        mock_clear_stop.assert_called_once()
        mock_clear_director_stop.assert_called_once()
        mock_reset.assert_called_once()


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_no_services_running(client: AsyncClient) -> None:
    """Reset tasks should handle case where no services are running."""
    pm_service = MagicMock()
    pm_service.get_status.return_value = {"running": False}

    director_service = MagicMock()
    director_service.get_status.return_value = {"state": "IDLE"}

    container = MagicMock()

    def resolve_side_effect(cls):
        if cls.__name__ == "PMService":
            return pm_service
        if cls.__name__ == "DirectorService":
            return director_service
        return MagicMock()

    container.resolve = resolve_side_effect

    def _mock_get_container():
        return container

    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new=_mock_get_container,
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="/cache/runtime",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_director_runtime_status",
            return_value={"running": False, "pid": None},
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_stop_flag",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_records",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ),
    ):
        response = await client.post("/v2/runtime/reset/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pm_running"] is False
        assert data["director_running"] is False
        assert data["director_external_pid"] is None
        assert data["director_external_terminated"] is False


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_services_not_found(client: AsyncClient) -> None:
    """Reset tasks should handle missing services gracefully."""
    container = MagicMock()
    container.resolve = MagicMock(side_effect=RuntimeError("Service not found"))

    def _mock_get_container():
        return container

    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new=_mock_get_container,
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="/cache/runtime",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_director_runtime_status",
            return_value={"running": False, "pid": None},
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_stop_flag",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_records",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ),
    ):
        response = await client.post("/v2/runtime/reset/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["pm_running"] is False
        assert data["director_running"] is False
