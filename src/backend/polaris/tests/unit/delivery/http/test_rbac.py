"""Unit tests for the RBAC skeleton framework.

Covers:
  - UserRole.from_string parsing and fallback behaviour
  - UserRole.level ordering
  - require_role dependency (allowed, disallowed, missing)
  - RBACMiddleware (header population, default fallback)
  - Integration with require_auth (role propagation)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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
    role_from_auth_context,
)
from polaris.kernelone.auth_context import SimpleAuthContext

# ---------------------------------------------------------------------------
# UserRole.from_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("admin", UserRole.ADMIN),
        ("developer", UserRole.DEVELOPER),
        ("viewer", UserRole.VIEWER),
        ("unknown", UserRole.VIEWER),
        ("", UserRole.VIEWER),
        ("ADMIN", UserRole.ADMIN),
        ("Developer", UserRole.DEVELOPER),
        ("VIEWER", UserRole.VIEWER),
    ],
)
def test_user_role_from_string(input_str: str, expected: UserRole) -> None:
    assert UserRole.from_string(input_str) is expected


def test_user_role_from_string_with_explicit_default() -> None:
    assert UserRole.from_string("bogus", default=UserRole.DEVELOPER) is UserRole.DEVELOPER


def test_user_role_from_string_none_defaults_to_viewer() -> None:
    assert UserRole.from_string(None) is UserRole.VIEWER


# ---------------------------------------------------------------------------
# UserRole.level
# ---------------------------------------------------------------------------


def test_user_role_level_ordering() -> None:
    assert UserRole.ADMIN.level > UserRole.DEVELOPER.level > UserRole.VIEWER.level


def test_user_role_level_values() -> None:
    assert UserRole.VIEWER.level == 1
    assert UserRole.DEVELOPER.level == 2
    assert UserRole.ADMIN.level == 3


# ---------------------------------------------------------------------------
# role_from_auth_context
# ---------------------------------------------------------------------------


def test_role_from_auth_context_none() -> None:
    assert role_from_auth_context(None) is UserRole.VIEWER


def test_role_from_auth_context_anonymous() -> None:
    ctx = SimpleAuthContext(principal="anonymous")
    assert role_from_auth_context(ctx) is UserRole.VIEWER


def test_role_from_auth_context_with_roles_list() -> None:
    ctx = SimpleAuthContext(principal="u", metadata={"roles": ["developer"]})
    assert role_from_auth_context(ctx) is UserRole.DEVELOPER


def test_role_from_auth_context_with_role_string() -> None:
    ctx = SimpleAuthContext(principal="u", metadata={"role": "admin"})
    assert role_from_auth_context(ctx) is UserRole.ADMIN


def test_role_from_auth_context_highest_role_wins() -> None:
    ctx = SimpleAuthContext(principal="u", metadata={"roles": ["viewer", "admin", "developer"]})
    assert role_from_auth_context(ctx) is UserRole.ADMIN


def test_role_from_auth_context_unknown_role_fallback() -> None:
    ctx = SimpleAuthContext(principal="u", metadata={"roles": ["superuser"]})
    assert role_from_auth_context(ctx) is UserRole.VIEWER


# ---------------------------------------------------------------------------
# require_role dependency
# ---------------------------------------------------------------------------


def _make_request_with_role(role: UserRole | None = None, auth: bool = True) -> Request:
    """Build a mock Request with the given role and optional auth context."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": FastAPI(),
    }
    request = Request(scope)
    if auth:
        request.state.auth_context = SimpleAuthContext(
            principal="test",
            metadata={"roles": [role.value] if role else ["viewer"]},
        )
    if role is not None:
        request.state.user_role = role
    else:
        request.state.user_role = UserRole.VIEWER
    return request


def test_require_role_allowed_passes() -> None:
    request = _make_request_with_role(UserRole.ADMIN)
    checker = require_role([UserRole.ADMIN, UserRole.DEVELOPER])
    checker(request)  # should not raise


def test_require_role_allowed_developer() -> None:
    request = _make_request_with_role(UserRole.DEVELOPER)
    checker = require_role([UserRole.DEVELOPER])
    checker(request)  # should not raise


def test_require_role_disallowed_raises_403() -> None:
    request = _make_request_with_role(UserRole.VIEWER)
    checker = require_role([UserRole.ADMIN])
    with pytest.raises(Exception) as exc_info:
        checker(request)
    assert exc_info.value.status_code == 403
    assert "viewer" in exc_info.value.detail


def test_require_role_missing_role_header_defaults_to_viewer_and_fails() -> None:
    request = _make_request_with_role(UserRole.VIEWER)
    checker = require_role([UserRole.ADMIN])
    with pytest.raises(Exception) as exc_info:
        checker(request)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# RBACMiddleware
# ---------------------------------------------------------------------------


@pytest.fixture
def rbac_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RBACMiddleware)

    @app.get("/role")
    async def read_role(request: Request) -> dict[str, str]:
        return {"role": request.state.user_role.value}

    return app


@pytest.mark.asyncio
async def test_rbac_middleware_defaults_to_viewer(rbac_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as client:
        response = await client.get("/role")
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_rbac_middleware_ignores_client_header(rbac_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(rbac_app), base_url="http://test") as client:
        response = await client.get("/role", headers={"X-User-Role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


# ---------------------------------------------------------------------------
# Integration with require_auth
# ---------------------------------------------------------------------------


@pytest.fixture
async def authed_app_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.state.auth = Auth("secret-token")

    @app.get("/auth-and-role", dependencies=[Depends(require_auth)])
    async def auth_and_role(request: Request) -> dict[str, str]:
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

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_require_auth_sets_user_role(authed_app_client: AsyncClient) -> None:
    response = await authed_app_client.get(
        "/auth-and-role",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["principal"] == "authenticated"
    assert data["role"] == "viewer"


@pytest.mark.asyncio
async def test_require_role_respects_auth_context_role(authed_app_client: AsyncClient) -> None:
    # With the default auth context (viewer), admin gate should reject
    response = await authed_app_client.post(
        "/admin-only",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 403
    assert "viewer" in response.json()["detail"]


# ---------------------------------------------------------------------------
# extract_role_from_request
# ---------------------------------------------------------------------------


def test_extract_role_from_request_with_auth_context() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": FastAPI(),
    }
    request = Request(scope)
    request.state.auth_context = SimpleAuthContext(
        principal="tester",
        metadata={"roles": ["developer"]},
    )
    role = extract_role_from_request(request)
    assert role is UserRole.DEVELOPER
    assert request.state.user_role is UserRole.DEVELOPER


def test_extract_role_from_request_without_auth_context() -> None:
    scope = {
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
