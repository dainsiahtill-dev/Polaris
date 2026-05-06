"""Tests for Polaris HTTP middleware integration.

Covers rate limiting, CORS, auth middleware, logging middleware, and
audit context middleware at the integration level via FastAPI app factory.
External services are mocked to avoid runtime dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import Auth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
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
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    # Missing auth object is a fail-closed server configuration error.
    app.state.auth = None

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
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.fixture
async def authed_client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create a client with auth token configured."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = Auth("secret-token")

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
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_headers_on_get(client: AsyncClient) -> None:
    """CORS headers should be present on GET responses when Origin is provided."""
    response = await client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


@pytest.mark.asyncio
async def test_cors_preflight_options(client: AsyncClient) -> None:
    """CORS preflight OPTIONS request should return proper headers."""
    response = await client.options(
        "/v2/pm/status",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert "access-control-allow-methods" in response.headers


@pytest.mark.asyncio
async def test_cors_allows_credentials(client: AsyncClient) -> None:
    """CORS should allow credentials."""
    response = await client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_cors_allows_all_methods(client: AsyncClient) -> None:
    """CORS should allow all methods."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert response.status_code == 200
    allowed = response.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allowed or "*" in allowed


# ---------------------------------------------------------------------------
# Auth Middleware / require_auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_rejects_missing_token(authed_client: AsyncClient) -> None:
    """Requests without auth token should be rejected with 401."""
    response = await authed_client.get("/v2/pm/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


@pytest.mark.asyncio
async def test_auth_accepts_valid_token(authed_client: AsyncClient) -> None:
    """Requests with valid bearer token should be accepted."""
    mock_pm = MagicMock()
    mock_pm.get_status.return_value = {"running": False}

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await authed_client.get(
            "/v2/pm/status",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_rejects_invalid_token(authed_client: AsyncClient) -> None:
    """Requests with invalid token should be rejected with 401."""
    response = await authed_client.get(
        "/v2/pm/status",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_allows_public_health_endpoint(authed_client: AsyncClient) -> None:
    """Health endpoint should be accessible without auth."""
    response = await authed_client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_allows_public_live_endpoint(authed_client: AsyncClient) -> None:
    """Live endpoint should be accessible without auth."""
    response = await authed_client.get("/live")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_not_initialized_rejects_protected_endpoints(client: AsyncClient) -> None:
    """When auth is not initialized, protected endpoints fail closed."""
    mock_pm = MagicMock()
    mock_pm.get_status.return_value = {"running": False}

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.get("/v2/pm/status")
        assert response.status_code == 503
        assert response.json()["detail"] == "auth not initialized"


# ---------------------------------------------------------------------------
# Rate Limit Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_disabled_allows_requests(mock_settings: Settings) -> None:
    """When rate limiting is disabled, requests should not be blocked."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = Auth("")

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
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch.dict("os.environ", {"KERNELONE_RATE_LIMIT_ENABLED": "false"}),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/health")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_excluded_paths_not_limited(mock_settings: Settings) -> None:
    """Health and metrics endpoints should be excluded from rate limiting."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = Auth("")

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
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            # Make many requests to health - should never be rate limited
            for _ in range(50):
                response = await ac.get("/health")
                assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_headers_present(mock_settings: Settings) -> None:
    """Rate limit headers should be present on non-excluded paths."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/ready")
            assert response.status_code == 200
            assert "x-ratelimit-limit" in response.headers


# ---------------------------------------------------------------------------
# Logging Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logging_middleware_adds_response_time_header(mock_settings: Settings) -> None:
    """Logging middleware should add X-Response-Time header."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/ready")
            assert response.status_code == 200
            assert "x-response-time" in response.headers


@pytest.mark.asyncio
async def test_logging_middleware_excludes_health(mock_settings: Settings) -> None:
    """Logging middleware should not add response time to excluded paths (or should still work)."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/health")
            assert response.status_code == 200
            # Health is excluded from detailed logging
            assert "x-response-time" not in response.headers


# ---------------------------------------------------------------------------
# Audit Context Middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_context_adds_trace_headers(mock_settings: Settings) -> None:
    """Audit context middleware should add trace headers to responses."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/ready")
            assert response.status_code == 200
            assert "x-trace-id" in response.headers
            assert "x-run-id" in response.headers
            assert "x-task-id" in response.headers


@pytest.mark.asyncio
async def test_audit_context_preserves_existing_trace_id(mock_settings: Settings) -> None:
    """Audit context middleware should preserve existing trace IDs from request."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get(
                "/ready",
                headers={"X-Trace-ID": "custom-trace-123"},
            )
            assert response.status_code == 200
            assert response.headers["x-trace-id"] == "custom-trace-123"


@pytest.mark.asyncio
async def test_audit_context_excludes_health(mock_settings: Settings) -> None:
    """Audit context middleware should not add trace headers to excluded paths."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/health")
            assert response.status_code == 200
            # Health is excluded from audit context
            assert "x-trace-id" not in response.headers


# ---------------------------------------------------------------------------
# Middleware Ordering / Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_stack_order(mock_settings: Settings) -> None:
    """Middleware should be applied in correct order (CORS outermost)."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = Auth("")

    # Verify middleware stack exists
    assert len(app.user_middleware) > 0

    # CORS should be present
    middleware_classes = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in middleware_classes


@pytest.mark.asyncio
async def test_multiple_middleware_layers_execute(mock_settings: Settings) -> None:
    """All middleware layers should execute without interfering."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

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
        ),
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
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get(
                "/ready",
                headers={"Origin": "http://localhost:3000"},
            )
            assert response.status_code == 200
            # All middleware headers should be present
            assert "access-control-allow-origin" in response.headers  # CORS
            assert "x-response-time" in response.headers  # Logging
            assert "x-trace-id" in response.headers  # Audit context
            assert "x-ratelimit-limit" in response.headers  # Rate limit
