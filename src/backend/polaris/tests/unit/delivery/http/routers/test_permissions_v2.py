"""Tests for Polaris permissions v2 endpoints.

Covers POST /v2/permissions/v2/check, GET /v2/permissions/v2/effective,
GET /v2/permissions/v2/roles, POST /v2/permissions/v2/assign,
and GET /v2/permissions/v2/policies.
External services are mocked to avoid policy and role registry dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = Path(".")
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
def mock_permission_service() -> MagicMock:
    """Create a mock PermissionService with canned responses."""
    service = MagicMock()
    service.check_permission = AsyncMock(
        return_value=MagicMock(
            allowed=True,
            decision="allow",
            matched_policies=["pm-read-all"],
            reason="allowed by policy: pm-read-all",
        ),
    )
    service.get_effective_permissions = AsyncMock(return_value=["file:read:**/*", "tool:execute:*"])
    service.list_roles = AsyncMock(
        return_value=[
            {
                "id": "pm",
                "display_name": "PM",
                "description": "Project Manager",
                "permission_count": 2,
                "inherits_from": [],
                "priority": 10,
            },
        ],
    )
    service.assign_role = AsyncMock(
        return_value={
            "assigned": True,
            "subject": {"type": "user", "id": "user-123"},
            "role_id": "pm",
        },
    )
    service.list_policies = MagicMock(
        return_value=[
            {
                "id": "pm-read-all",
                "name": "PM Read All",
                "effect": "allow",
                "subjects": [{"type": "role", "id": "pm"}],
                "resources": [{"type": "file", "pattern": "**/*"}],
                "actions": ["read"],
                "priority": 10,
                "enabled": True,
            },
        ],
    )
    return service


@pytest.fixture
async def client(
    mock_settings: Settings, mock_app_state: AppState, mock_permission_service: MagicMock
) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan and overridden dependencies."""
    from polaris.delivery.http.app_factory import create_app
    from polaris.delivery.http.routers.permissions import _get_permission_service

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    # Override the permission service dependency so endpoints receive our mock
    async def _override_get_permission_service() -> MagicMock:
        return mock_permission_service

    app.dependency_overrides[_get_permission_service] = _override_get_permission_service

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# POST /v2/permissions/v2/check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_permissions_check_allowed(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Check permission should return allowed=True for valid request."""
    response = await client.post(
        "/v2/permissions/v2/check",
        json={
            "subject": {"type": "role", "id": "pm"},
            "resource": {"type": "file", "pattern": "**/*.py"},
            "action": "read",
            "context": {},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is True
    assert data["decision"] == "allow"
    assert "pm-read-all" in data["matched_policies"]
    mock_permission_service.check_permission.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_permissions_check_invalid_subject_type(
    client: AsyncClient, mock_permission_service: MagicMock
) -> None:
    """Check permission with invalid subject type should return 400."""
    mock_permission_service.check_permission = AsyncMock(side_effect=ValueError("invalid subject type"))
    response = await client.post(
        "/v2/permissions/v2/check",
        json={
            "subject": {"type": "invalid", "id": "x"},
            "resource": {"type": "file", "pattern": "*"},
            "action": "read",
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_v2_permissions_check_runtime_error(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Check permission runtime error should return 500."""
    mock_permission_service.check_permission = AsyncMock(side_effect=RuntimeError("db down"))
    response = await client.post(
        "/v2/permissions/v2/check",
        json={
            "subject": {"type": "role", "id": "pm"},
            "resource": {"type": "file", "pattern": "*"},
            "action": "read",
        },
    )
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# GET /v2/permissions/v2/effective
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_permissions_effective(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Effective permissions should return list of permissions."""
    response = await client.get("/v2/permissions/v2/effective?subject_type=role&subject_id=pm")
    assert response.status_code == 200
    data = response.json()
    assert data["subject"] == {"type": "role", "id": "pm"}
    assert isinstance(data["permissions"], list)
    assert "file:read:**/*" in data["permissions"]
    mock_permission_service.get_effective_permissions.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_permissions_effective_invalid_subject(
    client: AsyncClient, mock_permission_service: MagicMock
) -> None:
    """Effective permissions with invalid subject should return 400."""
    mock_permission_service.get_effective_permissions = AsyncMock(side_effect=ValueError("bad subject"))
    response = await client.get("/v2/permissions/v2/effective?subject_type=bad&subject_id=x")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# GET /v2/permissions/v2/roles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_permissions_roles(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Roles endpoint should return list of roles."""
    response = await client.get("/v2/permissions/v2/roles")
    assert response.status_code == 200
    data = response.json()
    assert "roles" in data
    assert len(data["roles"]) == 1
    role = data["roles"][0]
    assert role["id"] == "pm"
    assert role["display_name"] == "PM"
    mock_permission_service.list_roles.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_permissions_roles_error(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Roles endpoint error should return 500."""
    mock_permission_service.list_roles = AsyncMock(side_effect=RuntimeError("db error"))
    response = await client.get("/v2/permissions/v2/roles")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# POST /v2/permissions/v2/assign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_permissions_assign(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Assign role should return assigned=True."""
    response = await client.post(
        "/v2/permissions/v2/assign",
        json={
            "subject_type": "user",
            "subject_id": "user-123",
            "role_id": "pm",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["assigned"] is True
    assert data["role_id"] == "pm"
    assert data["subject"]["id"] == "user-123"
    mock_permission_service.assign_role.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_permissions_assign_invalid_role(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Assign role with invalid role should return 400."""
    mock_permission_service.assign_role = AsyncMock(side_effect=ValueError("role not found"))
    response = await client.post(
        "/v2/permissions/v2/assign",
        json={
            "subject_type": "user",
            "subject_id": "user-123",
            "role_id": "unknown",
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_v2_permissions_assign_runtime_error(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Assign role runtime error should return 500."""
    mock_permission_service.assign_role = AsyncMock(side_effect=RuntimeError("db down"))
    response = await client.post(
        "/v2/permissions/v2/assign",
        json={
            "subject_type": "user",
            "subject_id": "user-123",
            "role_id": "pm",
        },
    )
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# GET /v2/permissions/v2/policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_permissions_policies(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Policies endpoint should return list of policies."""
    response = await client.get("/v2/permissions/v2/policies")
    assert response.status_code == 200
    data = response.json()
    assert "policies" in data
    assert len(data["policies"]) == 1
    policy = data["policies"][0]
    assert policy["id"] == "pm-read-all"
    assert policy["enabled"] is True
    mock_permission_service.list_policies.assert_called_once()


@pytest.mark.asyncio
async def test_v2_permissions_policies_error(client: AsyncClient, mock_permission_service: MagicMock) -> None:
    """Policies endpoint error should return 500."""
    mock_permission_service.list_policies = MagicMock(side_effect=RuntimeError("db error"))
    response = await client.get("/v2/permissions/v2/policies")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"
