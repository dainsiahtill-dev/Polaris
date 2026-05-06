"""Contract tests for polaris.delivery.http.routers.runtime module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import runtime as runtime_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(runtime_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return app


@pytest.mark.asyncio
class TestRuntimeRouter:
    """Contract tests for the runtime router."""

    async def test_storage_layout_returns_200(self) -> None:
        """GET /runtime/storage-layout returns 200 with storage information."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            ) as mock_roots,
            patch(
                "polaris.delivery.http.routers.runtime.STORAGE_POLICY_REGISTRY",
                [],
            ),
            patch(
                "polaris.delivery.http.routers.runtime.resolve_global_path",
                return_value="/config/settings.json",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.resolve_workspace_persistent_path",
                return_value="/workspace/persistent",
            ),
        ):
            mock_roots.return_value = MagicMock(
                workspace_abs="/workspace",
                workspace_key="default",
                storage_layout_mode="v2",
                runtime_mode="active",
                home_root="/home",
                global_root="/global",
                projects_root="/projects",
                project_root="/project",
                config_root="/config",
                workspace_persistent_root="/workspace/.polaris",
                project_persistent_root="/project/.polaris",
                runtime_base="/runtime",
                runtime_root="/workspace/.polaris/runtime",
                runtime_project_root="/project/.polaris/runtime",
                history_root="/workspace/history",
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/runtime/storage-layout")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "workspace" in payload
        assert "workspace_abs" in payload
        assert "classification" in payload
        assert "policies" in payload
        assert "migration_version" in payload
        assert payload["migration_version"] == 2

    async def test_runtime_clear_all_scope(self) -> None:
        """POST /runtime/clear with scope=all returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={"cleared_files": 10, "cleared_dirs": 2},
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/runtime/clear",
                    json={"scope": "all"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["scope"] == "all"

    async def test_runtime_clear_pm_scope(self) -> None:
        """POST /runtime/clear with scope=pm returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={"cleared_files": 5},
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/runtime/clear",
                    json={"scope": "pm"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["scope"] == "pm"

    async def test_runtime_clear_director_scope(self) -> None:
        """POST /runtime/clear with scope=director returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={"cleared_files": 3},
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/runtime/clear",
                    json={"scope": "director"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["scope"] == "director"

    async def test_runtime_clear_dialogue_scope(self) -> None:
        """POST /runtime/clear with scope=dialogue returns 200."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.runtime.clear_runtime_scope",
            return_value={"cleared_files": 1},
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/runtime/clear",
                    json={"scope": "dialogue"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["scope"] == "dialogue"

    async def test_migration_status_v1(self) -> None:
        """GET /runtime/migration-status returns v1 status when no version file."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.runtime.resolve_storage_roots",
            ) as mock_roots,
            patch(
                "pathlib.Path.exists",
                return_value=False,
            ),
        ):
            mock_roots.return_value = MagicMock(
                workspace_persistent_root="/workspace/.polaris",
                history_root="/workspace/history",
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/runtime/migration-status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["version"] == 1
        assert payload["strict_mode"] is False

    async def test_reset_tasks_happy_path(self) -> None:
        """POST /runtime/reset-tasks returns 200 with reset status."""
        app = _build_app()
        with (
            patch(
                "polaris.infrastructure.di.container.get_container",
            ) as mock_get_container,
            patch(
                "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
                return_value=[],
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.reset_runtime_records",
                return_value={"records_reset": 5},
            ),
            patch(
                "polaris.delivery.http.routers.runtime.build_director_runtime_status",
                return_value={"running": False},
            ),
        ):
            mock_pm_service = MagicMock()
            mock_pm_service.get_status.return_value = {"running": False}
            mock_pm_service.stop = AsyncMock()

            mock_director_service = MagicMock()
            mock_director_service.get_status = AsyncMock(return_value={"state": "idle"})
            mock_director_service.stop = AsyncMock()

            mock_container = MagicMock()
            mock_container.resolve = MagicMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            mock_container.resolve_async = AsyncMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            mock_get_container.return_value = mock_container

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/runtime/reset-tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "pm_running" in payload
        assert "director_running" in payload

    async def test_reset_tasks_with_running_pm(self) -> None:
        """POST /runtime/reset-tasks stops running PM and returns status."""
        app = _build_app()
        with (
            patch(
                "polaris.infrastructure.di.container.get_container",
            ) as mock_get_container,
            patch(
                "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
                return_value=[],
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.reset_runtime_records",
                return_value={"records_reset": 0},
            ),
            patch(
                "polaris.delivery.http.routers.runtime.build_director_runtime_status",
                return_value={"running": False},
            ),
        ):
            mock_pm_service = MagicMock()
            mock_pm_service.get_status.return_value = {"running": True}
            mock_pm_service.stop = AsyncMock()

            mock_director_service = MagicMock()
            mock_director_service.get_status = AsyncMock(return_value={"state": "idle"})
            mock_director_service.stop = AsyncMock()

            mock_container = MagicMock()
            mock_container.resolve = MagicMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            mock_container.resolve_async = AsyncMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            mock_get_container.return_value = mock_container

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/runtime/reset-tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["pm_running"] is True
        # Verify PM stop was called
        mock_pm_service.stop.assert_called_once()

    async def test_reset_tasks_graceful_degradation(self) -> None:
        """POST /runtime/reset-tasks handles service unavailability gracefully."""
        app = _build_app()
        with (
            patch(
                "polaris.infrastructure.di.container.get_container",
                side_effect=RuntimeError("Container unavailable"),
            ),
            patch(
                "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
                return_value=[],
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.clear_director_stop_flag",
            ),
            patch(
                "polaris.delivery.http.routers.runtime.reset_runtime_records",
                return_value={"records_reset": 0},
            ),
            patch(
                "polaris.delivery.http.routers.runtime.build_director_runtime_status",
                return_value={"running": False},
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/runtime/reset-tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True


class TestStorageClassification:
    """Unit tests for _STORAGE_CLASSIFICATION constant."""

    def test_storage_classification_has_required_keys(self) -> None:
        """_STORAGE_CLASSIFICATION contains all required storage categories."""
        from polaris.delivery.http.routers.runtime import _STORAGE_CLASSIFICATION

        required_categories = [
            "global_config",
            "workspace_persistent",
            "runtime_current",
            "runtime_run",
            "workspace_history",
        ]
        for category in required_categories:
            assert category in _STORAGE_CLASSIFICATION
            assert "description" in _STORAGE_CLASSIFICATION[category]
            assert "lifecycle" in _STORAGE_CLASSIFICATION[category]

    def test_storage_classification_descriptions_are_non_empty(self) -> None:
        """All storage classifications have non-empty descriptions."""
        from polaris.delivery.http.routers.runtime import _STORAGE_CLASSIFICATION

        for category, details in _STORAGE_CLASSIFICATION.items():
            assert details["description"], f"Category {category} has empty description"
