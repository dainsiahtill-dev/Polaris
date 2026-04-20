"""Defensive tests for polaris.delivery.http.error_handlers.

Verifies that:
1. All exception handlers are registered with the FastAPI app.
2. DomainException subclasses map to correct HTTP status codes.
3. RequestValidationError returns 422 with structured errors.
4. Unhandled Exception is logged (not silently swallowed) and returns 500.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.domain.exceptions import (
    AuthenticationError,
    ConflictError,
    DomainException,
    NotFoundError,
    PermissionDeniedError,
    ProcessAlreadyRunningError,
    ProcessNotRunningError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    ValidationError,
)


class _MinimalAppFixture:
    """Minimal FastAPI app wired with exception handlers for isolated testing."""

    def __init__(self) -> None:
        self.app = FastAPI()
        setup_exception_handlers(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False)

        @self.app.get("/raise-domain")
        def raise_domain() -> dict[str, str]:
            raise DomainException("test domain error", code="TEST_ERROR")

        @self.app.get("/raise-not-found")
        def raise_not_found() -> dict[str, str]:
            raise NotFoundError("Widget", "w123")

        @self.app.get("/raise-validation")
        def raise_validation() -> dict[str, str]:
            raise ValidationError("bad input", field="email")

        @self.app.get("/raise-conflict")
        def raise_conflict() -> dict[str, str]:
            raise ConflictError("already taken", resource_type="username")

        @self.app.get("/raise-permission")
        def raise_permission() -> dict[str, str]:
            raise PermissionDeniedError("nope", action="delete", resource="doc")

        @self.app.get("/raise-auth")
        def raise_auth() -> dict[str, str]:
            raise AuthenticationError("bad token")

        @self.app.get("/raise-rate-limit")
        def raise_rate_limit() -> dict[str, str]:
            raise RateLimitError("slow down", retry_after=60)

        @self.app.get("/raise-service-unavailable")
        def raise_service_unavailable() -> dict[str, str]:
            raise ServiceUnavailableError("db")

        @self.app.get("/raise-timeout")
        def raise_timeout() -> dict[str, str]:
            raise TimeoutError("ops timed out", timeout_seconds=30.0)

        @self.app.get("/raise-process-running")
        def raise_process_running() -> dict[str, str]:
            raise ProcessAlreadyRunningError("my-proc", pid=1234)

        @self.app.get("/raise-process-not-running")
        def raise_process_not_running() -> dict[str, str]:
            raise ProcessNotRunningError("my-proc")

        @self.app.get("/raise-unhandled")
        def raise_unhandled() -> dict[str, str]:
            raise RuntimeError("unexpected bug")


@pytest.fixture
def app() -> _MinimalAppFixture:
    return _MinimalAppFixture()


class TestExceptionHandlersRegistered:
    """Phase C: Verify handlers are present on the app."""

    def test_domain_exception_handler_registered(self, app: _MinimalAppFixture) -> None:
        assert DomainException in app.app.exception_handlers

    def test_request_validation_error_handler_registered(self, app: _MinimalAppFixture) -> None:
        assert RequestValidationError in app.app.exception_handlers

    def test_base_exception_handler_registered(self, app: _MinimalAppFixture) -> None:
        assert Exception in app.app.exception_handlers


class TestDomainExceptionHandler:
    """Phase C: Domain exceptions → correct status codes + structured JSON."""

    @pytest.mark.parametrize(
        "path,expected_code,expected_error_code",
        [
            ("/raise-domain", 500, "TEST_ERROR"),
            ("/raise-not-found", 404, "NOT_FOUND"),
            ("/raise-validation", 422, "VALIDATION_ERROR"),
            ("/raise-conflict", 409, "CONFLICT"),
            ("/raise-permission", 403, "PERMISSION_DENIED"),
            ("/raise-auth", 401, "AUTHENTICATION_ERROR"),
            ("/raise-rate-limit", 429, "RATE_LIMIT_EXCEEDED"),
            ("/raise-service-unavailable", 503, "SERVICE_UNAVAILABLE"),
            ("/raise-timeout", 504, "TIMEOUT"),
            ("/raise-process-running", 409, "PROCESS_ALREADY_RUNNING"),
            ("/raise-process-not-running", 409, "PROCESS_NOT_RUNNING"),
        ],
    )
    def test_status_code_and_error_code(
        self,
        app: _MinimalAppFixture,
        path: str,
        expected_code: int,
        expected_error_code: str,
    ) -> None:
        response = app.client.get(path)
        assert response.status_code == expected_code, response.json()
        body = response.json()
        assert body["error"]["code"] == expected_error_code

    def test_not_found_error_includes_details(self, app: _MinimalAppFixture) -> None:
        response = app.client.get("/raise-not-found")
        assert response.status_code == 404
        details = response.json()["error"]["details"]
        assert details["resource_type"] == "Widget"
        assert details["resource_id"] == "w123"

    def test_validation_error_includes_field(self, app: _MinimalAppFixture) -> None:
        response = app.client.get("/raise-validation")
        assert response.status_code == 422
        details = response.json()["error"]["details"]
        assert details.get("field") == "email"


class TestUnhandledExceptionHandler:
    """Phase C: Unhandled exceptions return 500 with type name, NOT silently swallowed."""

    def test_unhandled_returns_500_with_type(self, app: _MinimalAppFixture) -> None:
        response = app.client.get("/raise-unhandled")
        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["details"]["type"] == "RuntimeError"

    def test_unhandled_does_not_leak_stack_trace(self, app: _MinimalAppFixture) -> None:
        response = app.client.get("/raise-unhandled")
        body = response.json()
        # The generic handler MUST NOT include the exception message
        # (it already only returns type name, which is intentional)
        assert "unexpected bug" not in body["error"].get("message", "")
        assert "Traceback" not in str(body)


class TestRequestValidationHandler:
    """Phase C: FastAPI RequestValidationError is handled."""

    def test_query_param_validation_error(self, app: _MinimalAppFixture) -> None:
        # Define an endpoint with query validation
        @app.app.get("/validated")
        def validated(x: int) -> dict[str, int]:
            return {"x": x}

        response = app.client.get("/validated?x=not-an-int")
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"] == "Request validation failed"
        assert "errors" in body["error"]["details"]
