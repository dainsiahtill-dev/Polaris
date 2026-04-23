"""API Rate Limiting Middleware

Provides IP-based request rate limiting for the API.

Configuration:
- KERNELONE_RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: true)
- KERNELONE_RATE_LIMIT_RPS: Requests per second limit (default: 10)
- KERNELONE_RATE_LIMIT_BURST: Burst allowance (default: 20)
- KERNELONE_RATE_LIMIT_WINDOW: Time window in seconds (default: 60)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


@dataclass
class RateLimitEntry:
    """Rate limit tracking entry for a client."""

    requests: list[float] = field(default_factory=list)
    blocked_until: float = 0.0
    total_violations: int = 0


class RateLimitStore:
    """Thread-safe store for rate limit tracking."""

    def __init__(self, window_seconds: float = 60.0, max_entries: int = 10000) -> None:
        self._window = window_seconds
        self._max_entries = max_entries
        self._store: dict[str, RateLimitEntry] = {}
        self._lock = RLock()

    def _get_client_key(self, request: Request) -> str:
        """Generate unique key for client identification."""
        # Prefer X-Forwarded-For for proxied deployments
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-Ip")
        if real_ip:
            return real_ip.strip()

        client = request.client
        return str(client.host) if client else "unknown"

    def _cleanup_old_entries(self) -> None:
        """Remove expired entries to prevent memory growth."""
        now = time.time()
        expired_keys = [
            key for key, entry in self._store.items() if entry.blocked_until < now and len(entry.requests) == 0
        ]
        for key in expired_keys[:1000]:  # Limit cleanup batch size
            del self._store[key]

    def check_rate_limit(
        self,
        request: Request,
        max_requests: int,
        window_seconds: float,
    ) -> tuple[bool, float, int]:
        """Check if request is within rate limit.

        Returns:
            Tuple of (allowed, retry_after, current_count)
        """
        client_key = self._get_client_key(request)
        now = time.time()

        with self._lock:
            # Cleanup if store is too large
            if len(self._store) > self._max_entries:
                self._cleanup_old_entries()

            entry = self._store.get(client_key)
            if entry is None:
                entry = RateLimitEntry()
                self._store[client_key] = entry

            # Check if currently blocked
            if entry.blocked_until > now:
                return False, entry.blocked_until - now, len(entry.requests)

            # Remove old requests outside the window
            cutoff = now - window_seconds
            entry.requests = [t for t in entry.requests if t > cutoff]

            # Check rate limit
            if len(entry.requests) >= max_requests:
                entry.total_violations += 1
                # Progressive backoff: 30s, 60s, 120s
                block_duration = 30 * (2 ** min(entry.total_violations - 1, 3))
                entry.blocked_until = now + block_duration
                logger.warning(
                    f"Rate limit exceeded for {client_key}. "
                    f"Blocking for {block_duration}s (violation #{entry.total_violations})"
                )
                return False, block_duration, len(entry.requests)

            entry.requests.append(now)
            return True, 0.0, len(entry.requests)

    def reset(self, request: Request | None = None) -> None:
        """Reset rate limit for a client or all clients."""
        with self._lock:
            if request is None:
                self._store.clear()
            else:
                client_key = self._get_client_key(request)
                self._store.pop(client_key, None)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware for FastAPI."""

    def __init__(
        self,
        app: ASGIApp,
        requests_per_second: float = 10.0,
        burst_size: int = 20,
        window_seconds: float = 60.0,
        excluded_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._rps = requests_per_second
        self._burst = burst_size
        self._window = window_seconds
        self._store = RateLimitStore(window_seconds=window_seconds)
        self._excluded_paths = set(excluded_paths or [])
        self._excluded_paths.update(
            [
                "/health",  # Health checks should never be rate limited
                "/metrics",  # Metrics endpoint
            ]
        )

        # Check if disabled via environment
        self._enabled = os.environ.get("KERNELONE_RATE_LIMIT_ENABLED", "true").lower() not in (
            "false",
            "0",
            "no",
            "off",
        )

        if not self._enabled:
            logger.info("Rate limiting is disabled via environment")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with rate limiting."""
        if not self._enabled:
            return await call_next(request)

        # Skip rate limiting for excluded paths
        path = request.url.path
        if any(path.startswith(excluded) for excluded in self._excluded_paths):
            return await call_next(request)

        # Effective per-window capacity derived from RPS and burst.
        effective_limit = max(int(self._rps * self._window), self._burst, 1)

        # Check rate limit
        allowed, retry_after, current_count = self._store.check_rate_limit(
            request,
            max_requests=effective_limit,
            window_seconds=self._window,
        )

        if not allowed:
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": str(int(retry_after)),
                    "X-RateLimit-Limit": str(effective_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + retry_after)),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        remaining = max(0, effective_limit - current_count - 1)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


def get_rate_limit_middleware(
    app: ASGIApp,
    rps: float | None = None,
    burst: int | None = None,
    window: float | None = None,
) -> RateLimitMiddleware:
    """Factory function to create rate limit middleware with config from environment.

    Args:
        app: The ASGI application
        rps: Requests per second (overrides environment)
        burst: Burst size (overrides environment)
        window: Time window in seconds (overrides environment)

    Returns:
        Configured RateLimitMiddleware instance
    """
    rps = rps or float(os.environ.get("KERNELONE_RATE_LIMIT_RPS", "10"))
    burst = burst or int(os.environ.get("KERNELONE_RATE_LIMIT_BURST", "20"))
    window = window or float(os.environ.get("KERNELONE_RATE_LIMIT_WINDOW", "60"))

    logger.info(f"Initializing rate limiting: {rps} RPS, burst={burst}, window={window}s")

    return RateLimitMiddleware(
        app=app,
        requests_per_second=rps,
        burst_size=burst,
        window_seconds=window,
    )
