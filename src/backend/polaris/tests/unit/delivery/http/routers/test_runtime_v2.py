"""Tests for Polaris runtime v2 endpoints.

Covers GET /v2/runtime/storage/layout, POST /v2/runtime/clear,
GET /v2/runtime/migration/status, and POST /v2/runtime/reset/tasks.
External services are mocked to avoid storage and runtime dependencies.

Fixture contract
----------------
* This module reuses the shared router fixtures in this package's
  ``conftest.py``: ``mock_settings`` is a REAL, JSON-serialisable
  :class:`Settings` instance bound to a project-private workspace, and
  ``client`` builds the real application through ``create_app``.
* A local ``MagicMock(spec=Settings)`` cannot be used here: ``create_app``
  really constructs the ``EngineeringRunService`` owner, and that owner
  serialises the settings snapshot into the workspace LLM config, so a mock
  fails closed with ``TypeError: Object of type MagicMock is not JSON
  serializable`` before any router assertion runs.
* The shared fixture also keeps the engineering owner away from the Polaris
  main repository: the private workspace is created under the pytest
  basetemp, and the global KernelOne config root is redirected to a private
  KernelOne home.
* No fake Pydantic response models are installed into
  ``polaris.delivery.http.schemas.common``.  The six conversation/message
  response models that this module used to inject already exist as real
  production classes, so the injection was dead weight that permanently
  replaced the public schema classes for every later test in the same
  process.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.tests.unit.delivery.http.routers.conftest import _DirectorRouterTestSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bind_windows_ramdisk(settings: _DirectorRouterTestSettings, literal: str) -> None:
    """Bind a Windows-style ramdisk literal without cwd resolution.

    ``Settings.ramdisk_root`` resolves its argument against the process
    working directory, which would rewrite ``"C:/Ram"`` into an absolute
    POSIX path and destroy the literal the runtime router contract asserts.
    The underlying runtime field keeps the literal verbatim.
    """
    settings.runtime.ramdisk_root = Path(literal)


def _make_mock_storage_roots() -> MagicMock:
    """Build storage roots projection used by runtime router tests."""
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
    return mock_roots


# ---------------------------------------------------------------------------
# GET /v2/runtime/storage/layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_storage_layout_success(client: AsyncClient) -> None:
    """Storage layout endpoint should return 200 with expected fields."""
    mock_roots = _make_mock_storage_roots()

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


@pytest.mark.asyncio
async def test_v2_runtime_storage_layout_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Storage layout should inspect the active desktop workspace_path first."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_roots = _make_mock_storage_roots()

    with (
        patch(
            "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            return_value=mock_roots,
        ) as mock_resolve_roots,
        patch(
            "polaris.delivery.http.routers.runtime.resolve_global_path",
            side_effect=lambda p: f"/home/global/{p}",
        ),
        patch(
            "polaris.delivery.http.routers.runtime.resolve_workspace_persistent_path",
            side_effect=lambda workspace, path: f"{workspace}/.polaris/{path}",
        ) as mock_resolve_workspace_path,
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
    mock_resolve_roots.assert_called_once_with("C:/Temp/Product", ramdisk_root=None)
    mock_resolve_workspace_path.assert_any_call("C:/Temp/Product", "workspace/brain")


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


@pytest.mark.asyncio
async def test_v2_runtime_clear_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Runtime clear should clear active workspace runtime state."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    _bind_windows_ramdisk(mock_settings, "C:/Ram")

    with (
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="C:/Ram/runtime",
        ) as mock_cache_root,
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
        response = await client.post("/v2/runtime/clear", json={"scope": "pm"})

    assert response.status_code == 200
    mock_cache_root.assert_called_once_with("C:/Ram", "C:/Temp/Product")
    mock_clear.assert_called_once_with("C:/Temp/Product", "C:/Ram/runtime", "pm")


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


@pytest.mark.asyncio
async def test_v2_runtime_migration_status_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Migration status should read active workspace storage roots."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_roots = MagicMock()
    mock_roots.workspace_persistent_root = "/workspace/.polaris"
    mock_roots.history_root = "/workspace/.polaris/history"

    with patch(
        "polaris.delivery.http.routers.runtime.resolve_storage_roots",
        return_value=mock_roots,
    ) as mock_resolve_roots:
        response = await client.get("/v2/runtime/migration/status")

    assert response.status_code == 200
    mock_resolve_roots.assert_called_once_with("C:/Temp/Product")


# ---------------------------------------------------------------------------
# POST /v2/runtime/reset/tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_success(client: AsyncClient) -> None:
    """Reset tasks endpoint should stop services and reset runtime records."""
    pm_service = MagicMock()
    pm_service.get_status.return_value = {"running": True}
    pm_service.stop = AsyncMock()

    director_service = MagicMock()
    director_service.get_status = AsyncMock(return_value={"state": "RUNNING"})
    director_service.stop = AsyncMock()

    container = MagicMock()

    def resolve_side_effect(cls: type) -> MagicMock:
        if cls.__name__ == "PMService":
            return pm_service
        if cls.__name__ == "DirectorService":
            return director_service
        return MagicMock()

    container.resolve = resolve_side_effect

    async def _mock_get_container() -> MagicMock:
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
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_task_records",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ) as mock_task_reset,
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
        mock_task_reset.assert_called_once()


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_no_services_running(client: AsyncClient) -> None:
    """Reset tasks should handle case where no services are running."""
    pm_service = MagicMock()
    pm_service.get_status.return_value = {"running": False}

    director_service = MagicMock()
    director_service.get_status = AsyncMock(return_value={"state": "IDLE"})

    container = MagicMock()

    def resolve_side_effect(cls: type) -> MagicMock:
        if cls.__name__ == "PMService":
            return pm_service
        if cls.__name__ == "DirectorService":
            return director_service
        return MagicMock()

    container.resolve = resolve_side_effect

    async def _mock_get_container() -> MagicMock:
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
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_task_records",
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
async def test_v2_runtime_reset_tasks_can_preserve_planning_contracts(client: AsyncClient) -> None:
    """Reset tasks can clear taskboard state without deleting PM planning contracts."""
    pm_service = MagicMock()
    pm_service.get_status.return_value = {"running": False}

    director_service = MagicMock()
    director_service.get_status = AsyncMock(return_value={"state": "IDLE"})

    container = MagicMock()

    def resolve_side_effect(cls: type) -> MagicMock:
        if cls.__name__ == "PMService":
            return pm_service
        if cls.__name__ == "DirectorService":
            return director_service
        return MagicMock()

    container.resolve = resolve_side_effect

    async def _mock_get_container() -> MagicMock:
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
                "cleared_paths": ["/cache/runtime/contracts/pm_tasks.contract.json"],
                "failed_paths": [],
                "cleared_count": 1,
                "failed_count": 0,
            },
        ) as mock_reset,
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_task_records",
            return_value={
                "cleared_paths": ["/cache/runtime/tasks/task_1.json"],
                "failed_paths": [],
                "cleared_count": 1,
                "failed_count": 0,
            },
        ) as mock_task_reset,
    ):
        response = await client.post(
            "/v2/runtime/reset/tasks",
            json={"preserve_planning_contracts": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["preserve_planning_contracts"] is True
    assert data["state_reset"]["preserved_planning_contracts"] is True
    mock_reset.assert_not_called()
    mock_task_reset.assert_called_once()


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_services_not_found(client: AsyncClient) -> None:
    """Reset tasks should handle missing services gracefully."""
    container = MagicMock()
    container.resolve = MagicMock(side_effect=RuntimeError("Service not found"))

    async def _mock_get_container() -> MagicMock:
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
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_task_records",
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


@pytest.mark.asyncio
async def test_v2_runtime_reset_tasks_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Runtime task reset should clear stop flags and records for workspace_path."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    _bind_windows_ramdisk(mock_settings, "C:/Ram")

    container = MagicMock()
    container.resolve = MagicMock(side_effect=RuntimeError("Service not found"))

    async def _mock_get_container() -> MagicMock:
        return container

    with (
        patch(
            "polaris.infrastructure.di.container.get_container",
            new=_mock_get_container,
        ),
        patch(
            "polaris.delivery.http.routers.runtime.build_cache_root",
            return_value="C:/Ram/runtime",
        ) as mock_cache_root,
        patch(
            "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
            return_value=[],
        ) as mock_terminate_pm,
        patch(
            "polaris.delivery.http.routers.runtime.build_director_runtime_status",
            return_value={"running": False, "pid": None},
        ) as mock_director_status,
        patch(
            "polaris.delivery.http.routers.runtime.clear_stop_flag",
        ) as mock_clear_stop,
        patch(
            "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
        ) as mock_clear_director_stop,
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_records",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ) as mock_reset,
        patch(
            "polaris.delivery.http.routers.runtime.reset_runtime_task_records",
            return_value={
                "cleared_paths": [],
                "failed_paths": [],
                "cleared_count": 0,
                "failed_count": 0,
            },
        ) as mock_task_reset,
    ):
        response = await client.post("/v2/runtime/reset/tasks")

    assert response.status_code == 200
    mock_cache_root.assert_called_once_with("C:/Ram", "C:/Temp/Product")
    mock_terminate_pm.assert_called_once_with("C:/Temp/Product")
    assert mock_director_status.call_args.args[1:] == ("C:/Temp/Product", "C:/Ram/runtime")
    mock_clear_stop.assert_called_once_with("C:/Temp/Product")
    mock_clear_director_stop.assert_called_once_with("C:/Temp/Product")
    mock_reset.assert_called_once_with("C:/Temp/Product", "C:/Ram/runtime")
    mock_task_reset.assert_called_once_with("C:/Temp/Product")
