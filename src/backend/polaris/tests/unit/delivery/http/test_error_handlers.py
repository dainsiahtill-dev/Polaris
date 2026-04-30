"""Tests for Polaris HTTP error handlers.

Covers domain exception handling, validation errors, structured HTTP exceptions,
and the generic catch-all handler. Tests verify proper JSON response format
per ADR-003 contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    DomainException,
    NotFoundError,
    PermissionDeniedError,
    ProcessAlreadyRunningError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError as DomainTimeoutError,
    ValidationError,
)

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
    # auth=None bypasses auth entirely (dev/test mode)
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


# ---------------------------------------------------------------------------
# Domain Exception Handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_error_response_format(client: AsyncClient) -> None:
    """ValidationError should return 422 with structured error body."""

    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/validation-error")
    async def trigger_validation_error() -> None:
        raise ValidationError("Field required", field="name", value=None)

    response = await client.get("/_test/validation-error")
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "Field required" in data["error"]["message"]
    assert data["error"]["details"]["field"] == "name"


@pytest.mark.asyncio
async def test_not_found_error_response_format(client: AsyncClient) -> None:
    """NotFoundError should return 404 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/not-found")
    async def trigger_not_found() -> None:
        raise NotFoundError("User", "user-123")

    response = await client.get("/_test/not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "NOT_FOUND"
    assert "user-123" in data["error"]["message"]
    assert data["error"]["details"]["resource_type"] == "User"


@pytest.mark.asyncio
async def test_conflict_error_response_format(client: AsyncClient) -> None:
    """ConflictError should return 409 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/conflict")
    async def trigger_conflict() -> None:
        raise ConflictError("Resource already exists", resource_type="Project")

    response = await client.get("/_test/conflict")
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "CONFLICT"
    assert "already exists" in data["error"]["message"]


@pytest.mark.asyncio
async def test_permission_denied_error_response_format(client: AsyncClient) -> None:
    """PermissionDeniedError should return 403 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/permission-denied")
    async def trigger_permission_denied() -> None:
        raise PermissionDeniedError("Access denied", action="delete", resource="project")

    response = await client.get("/_test/permission-denied")
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == "PERMISSION_DENIED"
    assert data["error"]["details"]["action"] == "delete"


@pytest.mark.asyncio
async def test_authentication_error_response_format(client: AsyncClient) -> None:
    """AuthenticationError should return 401 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/auth-error")
    async def trigger_auth_error() -> None:
        raise AuthenticationError("Invalid credentials")

    response = await client.get("/_test/auth-error")
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.asyncio
async def test_rate_limit_error_response_format(client: AsyncClient) -> None:
    """RateLimitError should return 429 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/rate-limit")
    async def trigger_rate_limit() -> None:
        raise RateLimitError("Too many requests", retry_after=60)

    response = await client.get("/_test/rate-limit")
    assert response.status_code == 429
    data = response.json()
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert data["error"]["details"]["retry_after"] == 60


@pytest.mark.asyncio
async def test_service_unavailable_error_response_format(client: AsyncClient) -> None:
    """ServiceUnavailableError should return 503 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/service-unavailable")
    async def trigger_service_unavailable() -> None:
        raise ServiceUnavailableError("database")

    response = await client.get("/_test/service-unavailable")
    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "database" in data["error"]["message"]


@pytest.mark.asyncio
async def test_timeout_error_response_format(client: AsyncClient) -> None:
    """TimeoutError should return 504 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/timeout")
    async def trigger_timeout() -> None:
        raise DomainTimeoutError("Operation timed out", timeout_seconds=30.0, operation="query")

    response = await client.get("/_test/timeout")
    assert response.status_code == 504
    data = response.json()
    assert data["error"]["code"] == "TIMEOUT_ERROR"
    assert data["error"]["details"]["timeout_seconds"] == 30.0


@pytest.mark.asyncio
async def test_process_already_running_error_response_format(client: AsyncClient) -> None:
    """ProcessAlreadyRunningError should return 409 with structured error body."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/process-running")
    async def trigger_process_running() -> None:
        raise ProcessAlreadyRunningError("pm_loop", pid=12345)

    response = await client.get("/_test/process-running")
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "PROCESS_ALREADY_RUNNING"
    assert data["error"]["details"]["pid"] == 12345


# ---------------------------------------------------------------------------
# RequestValidationError Handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastapi_validation_error_handler(client: AsyncClient) -> None:
    """FastAPI RequestValidationError should return 422 with field details."""
    app = client._transport.app  # type: ignore[attr-defined]

    from pydantic import BaseModel

    class TestModel(BaseModel):
        name: str
        age: int

    @app.post("/_test/validate")
    async def trigger_fastapi_validation(data: TestModel) -> dict[str, Any]:
        return {"ok": True}

    response = await client.post("/_test/validate", json={"age": "not_a_number"})
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "errors" in data["error"]["details"]
    assert len(data["error"]["details"]["errors"]) > 0


@pytest.mark.asyncio
async def test_fastapi_validation_missing_required_field(client: AsyncClient) -> None:
    """Missing required field should return 422 with field info."""
    app = client._transport.app  # type: ignore[attr-defined]

    from pydantic import BaseModel

    class TestModel(BaseModel):
        name: str
        email: str

    @app.post("/_test/validate-required")
    async def trigger_required(data: TestModel) -> dict[str, Any]:
        return {"ok": True}

    response = await client.post("/_test/validate-required", json={})
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    errors = data["error"]["details"]["errors"]
    assert len(errors) >= 1  # At least one validation error reported


# ---------------------------------------------------------------------------
# StructuredHTTPException Handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_http_exception_handler(client: AsyncClient) -> None:
    """StructuredHTTPException should return unified error format."""
    app = client._transport.app  # type: ignore[attr-defined]

    from polaris.delivery.http.routers._shared import StructuredHTTPException

    @app.get("/_test/structured")
    async def trigger_structured() -> None:
        raise StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": ["director", "qa"],
                "missing_roles": ["director"],
            },
        )

    response = await client.get("/_test/structured")
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == ["director"]


# ---------------------------------------------------------------------------
# Generic Exception Handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_exception_handler(client: AsyncClient) -> None:
    """Unhandled exceptions should return 500 with safe error info."""
    from unittest.mock import MagicMock

    from polaris.delivery.http.error_handlers import setup_exception_handlers

    app = client._transport.app  # type: ignore[attr-defined]
    setup_exception_handlers(app)

    # Find the generic exception handler
    generic_handler = None
    for exc_cls, handler in app.exception_handlers.items():
        if exc_cls is Exception:
            generic_handler = handler
            break

    assert generic_handler is not None

    mock_request = MagicMock()
    mock_request.url.path = "/test"
    exc = RuntimeError("Something went wrong internally")
    response = await generic_handler(mock_request, exc)
    assert response.status_code == 500
    data = response.body.decode()
    import json

    body = json.loads(data)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "internal error" in body["error"]["message"].lower()
    # Should include exception type but not full message
    assert body["error"]["details"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_generic_exception_does_not_leak_stacktrace(client: AsyncClient) -> None:
    """Generic handler should not leak internal stack traces."""
    from unittest.mock import MagicMock

    from polaris.delivery.http.error_handlers import setup_exception_handlers

    app = client._transport.app  # type: ignore[attr-defined]
    setup_exception_handlers(app)

    # Find the generic exception handler
    generic_handler = None
    for exc_cls, handler in app.exception_handlers.items():
        if exc_cls is Exception:
            generic_handler = handler
            break

    assert generic_handler is not None

    mock_request = MagicMock()
    mock_request.url.path = "/test"
    exc = ValueError("Secret internal detail that should not leak")
    response = await generic_handler(mock_request, exc)
    assert response.status_code == 500
    data = response.body.decode()
    import json

    body = json.loads(data)
    # The message should be generic, not the actual exception message
    assert "Secret internal detail" not in body["error"]["message"]
    assert body["error"]["details"]["type"] == "ValueError"


# ---------------------------------------------------------------------------
# Error Response Contract (ADR-003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_error_responses_follow_adr003_format(client: AsyncClient) -> None:
    """All error responses should follow {error: {code, message, details}} format."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/adr003-check")
    async def trigger_adr003() -> None:
        raise NotFoundError("Test", "test-123")

    response = await client.get("/_test/adr003-check")
    data = response.json()

    # Top-level must have 'error' key
    assert "error" in data
    # Error object must have code, message
    assert "code" in data["error"]
    assert "message" in data["error"]
    # Details may be null or dict
    assert "details" in data["error"]


@pytest.mark.asyncio
async def test_error_response_content_type_json(client: AsyncClient) -> None:
    """Error responses should have application/json content type."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/content-type")
    async def trigger_content_type() -> None:
        raise ConflictError("Test conflict")

    response = await client.get("/_test/content-type")
    assert "application/json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# DomainException Base Class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_exception_base_handler(client: AsyncClient) -> None:
    """Base DomainException should be handled by the generic domain handler."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/domain-base")
    async def trigger_domain_base() -> None:
        raise DomainException("Generic domain error", code="CUSTOM_ERROR")

    response = await client.get("/_test/domain-base")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "CUSTOM_ERROR"
    assert data["error"]["message"] == "Generic domain error"


@pytest.mark.asyncio
async def test_domain_exception_with_details(client: AsyncClient) -> None:
    """DomainException with details should include them in response."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/domain-details")
    async def trigger_domain_details() -> None:
        raise DomainException(
            "Error with details",
            code="DETAILED_ERROR",
            details={"field": "value", "count": 42},
        )

    response = await client.get("/_test/domain-details")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["details"]["field"] == "value"
    assert data["error"]["details"]["count"] == 42


@pytest.mark.asyncio
async def test_domain_exception_without_details_has_null_details(client: AsyncClient) -> None:
    """DomainException without details should have null details in response."""
    app = client._transport.app  # type: ignore[attr-defined]

    @app.get("/_test/domain-no-details")
    async def trigger_domain_no_details() -> None:
        raise DomainException("Simple error")

    response = await client.get("/_test/domain-no-details")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["details"] is None


# ---------------------------------------------------------------------------
# Error Response Examples (OpenAPI documentation)
# ---------------------------------------------------------------------------


def test_error_responses_mapping_exists() -> None:
    """ERROR_RESPONSES mapping should cover all domain exceptions."""
    from polaris.delivery.http.error_handlers import ERROR_RESPONSES

    assert ValidationError in ERROR_RESPONSES
    assert NotFoundError in ERROR_RESPONSES
    assert ConflictError in ERROR_RESPONSES
    assert PermissionDeniedError in ERROR_RESPONSES
    assert AuthenticationError in ERROR_RESPONSES
    assert RateLimitError in ERROR_RESPONSES
    assert ServiceUnavailableError in ERROR_RESPONSES
    assert DomainTimeoutError in ERROR_RESPONSES
    assert ProcessAlreadyRunningError in ERROR_RESPONSES


def test_error_responses_have_status_codes() -> None:
    """Each ERROR_RESPONSES entry should have status_code and description."""
    from polaris.delivery.http.error_handlers import ERROR_RESPONSES

    for _exc_class, response_info in ERROR_RESPONSES.items():
        assert "status_code" in response_info
        assert "description" in response_info
        assert isinstance(response_info["status_code"], int)
        assert 400 <= response_info["status_code"] < 600


def test_error_responses_status_codes_match_exception_defaults() -> None:
    """ERROR_RESPONSES status codes should match exception class defaults."""
    from polaris.delivery.http.error_handlers import ERROR_RESPONSES

    for exc_class, response_info in ERROR_RESPONSES.items():
        assert exc_class.status_code == response_info["status_code"]
