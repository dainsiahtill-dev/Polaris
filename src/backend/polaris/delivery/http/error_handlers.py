"""API error handlers for Polaris backend.

Converts domain exceptions to HTTP responses.
Follows FastAPI exception handler pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
    TimeoutError as DomainTimeoutError,
    ValidationError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def setup_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the FastAPI app."""

    @app.exception_handler(DomainException)
    async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
        """Handle all domain exceptions.

        Raises:
            DomainException: Any domain-level error (validation, not found, conflict, etc.)
        """
        logger.warning(
            "[error_handler] DomainException intercepted: code=%s status=%d path=%s msg=%s",
            exc.code,
            exc.status_code,
            request.url.path,
            exc.message,
            exc_info=True,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details if exc.details else None,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle FastAPI request validation errors (Pydantic / query param errors)."""
        errors: list[dict[str, Any]] = []
        for error in exc.errors():
            errors.append(
                {
                    "field": " -> ".join(str(loc) for loc in error.get("loc", [])),
                    "type": error.get("type", "unknown"),
                    "message": error.get("msg", "Unknown error"),
                }
            )

        logger.warning(
            "[error_handler] RequestValidationError: path=%s errors=%s",
            request.url.path,
            errors,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"errors": errors},
                }
            },
        )

    from polaris.delivery.http.routers._shared import StructuredHTTPException

    @app.exception_handler(StructuredHTTPException)
    async def structured_http_exception_handler(request: Request, exc: StructuredHTTPException) -> JSONResponse:
        """Handle StructuredHTTPException: return unified {code, message, details} format.

        Matches the ADR-003 error contract so all role/* API errors use the same shape.
        """
        logger.warning(
            "[error_handler] StructuredHTTPException: code=%s status=%d path=%s msg=%s",
            exc.code,
            exc.status_code,
            request.url.path,
            exc.structured_message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.structured_message,
                    "details": exc.structured_details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all handler for any unhandled exception.

        Security note: The exception type name is included in the response body
        (details.type) so clients can distinguish error categories without leaking
        internal stack traces or messages.
        """
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        logger.error(
            "[error_handler] Unhandled exception: type=%s path=%s msg=%s",
            exc_type,
            request.url.path,
            exc_msg,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                    "details": {"type": exc_type},
                }
            },
        )


# Error response examples for OpenAPI documentation
ERROR_RESPONSES: dict[type[DomainException], dict[str, Any]] = {
    ValidationError: {"status_code": 422, "description": "Validation error"},
    NotFoundError: {"status_code": 404, "description": "Resource not found"},
    ConflictError: {"status_code": 409, "description": "Resource conflict"},
    PermissionDeniedError: {"status_code": 403, "description": "Permission denied"},
    AuthenticationError: {"status_code": 401, "description": "Authentication required"},
    RateLimitError: {"status_code": 429, "description": "Rate limit exceeded"},
    ServiceUnavailableError: {"status_code": 503, "description": "Service unavailable"},
    DomainTimeoutError: {"status_code": 504, "description": "Operation timed out"},
    ProcessAlreadyRunningError: {"status_code": 409, "description": "Process already running"},
    ProcessNotRunningError: {"status_code": 409, "description": "Process not running"},
}
