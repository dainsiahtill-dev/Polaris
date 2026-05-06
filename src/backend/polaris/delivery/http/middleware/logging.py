"""API Request Logging Middleware

Provides structured request/response logging with timing information.

Configuration:
- KERNELONE_LOG_REQUESTS: Enable/disable request logging (default: true)
- KERNELONE_LOG_REQUEST_BODY: Log request bodies (default: false for security)
- KERNELONE_LOG_RESPONSE_BODY: Log response bodies (default: false)
- KERNELONE_LOG_SLOW_REQUEST_MS: Threshold for slow request warning (default: 1000)
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from polaris.delivery.http.endpoint_policy import is_observability_exempt
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    # Paths to exclude from detailed logging
    EXCLUDED_PATHS = frozenset({"/health", "/metrics", "/favicon.ico"})

    # Sensitive headers that should be masked
    SENSITIVE_HEADERS = {
        "authorization",
        "cookie",
        "x-api-key",
        "api-key",
    }

    def __init__(
        self,
        app: ASGIApp,
        log_requests: bool | None = None,
        log_request_body: bool | None = None,
        log_response_body: bool | None = None,
        slow_request_ms: float = 1000.0,
    ) -> None:
        super().__init__(app)

        # Explicit parameters take precedence over environment variables.
        env_log_requests = os.environ.get("KERNELONE_LOG_REQUESTS", "true").lower() not in ("false", "0", "no", "off")
        self._log_requests = log_requests if log_requests is not None else env_log_requests

        env_log_request_body = os.environ.get("KERNELONE_LOG_REQUEST_BODY", "false").lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        self._log_request_body = log_request_body if log_request_body is not None else env_log_request_body

        env_log_response_body = os.environ.get("KERNELONE_LOG_RESPONSE_BODY", "false").lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        self._log_response_body = log_response_body if log_response_body is not None else env_log_response_body

        self._slow_request_ms = float(os.environ.get("KERNELONE_LOG_SLOW_REQUEST_MS", str(slow_request_ms)))

        if self._log_request_body:
            logger.warning(
                "[SECURITY] Request body logging is enabled. "
                "Sensitive data (API keys, credentials, user input) may be written to logs. "
                "Only enable for debugging with controlled access."
            )
        if self._log_response_body:
            logger.warning(
                "[SECURITY] Response body logging is enabled. "
                "Sensitive data may be written to logs. "
                "Only enable for debugging with controlled access."
            )

    def _should_log_path(self, path: str) -> bool:
        """Check if path should be logged."""
        return not is_observability_exempt(path)

    def _mask_sensitive_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Mask sensitive header values."""
        masked = {}
        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower in self.SENSITIVE_HEADERS:
                masked[key] = "***REDACTED***"
            else:
                masked[key] = value
        return masked

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        # Check for forwarded headers
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-Ip")
        if real_ip:
            return real_ip.strip()

        if request.client:
            return str(request.client.host)

        return "unknown"

    def _format_log_entry(
        self,
        request: Request,
        response_status: int,
        duration_ms: float,
        request_body: str | None = None,
        response_body: str | None = None,
    ) -> dict[str, Any]:
        """Format structured log entry."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": "WARNING" if response_status >= 400 else "INFO",
            "method": request.method,
            "path": unquote(str(request.url.path)),
            "query": unquote(str(request.query_params)) if request.query_params else None,
            "status_code": response_status,
            "duration_ms": round(duration_ms, 2),
            "client_ip": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent"),
        }

        # Add request ID if present
        request_id = request.headers.get("X-Request-ID")
        if request_id:
            entry["request_id"] = request_id

        # Add slow request warning
        if duration_ms > self._slow_request_ms:
            entry["slow_request"] = True
            entry["level"] = "WARNING"

        # Add error details for failed requests
        if response_status >= 400:
            entry["error"] = True

        # Optionally include bodies
        if self._log_request_body and request_body:
            entry["request_body"] = request_body[:1000]  # Limit size

        if self._log_response_body and response_body:
            entry["response_body"] = response_body[:1000]  # Limit size

        return entry

    async def dispatch(self, request: Request, call_next) -> Any:
        """Process request with logging."""
        if not self._log_requests:
            return await call_next(request)

        # Skip excluded paths
        if not self._should_log_path(request.url.path):
            return await call_next(request)

        start_time = time.time()

        # Read request body if needed
        request_body = None
        if self._log_request_body and request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                request_body = body.decode("utf-8", errors="replace") if body else None
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] logging.py silent exception", exc)

        # Process request
        try:
            response = await call_next(request)
        except (RuntimeError, ValueError) as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Request failed: {request.method} {request.url.path} - {e}",
                extra={
                    "method": request.method,
                    "path": str(request.url.path),
                    "duration_ms": duration_ms,
                    "error": str(e),
                },
            )
            raise

        duration_ms = (time.time() - start_time) * 1000

        # Format and log
        log_entry = self._format_log_entry(
            request=request,
            response_status=response.status_code,
            duration_ms=duration_ms,
            request_body=request_body,
        )

        # Log based on severity
        if log_entry.get("slow_request"):
            logger.warning(
                f"Slow request: {request.method} {request.url.path} took {duration_ms:.1f}ms", extra=log_entry
            )
        elif response.status_code >= 500:
            logger.error(
                f"Server error: {request.method} {request.url.path} returned {response.status_code}", extra=log_entry
            )
        elif response.status_code >= 400:
            logger.warning(
                f"Client error: {request.method} {request.url.path} returned {response.status_code}", extra=log_entry
            )
        else:
            logger.info(
                f"{request.method} {request.url.path} {response.status_code} ({duration_ms:.1f}ms)", extra=log_entry
            )

        # Add response headers
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

        return response


def get_logging_middleware(
    app: ASGIApp,
    log_requests: bool | None = None,
    log_request_body: bool | None = None,
    log_response_body: bool | None = None,
    slow_request_ms: float | None = None,
) -> RequestLoggingMiddleware:
    """Factory function to create logging middleware with config from environment.

    Args:
        app: The ASGI application
        log_requests: Enable request logging (overrides environment)
        log_request_body: Log request bodies (overrides environment)
        log_response_body: Log response bodies (overrides environment)
        slow_request_ms: Slow request threshold in ms (overrides environment)

    Returns:
        Configured RequestLoggingMiddleware instance
    """
    return RequestLoggingMiddleware(
        app=app,
        log_requests=log_requests,
        log_request_body=log_request_body,
        log_response_body=log_response_body,
        slow_request_ms=slow_request_ms if slow_request_ms is not None else 1000.0,
    )
