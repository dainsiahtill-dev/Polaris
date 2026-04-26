"""Contract tests for polaris.delivery.http.routers.primary module."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import primary_router


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(primary_router)
    return TestClient(app)


class TestPrimaryRouter:
    """Contract tests for the primary router (health, readiness, liveness)."""

    def test_health_check_happy_path(self) -> None:
        """GET /health returns 200 with service status."""
        client = _build_client()
        response = client.get("/health")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "ok"
        assert payload["service"] == "polaris-backend"
        assert payload["version"] == "2.0.0"

    def test_readiness_check_ready(self) -> None:
        """GET /ready returns 200 when service is ready."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.primary.get_settings",
        ) as mock_settings:
            mock_settings.return_value = type(
                "Settings",
                (),
                {"nats": type("Nats", (), {"enabled": False, "required": False})()},
            )()
            response = client.get("/ready")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True
        assert payload["checks"]["api"] == "ok"

    def test_readiness_check_not_ready_nats_required(self) -> None:
        """GET /ready returns 503 when NATS is required but not connected."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.primary.get_settings",
            ) as mock_settings,
            patch(
                "polaris.delivery.http.routers.primary.is_nats_connected",
                return_value=False,
            ),
            patch(
                "polaris.infrastructure.messaging.get_default_client",
                side_effect=RuntimeError("no nats"),
            ),
        ):
            mock_settings.return_value = type(
                "Settings",
                (),
                {"nats": type("Nats", (), {"enabled": True, "required": True})()},
            )()
            response = client.get("/ready")

        assert response.status_code == 503
        payload: dict[str, Any] = response.json()
        assert payload["detail"]["ready"] is False
        assert payload["detail"]["checks"]["nats"] == "required_but_not_connected"

    def test_readiness_check_nats_optional_not_connected(self) -> None:
        """GET /ready returns 200 when NATS is optional and not connected."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.primary.get_settings",
            ) as mock_settings,
            patch(
                "polaris.delivery.http.routers.primary.is_nats_connected",
                return_value=False,
            ),
            patch(
                "polaris.infrastructure.messaging.get_default_client",
                side_effect=RuntimeError("no nats"),
            ),
        ):
            mock_settings.return_value = type(
                "Settings",
                (),
                {"nats": type("Nats", (), {"enabled": True, "required": False})()},
            )()
            response = client.get("/ready")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True
        assert payload["checks"]["nats"] == "not_connected"

    def test_liveness_check(self) -> None:
        """GET /live returns 200 liveness probe."""
        client = _build_client()
        response = client.get("/live")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["alive"] is True
