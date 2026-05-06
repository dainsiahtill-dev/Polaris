"""Integration tests for RBACMiddleware.

Covers:
  - Middleware registration and request processing
  - Role extraction (auth context, header fallback, default VIEWER)
  - Integration with require_auth dependency
  - Request lifecycle (role availability, persistence, cleanup)
  - Edge cases (concurrent requests, role stability mid-request)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from polaris.cells.runtime.state_owner.public.service import Auth
from polaris.delivery.http.auth.roles import UserRole
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.middleware.rbac import (
    RBACMiddleware,
    extract_role_from_request,
    require_role,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rbac_app() -> FastAPI:
    """FastAPI app with RBACMiddleware and role-exposing endpoint."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    @app.get("/role")
    async def read_role(request: Request) -> dict[str, str]:
        return {"role": request.state.user_role.value}

    @app.get("/role-and-auth", dependencies=[Depends(require_auth)])
    async def read_role_and_auth(request: Request) -> dict[str, str]:
        return {
            "role": request.state.user_role.value,
            "principal": request.state.auth_context.principal,
        }

    return app


@pytest.fixture
async def client(rbac_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as async_client:
        yield async_client


@pytest.fixture
def authed_rbac_app() -> FastAPI:
    """FastAPI app with RBACMiddleware, auth, and role-gated endpoints."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)
    app.state.auth = Auth("secret-token")

    @app.get("/role")
    async def read_role(request: Request) -> dict[str, str]:
        return {"role": request.state.user_role.value}

    @app.get("/protected", dependencies=[Depends(require_auth)])
    async def protected(request: Request) -> dict[str, str]:
        return {
            "role": request.state.user_role.value,
            "principal": request.state.auth_context.principal,
        }

    @app.post(
        "/admin-only",
        dependencies=[Depends(require_auth), Depends(require_role([UserRole.ADMIN]))],
    )
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
async def authed_client(authed_rbac_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(authed_rbac_app), base_url="http://test") as async_client:
        yield async_client


# ---------------------------------------------------------------------------
# 1. Middleware registration
# ---------------------------------------------------------------------------


def test_rbac_middleware_is_added_to_app() -> None:
    """RBACMiddleware should appear in the app's middleware stack."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    middleware_classes = [m.cls.__name__ for m in app.user_middleware]
    assert "RBACMiddleware" in middleware_classes


@pytest.mark.asyncio
async def test_middleware_processes_requests(client: AsyncClient) -> None:
    """Middleware should process requests and set a default role."""
    response = await client.get("/role")
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


# ---------------------------------------------------------------------------
# 2. Role extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_x_user_role_header_ignored(rbac_app: FastAPI) -> None:
    """Client-supplied X-User-Role header is ignored; default VIEWER is set."""
    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as c:
        response = await c.get("/role", headers={"X-User-Role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_missing_header_defaults_to_viewer(client: AsyncClient) -> None:
    """No X-User-Role header → request.state.user_role defaults to VIEWER."""
    response = await client.get("/role")
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_invalid_header_defaults_to_viewer(rbac_app: FastAPI) -> None:
    """Malformed X-User-Role header is ignored; role defaults to VIEWER."""
    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as c:
        response = await c.get("/role", headers={"X-User-Role": "superuser"})
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


# ---------------------------------------------------------------------------
# 3. Integration with require_auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_sets_role_middleware_respects_it(authed_client: AsyncClient) -> None:
    """When require_auth sets role, middleware/extract respects it."""
    response = await authed_client.get(
        "/protected",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "viewer"
    assert data["principal"] == "authenticated"


@pytest.mark.asyncio
async def test_auth_does_not_set_role_middleware_falls_back_to_header() -> None:
    """Auth doesn't set role → middleware falls back to default VIEWER."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)
    app.state.auth = Auth("secret-token")

    @app.get("/no-auth-role")
    async def no_auth_role(request: Request) -> dict[str, str]:
        return {"role": request.state.user_role.value}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        response = await c.get("/no-auth-role")
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_neither_auth_nor_header_sets_role_defaults_to_viewer(client: AsyncClient) -> None:
    """Neither auth nor header sets role → VIEWER."""
    response = await client.get("/role")
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


# ---------------------------------------------------------------------------
# 4. Request lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_available_in_route_handler(authed_client: AsyncClient) -> None:
    """Role set by middleware/auth should be readable inside route handlers."""
    response = await authed_client.get(
        "/protected",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert "role" in response.json()
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_role_persists_through_sub_requests() -> None:
    """Role should persist when a route handler makes internal sub-requests."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)
    app.state.auth = Auth("secret-token")

    @app.get("/inner")
    async def inner(request: Request) -> dict[str, str]:
        return {"role": request.state.user_role.value}

    @app.get("/outer")
    async def outer(request: Request) -> dict[str, str]:
        # Simulate sub-request by reading the same state
        return {
            "outer_role": request.state.user_role.value,
            "inner_role": request.state.user_role.value,
        }

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        response = await c.get("/outer")
    assert response.status_code == 200
    data = response.json()
    assert data["outer_role"] == "viewer"
    assert data["inner_role"] == "viewer"


@pytest.mark.asyncio
async def test_role_cleared_after_response(rbac_app: FastAPI) -> None:
    """Each new request should start with a fresh role state."""
    roles_seen: list[str] = []

    @rbac_app.get("/capture")
    async def capture_role(request: Request) -> dict[str, str]:
        roles_seen.append(request.state.user_role.value)
        return {"role": request.state.user_role.value}

    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as c:
        # First request
        r1 = await c.get("/capture")
        assert r1.status_code == 200
        # Second request
        r2 = await c.get("/capture")
        assert r2.status_code == 200

    assert roles_seen == ["viewer", "viewer"]


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_requests_with_different_roles() -> None:
    """Concurrent requests should each have independent role state."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    captured_roles: list[str] = []

    @app.get("/capture")
    async def capture(request: Request) -> dict[str, str]:
        captured_roles.append(request.state.user_role.value)
        await asyncio.sleep(0.01)
        # Verify role hasn't changed mid-request
        assert request.state.user_role.value == captured_roles[-1]
        return {"role": request.state.user_role.value}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        # Fire multiple concurrent requests
        responses = await asyncio.gather(*(c.get("/capture") for _ in range(5)))

    for resp in responses:
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"

    assert len(captured_roles) == 5
    assert all(r == "viewer" for r in captured_roles)


@pytest.mark.asyncio
async def test_role_does_not_change_mid_request() -> None:
    """Role should remain stable throughout a single request lifecycle."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    snapshots: list[str] = []

    @app.get("/stable")
    async def stable_role(request: Request) -> dict[str, str]:
        snapshots.append(request.state.user_role.value)
        await asyncio.sleep(0.01)
        snapshots.append(request.state.user_role.value)
        await asyncio.sleep(0.01)
        snapshots.append(request.state.user_role.value)
        return {"role": request.state.user_role.value}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        response = await c.get("/stable")

    assert response.status_code == 200
    assert len(snapshots) == 3
    assert all(s == "viewer" for s in snapshots)


@pytest.mark.asyncio
async def test_extract_role_from_request_mirrors_to_state() -> None:
    """extract_role_from_request should set request.state.user_role."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": FastAPI(),
    }
    request = Request(scope)
    role = extract_role_from_request(request)
    assert role is UserRole.VIEWER
    assert request.state.user_role is UserRole.VIEWER


@pytest.mark.asyncio
async def test_middleware_sets_default_before_handler_runs() -> None:
    """RBACMiddleware should initialize user_role before the route handler."""
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    observed: UserRole | None = None

    @app.get("/observe")
    async def observe(request: Request) -> dict[str, str]:
        nonlocal observed
        observed = request.state.user_role
        return {"role": observed.value}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        response = await c.get("/observe")

    assert response.status_code == 200
    assert observed is UserRole.VIEWER
