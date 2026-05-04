"""API Rate Limiting Middleware

Provides IP-based request rate limiting for the API using a token-bucket algorithm.

Configuration:
- KERNELONE_RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: true)
- KERNELONE_RATE_LIMIT_RPS: Requests per second limit (default: 10)
- KERNELONE_RATE_LIMIT_BURST: Burst allowance (default: 20)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


@dataclass
class RateLimitEntry:
    """Token-bucket rate limit tracking entry for a client."""

    tokens: float = 0.0
    last_update: float = 0.0
    blocked_until: float = 0.0
    total_violations: int = 0


class RateLimitStore:
    """Thread-safe token-bucket store for rate limit tracking."""

    def __init__(
        self,
        max_entries: int = 10000,
        trusted_proxies: list[str] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._store: dict[str, RateLimitEntry] = {}
        self._lock = RLock()
        self._trusted_proxies = set(trusted_proxies or [])
        self._trust_x_forwarded_for = bool(self._trusted_proxies)

    def _get_client_key(self, request: Request) -> str:
        """Generate unique key for client identification.

        Only trusts X-Forwarded-For and X-Real-IP headers when the request
        comes from a trusted proxy. Otherwise, falls back to direct client IP.
        """
        client_host = str(request.client.host) if request.client else "unknown"

        if self._trust_x_forwarded_for:
            direct_client_ip = client_host
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # Only trust X-Forwarded-For when the direct connection is from a trusted proxy.
                # Parse the chain from right to left: the rightmost IP is the proxy directly
                # connected to us. Walk backwards skipping trusted proxies; the first untrusted
                # IP is the real client. If all IPs are trusted, return the leftmost (original).
                if direct_client_ip in self._trusted_proxies:
                    chain = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
                    for ip in reversed(chain):
                        if ip not in self._trusted_proxies:
                            return ip
                    return chain[0] if chain else direct_client_ip
                logger.debug(
                    "X-Forwarded-For from untrusted source: client=%s",
                    direct_client_ip,
                )
                return direct_client_ip

            real_ip = request.headers.get("X-Real-Ip")
            if real_ip:
                real_ip_stripped = real_ip.strip()
                if direct_client_ip in self._trusted_proxies:
                    return real_ip_stripped
                logger.debug(
                    "X-Real-IP from untrusted source: real_ip=%s, client=%s",
                    real_ip_stripped,
                    direct_client_ip,
                )

        return client_host

    def _cleanup_old_entries(self, now: float) -> None:
        """Remove expired entries to prevent memory growth."""
        expired_keys = [
            key for key, entry in self._store.items() if entry.blocked_until < now
        ]
        for key in expired_keys[:1000]:  # Limit cleanup batch size
            del self._store[key]

    def _replenish_tokens(self, entry: RateLimitEntry, now: float, rps: float, burst: int) -> None:
        """Add tokens based on elapsed time since last update."""
        elapsed = now - entry.last_update
        if elapsed > 0:
            entry.tokens = min(float(burst), entry.tokens + elapsed * rps)
            entry.last_update = now

    def check_rate_limit(
        self,
        request: Request,
        rps: float,
        burst: int,
    ) -> tuple[bool, float, float]:
        """Token-bucket rate limit check.

        Returns:
            Tuple of (allowed, retry_after, current_tokens)
        """
        client_key = self._get_client_key(request)
        now = time.time()

        with self._lock:
            # Cleanup if store is too large
            if len(self._store) > self._max_entries:
                self._cleanup_old_entries(now)

            entry = self._store.get(client_key)
            if entry is None:
                entry = RateLimitEntry(last_update=now)
                self._store[client_key] = entry

            # Check if currently blocked
            if entry.blocked_until > now:
                return False, entry.blocked_until - now, entry.tokens

            self._replenish_tokens(entry, now, rps, burst)

            if entry.tokens < 1.0:
                entry.total_violations += 1
                # Progressive backoff: 30s, 60s, 120s, 240s
                block_duration = 30 * (2 ** min(entry.total_violations - 1, 3))
                entry.blocked_until = now + block_duration
                logger.warning(
                    "Rate limit exceeded for %s. Blocking for %ss (violation #%d)",
                    client_key,
                    block_duration,
                    entry.total_violations,
                )
                return False, block_duration, entry.tokens

            entry.tokens -= 1.0
            return True, 0.0, entry.tokens

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
        excluded_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._rps = requests_per_second
        self._burst = burst_size
        self._store = RateLimitStore()
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

        # Token-bucket rate limit check (atomic: check + consume under lock)
        allowed, retry_after, tokens = self._store.check_rate_limit(
            request,
            rps=self._rps,
            burst=self._burst,
        )

        if not allowed:
            reset_at = int(time.time() + retry_after)
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": str(int(retry_after)),
                    "X-RateLimit-Limit": str(self._burst),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                },
            )

        # Process request
        try:
            response = await call_next(request)
        except Exception:
            # Re-raise without deducting from remaining count on failure
            raise

        # Add rate limit headers
        remaining = max(0, int(tokens))
        response.headers["X-RateLimit-Limit"] = str(self._burst)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


def get_rate_limit_middleware(
    app: ASGIApp,
    rps: float | None = None,
    burst: int | None = None,
) -> RateLimitMiddleware:
    """Factory function to create rate limit middleware with config from environment.

    Args:
        app: The ASGI application
        rps: Requests per second (overrides environment)
        burst: Burst size (overrides environment)

    Returns:
        Configured RateLimitMiddleware instance
    """
    rps = rps or float(os.environ.get("KERNELONE_RATE_LIMIT_RPS", "10"))
    burst = burst or int(os.environ.get("KERNELONE_RATE_LIMIT_BURST", "20"))

    logger.info("Initializing rate limiting: %s RPS, burst=%s", rps, burst)

    return RateLimitMiddleware(
        app=app,
        requests_per_second=rps,
        burst_size=burst,
    )
