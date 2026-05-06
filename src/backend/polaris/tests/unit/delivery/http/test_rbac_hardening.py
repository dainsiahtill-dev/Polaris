"""Regression tests for HTTP RBAC hardening."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from polaris.cells.runtime.state_owner.public.service import Auth
from polaris.delivery.http.auth.roles import UserRole
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.middleware.rbac import require_role, role_from_auth_context
from polaris.kernelone.auth_context import SimpleAuthContext


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Create a minimal FastAPI app with auth and RBAC gates."""
    app = FastAPI()
    app.state.auth = Auth("secret-token")

    @app.get("/authenticated", dependencies=[Depends(require_auth)])
    async def authenticated(request: Request) -> dict[str, str]:
        return {
            "principal": request.state.auth_context.principal,
            "role": request.state.user_role.value,
        }

    @app.post(
        "/admin-only",
        dependencies=[Depends(require_auth), Depends(require_role([UserRole.ADMIN]))],
    )
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}

    @app.post(
        "/developer-only",
        dependencies=[Depends(require_auth), Depends(require_role([UserRole.DEVELOPER]))],
    )
    async def developer_only() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_authenticated_endpoint_ignores_forged_admin_header(client: AsyncClient) -> None:
    response = await client.get(
        "/authenticated",
        headers={
            "Authorization": "Bearer secret-token",
            "X-User-Role": "admin",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal": "authenticated",
        "role": "viewer",
    }


@pytest.mark.asyncio
async def test_forged_admin_header_cannot_pass_admin_gate(client: AsyncClient) -> None:
    response = await client.post(
        "/admin-only",
        headers={
            "Authorization": "Bearer secret-token",
            "X-User-Role": "admin",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "role 'viewer' not authorized for this resource"


@pytest.mark.asyncio
async def test_forged_developer_header_cannot_pass_developer_gate(client: AsyncClient) -> None:
    response = await client.post(
        "/developer-only",
        headers={
            "Authorization": "Bearer secret-token",
            "X-User-Role": "developer",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "role 'viewer' not authorized for this resource"


def test_server_bound_auth_context_can_carry_trusted_role() -> None:
    context = SimpleAuthContext(
        principal="server-bound",
        metadata={"roles": ["developer"]},
    )

    assert role_from_auth_context(context) is UserRole.DEVELOPER
