"""Contract tests for polaris.delivery.http.routers.system module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import system as system_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    app.state.app_state.settings.ramdisk_root = ""
    app.state.app_state.settings.to_payload.return_value = {"workspace": "."}
    return app


@pytest.mark.asyncio
class TestSystemRouter:
    """Contract tests for the system router."""

    async def test_health_check_returns_200(self) -> None:
        """GET /health returns 200 with health status."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.system.get_lancedb_status",
                return_value={"ok": True, "python": "3.12"},
            ),
            patch(
                "polaris.infrastructure.di.container.get_container",
            ) as mock_get_container,
        ):
            mock_pm_service = MagicMock()
            mock_pm_service.get_status.return_value = {"status": "idle", "running": False}
            mock_director_service = MagicMock()
            mock_director_service.get_status = AsyncMock(return_value={"status": "idle", "state": "idle"})
            mock_container = MagicMock()
            mock_container.resolve_async = AsyncMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            # get_container() returns a coroutine that resolves to mock_container
            # but code doesn't await it, so we need to return mock_container directly
            mock_get_container.return_value = mock_container

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/health")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["version"] == "0.1"
        assert "timestamp" in payload
        assert payload["lancedb_ok"] is True
        assert payload["pm"]["status"] == "idle"
        assert payload["director"]["status"] == "idle"

    async def test_settings_get_returns_200(self) -> None:
        """GET /settings returns 200 with settings."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/settings")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "workspace" in payload

    async def test_state_snapshot_returns_200(self) -> None:
        """GET /state/snapshot returns 200 with snapshot."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.system.resolve_workspace_runtime_context",
            ) as mock_ctx,
            patch(
                "polaris.delivery.http.routers.system.build_snapshot",
                return_value={"workspace": ".", "timestamp": "2026-01-01"},
            ),
        ):
            mock_ctx.return_value = MagicMock()
            mock_ctx.return_value.workspace = "."
            mock_ctx.return_value.runtime_root = "/tmp"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/state/snapshot")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["workspace"] == "."

    async def test_app_shutdown_returns_200(self) -> None:
        """POST /app/shutdown returns 200."""
        app = _build_app()
        with (
            patch(
                "polaris.infrastructure.di.container.get_container",
            ) as mock_get_container,
            patch(
                "polaris.delivery.http.routers.system.terminate_external_loop_pm_processes",
                return_value=[],
            ),
            patch("polaris.delivery.http.routers.system.clear_stop_flag"),
            patch("polaris.delivery.http.routers.system.clear_director_stop_flag"),
        ):
            mock_pm_service = MagicMock()
            mock_pm_service.get_status.return_value = {"running": False}
            mock_pm_service.stop = AsyncMock()
            mock_director_service = MagicMock()
            mock_director_service.get_status = AsyncMock(return_value={"state": "idle"})
            mock_director_service.stop = AsyncMock()
            mock_container = MagicMock()
            mock_container.resolve_async = AsyncMock(
                side_effect=lambda cls: mock_pm_service if "PMService" in str(cls) else mock_director_service
            )
            # get_container() returns a coroutine that resolves to mock_container
            # but code doesn't await it, so we need to return mock_container directly
            mock_get_container.return_value = mock_container

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/app/shutdown")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "pm_terminated" in payload
        assert "director_terminated" in payload

    async def test_nonexistent_endpoint_returns_404(self) -> None:
        """GET /nonexistent returns 404."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/nonexistent")

        assert response.status_code == 404
