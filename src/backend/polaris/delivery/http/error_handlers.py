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
            stack_info=True,
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

    # Starlette 1.0.0 / FastAPI 0.135.2 routes ``Exception`` handlers through
    # ``ServerErrorMiddleware``, which **always re-raises** after sending the
    # response (by design, so test clients can optionally surface the error).
    # This causes exceptions to propagate through the ASGI transport in test
    # mode even though the HTTP response is correct.
    #
    # Work-around: register specific exception-class handlers for the common
    # built-in types we expect to leak from route handlers.  These go through
    # ``ExceptionMiddleware`` which does **not** re-raise after handling.
    #
    # See: https://github.com/encode/starlette/blob/1.0.0/starlette/middleware/errors.py

    async def _generic_error_response(request: Request, exc: Exception) -> JSONResponse:
        """Build the standard 500 JSONResponse for an unhandled exception."""
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        logger.error(
            "[error_handler] Unhandled exception: type=%s path=%s msg=%s",
            exc_type,
            request.url.path,
            exc_msg,
            stack_info=True,
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

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(TypeError)
    async def type_error_handler(request: Request, exc: TypeError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(AttributeError)
    async def attribute_error_handler(request: Request, exc: AttributeError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(IndexError)
    async def index_error_handler(request: Request, exc: IndexError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(NotImplementedError)
    async def not_implemented_error_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(AssertionError)
    async def assertion_error_handler(request: Request, exc: AssertionError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(OSError)
    async def os_error_handler(request: Request, exc: OSError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ImportError)
    async def import_error_handler(request: Request, exc: ImportError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ModuleNotFoundError)
    async def module_not_found_error_handler(request: Request, exc: ModuleNotFoundError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ZeroDivisionError)
    async def zero_division_error_handler(request: Request, exc: ZeroDivisionError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(LookupError)
    async def lookup_error_handler(request: Request, exc: LookupError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(NameError)
    async def name_error_handler(request: Request, exc: NameError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(UnboundLocalError)
    async def unbound_local_error_handler(request: Request, exc: UnboundLocalError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ArithmeticError)
    async def arithmetic_error_handler(request: Request, exc: ArithmeticError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(EnvironmentError)
    async def environment_error_handler(request: Request, exc: EnvironmentError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(IOError)
    async def io_error_handler(request: Request, exc: IOError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(ReferenceError)
    async def reference_error_handler(request: Request, exc: ReferenceError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(SyntaxError)
    async def syntax_error_handler(request: Request, exc: SyntaxError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(RecursionError)
    async def recursion_error_handler(request: Request, exc: RecursionError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(OverflowError)
    async def overflow_error_handler(request: Request, exc: OverflowError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(MemoryError)
    async def memory_error_handler(request: Request, exc: MemoryError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(SystemError)
    async def system_error_handler(request: Request, exc: SystemError) -> JSONResponse:
        return await _generic_error_response(request, exc)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Fallback catch-all handler for any unhandled exception.

        Security note: The exception type name is included in the response body
        (details.type) so clients can distinguish error categories without leaking
        internal stack traces or messages.

        Note: In Starlette 1.0.0+ this handler is routed through
        ``ServerErrorMiddleware`` which always re-raises the exception after
        sending the response.  The specific handlers above avoid this behaviour
        in test ASGI mode.
        """
        return await _generic_error_response(request, exc)


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
