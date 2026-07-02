"""Contract tests for polaris.delivery.http.routers.runtime module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

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

    async def test_retired_runtime_clear_alias_route_is_not_registered(self) -> None:
        """POST /runtime/clear is retired; callers must use /v2/runtime/clear."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/runtime/clear",
                json={"scope": "all"},
            )

        assert response.status_code == 404

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

    async def test_retired_runtime_reset_tasks_alias_route_is_not_registered(self) -> None:
        """POST /runtime/reset-tasks is retired; callers must use /v2/runtime/reset/tasks."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/runtime/reset-tasks")

        assert response.status_code == 404


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
