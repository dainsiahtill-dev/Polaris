"""Contract tests for polaris.delivery.http.routers.permissions module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import permissions as permissions_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(permissions_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return app


@pytest.mark.asyncio
class TestPermissionsRouter:
    """Contract tests for the permissions router."""

    async def test_get_effective_permissions_returns_200(self) -> None:
        """GET /v2/permissions/effective returns 200 with permissions."""
        app = _build_app()
        mock_service = MagicMock()
        mock_service.get_effective_permissions = AsyncMock(
            return_value=["read", "write"]
        )
        app.dependency_overrides[permissions_router._get_permission_service] = (
            lambda: mock_service
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/v2/permissions/effective",
                params={"subject_type": "role", "subject_id": "pm"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["subject"]["type"] == "role"
        assert payload["subject"]["id"] == "pm"
        assert "read" in payload["permissions"]

    async def test_list_roles_returns_200(self) -> None:
        """GET /v2/permissions/roles returns 200 with role list."""
        app = _build_app()
        mock_service = MagicMock()
        mock_service.list_roles = AsyncMock(
            return_value=[
                {
                    "id": "pm",
                    "display_name": "PM",
                    "description": "Project Manager",
                    "permission_count": 5,
                    "inherits_from": [],
                    "priority": 10,
                }
            ]
        )
        app.dependency_overrides[permissions_router._get_permission_service] = (
            lambda: mock_service
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v2/permissions/roles")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "roles" in payload
        assert len(payload["roles"]) == 1
        assert payload["roles"][0]["id"] == "pm"

    async def test_assign_role_returns_200(self) -> None:
        """POST /v2/permissions/assign returns 200 with assignment result."""
        app = _build_app()
        mock_service = MagicMock()
        mock_service.assign_role = AsyncMock(
            return_value={
                "assigned": True,
                "subject": {"type": "user", "id": "user-123"},
                "role_id": "pm",
            }
        )
        app.dependency_overrides[permissions_router._get_permission_service] = (
            lambda: mock_service
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v2/permissions/assign",
                json={
                    "subject_type": "user",
                    "subject_id": "user-123",
                    "role_id": "pm",
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["assigned"] is True
        assert payload["role_id"] == "pm"

    async def test_list_policies_returns_200(self) -> None:
        """GET /v2/permissions/policies returns 200 with policy list."""
        app = _build_app()
        mock_service = MagicMock()
        mock_service.list_policies = MagicMock(
            return_value=[
                {
                    "id": "policy-1",
                    "name": "Default Policy",
                    "effect": "allow",
                    "subjects": [{"type": "role", "id": "pm"}],
                    "resources": [{"type": "file", "pattern": "**/*"}],
                    "actions": ["read", "write"],
                    "priority": 10,
                    "enabled": True,
                }
            ]
        )
        app.dependency_overrides[permissions_router._get_permission_service] = (
            lambda: mock_service
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v2/permissions/policies")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "policies" in payload
        assert len(payload["policies"]) == 1
        assert payload["policies"][0]["id"] == "policy-1"

    async def test_nonexistent_endpoint_returns_404(self) -> None:
        """GET /v2/permissions/nonexistent returns 404."""
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/v2/permissions/nonexistent")

        assert response.status_code == 404