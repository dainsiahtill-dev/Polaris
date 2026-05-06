"""Contract tests for polaris.delivery.http.routers.memory module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import memory as memory_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(memory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestMemoryRouter:
    """Contract tests for the memory router."""

    def test_get_memory_state_happy_path(self) -> None:
        """GET /memory/state returns 200 with AnthroState."""
        client = _build_client()
        mock_mem_store = MagicMock()
        mock_mem_store.memories = ["m1", "m2"]
        mock_mem_store.count_recent_errors.return_value = 0

        mock_ref_store = MagicMock()
        mock_ref_store.get_last_reflection_step.return_value = 5
        mock_ref_store.reflections = ["r1"]

        with (
            patch(
                "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_memory_store",
                return_value=mock_mem_store,
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_reflection_store",
                return_value=mock_ref_store,
            ),
        ):
            response = client.get("/memory/state")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["last_reflection_step"] == 5
        assert payload["recent_error_count"] == 0
        assert payload["total_memories"] == 2
        assert payload["total_reflections"] == 1

    def test_get_memory_state_store_not_initialized(self) -> None:
        """GET /memory/state returns 503 when memory store is not initialized."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_memory_store",
                return_value=None,
            ),
        ):
            response = client.get("/memory/state")

        assert response.status_code == 503
        assert response.json()["error"]["message"] == "Memory store not initialized"

    def test_delete_memory_happy_path(self) -> None:
        """DELETE /memory/memories/{id} returns 200 on successful deletion."""
        client = _build_client()
        mock_mem_store = MagicMock()
        mock_mem_store.delete.return_value = True

        with (
            patch(
                "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_memory_store",
                return_value=mock_mem_store,
            ),
        ):
            response = client.delete("/memory/memories/mem-123")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "deleted"
        assert payload["id"] == "mem-123"
        mock_mem_store.delete.assert_called_once_with("mem-123")

    def test_delete_memory_not_found(self) -> None:
        """DELETE /memory/memories/{id} returns 404 when memory does not exist."""
        client = _build_client()
        mock_mem_store = MagicMock()
        mock_mem_store.delete.return_value = False

        with (
            patch(
                "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_memory_store",
                return_value=mock_mem_store,
            ),
        ):
            response = client.delete("/memory/memories/nonexistent")

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Memory not found"

    def test_delete_memory_store_not_initialized(self) -> None:
        """DELETE /memory/memories/{id} returns 503 when memory store is not initialized."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.memory.init_anthropomorphic_modules",
            ),
            patch(
                "polaris.delivery.http.routers.memory.get_memory_store",
                return_value=None,
            ),
        ):
            response = client.delete("/memory/memories/mem-123")

        assert response.status_code == 503
        assert response.json()["error"]["message"] == "Memory store not initialized"
