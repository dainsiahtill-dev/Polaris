"""Audit Context Middleware for FastAPI.

Extracts or generates trace IDs from request headers (X-Trace-ID, X-Run-ID, X-Task-ID)
and establishes the UnifiedAuditContext scope for the request lifecycle.

Configuration:
- POLARIS_AUDIT_CONTEXT_ENABLED: Enable/disable audit context (default: true)

Usage:
    from polaris.delivery.http.middleware.audit_context import (
        get_audit_context_middleware,
    )
    app.add_middleware(get_audit_context_middleware)
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


def _generate_trace_id() -> str:
    """Generate a standardized trace_id (16-char hex)."""
    return uuid.uuid4().hex[:16]


def _generate_run_id() -> str:
    """Generate a run_id (UUID format)."""
    return str(uuid.uuid4())


def _generate_task_id() -> str:
    """Generate a task_id (UUID format)."""
    return str(uuid.uuid4())


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Middleware for establishing unified audit context from request headers.

    Extracts X-Trace-ID, X-Run-ID, X-Task-ID from request headers.
    If not present, auto-generates them.
    Sets the UnifiedAuditContext scope that propagates to all async handlers.
    """

    # Paths to exclude from audit context
    EXCLUDED_PATHS = {
        "/health",
        "/metrics",
        "/favicon.ico",
    }

    def __init__(
        self,
        app: ASGIApp,
        enabled: bool | None = None,
    ) -> None:
        super().__init__(app)
        # Explicit parameter takes precedence over environment variable
        env_enabled = os.environ.get("POLARIS_AUDIT_CONTEXT_ENABLED", "true").lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        self._enabled = enabled if enabled is not None else env_enabled

        if not self._enabled:
            logger.info("Audit context middleware is disabled via environment")

    def _should_setup_context(self, path: str) -> bool:
        """Check if path should have audit context setup."""
        return not any(path.startswith(excluded) for excluded in self.EXCLUDED_PATHS)

    def _extract_or_generate_ids(
        self,
        request: Request,
    ) -> tuple[str, str, str]:
        """Extract IDs from headers or generate if not present.

        Returns:
            Tuple of (trace_id, run_id, task_id)
        """
        # Extract from headers, generate if not present
        trace_id = request.headers.get("X-Trace-ID", "")
        if not trace_id:
            trace_id = _generate_trace_id()

        run_id = request.headers.get("X-Run-ID", "")
        if not run_id:
            run_id = _generate_run_id()

        task_id = request.headers.get("X-Task-ID", "")
        if not task_id:
            task_id = _generate_task_id()

        return trace_id, run_id, task_id

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process request with audit context setup."""
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if not self._should_setup_context(path):
            return await call_next(request)

        # Extract or generate IDs
        trace_id, run_id, task_id = self._extract_or_generate_ids(request)

        # Import here to avoid circular imports at module level
        from polaris.kernelone.audit.omniscient.context_manager import (
            UnifiedContextFactory,
            audit_context_scope,
            set_audit_context,
        )

        # Create and set the audit context
        ctx = UnifiedContextFactory.create(
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
        )
        set_audit_context(ctx)

        # Use async context manager for proper cleanup
        async with audit_context_scope(
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
        ):
            # Process request - context propagates to all async child tasks
            response = await call_next(request)

        # Add trace headers to response
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Run-ID"] = run_id
        response.headers["X-Task-ID"] = task_id

        return response


def get_audit_context_middleware(
    app: ASGIApp,
    enabled: bool | None = None,
) -> AuditContextMiddleware:
    """Factory function to create audit context middleware with config from environment.

    Args:
        app: The ASGI application
        enabled: Enable/disable (overrides environment)

    Returns:
        Configured AuditContextMiddleware instance
    """
    return AuditContextMiddleware(app=app, enabled=enabled)
