"""Contract tests for polaris.delivery.http.routers.ollama module."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import ollama as ollama_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(ollama_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


class TestOllamaRouter:
    """Contract tests for the ollama router."""

    def test_get_ollama_models_happy_path(self) -> None:
        """GET /ollama/models returns 200 with model list."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.ollama.list_ollama_models",
            return_value=["llama2", "codellama"],
        ) as mock_list:
            response = client.get("/ollama/models")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["models"] == ["llama2", "codellama"]
        mock_list.assert_called_once()

    def test_get_ollama_models_empty(self) -> None:
        """GET /ollama/models returns empty list when no models."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.ollama.list_ollama_models",
            return_value=[],
        ):
            response = client.get("/ollama/models")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["models"] == []

    def test_stop_ollama_models_happy_path(self) -> None:
        """POST /ollama/stop returns 200 with stop result."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.ollama.ollama_stop",
            return_value={"stopped": True},
        ) as mock_stop:
            response = client.post("/ollama/stop")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["stopped"] is True
        mock_stop.assert_called_once()

    def test_stop_ollama_models_error(self) -> None:
        """POST /ollama/stop handles errors gracefully."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.ollama.ollama_stop",
            return_value={"stopped": False, "error": "service unavailable"},
        ):
            response = client.post("/ollama/stop")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["stopped"] is False
