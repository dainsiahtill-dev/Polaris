"""Contract tests for polaris.delivery.http.routers.lancedb module."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import lancedb as lancedb_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(lancedb_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


class TestLancedbRouter:
    """Contract tests for the lancedb router."""

    def test_lancedb_status_happy_path(self) -> None:
        """GET /lancedb/status returns 200 with status payload."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.lancedb.get_lancedb_status",
            return_value={"ok": True, "python": "3.12"},
        ):
            response = client.get("/lancedb/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["python"] == "3.12"

    def test_lancedb_status_error(self) -> None:
        """GET /lancedb/status handles errors gracefully."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.lancedb.get_lancedb_status",
            return_value={"ok": False, "error": "connection failed"},
        ):
            response = client.get("/lancedb/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is False
        assert payload["error"] == "connection failed"
