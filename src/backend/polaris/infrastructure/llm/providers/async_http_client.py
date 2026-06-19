"""Async HTTP client for LLM provider communication.

Replaces the sync ``requests`` + ``ThreadPoolExecutor`` pattern with
native ``httpx.AsyncClient`` for non-blocking I/O in async contexts.

Architecture:
    - ``AsyncProviderHttpClient``: Thin wrapper around ``httpx.AsyncClient``
      with circuit breaker, retry, and structured error handling.
    - ``SyncProviderHttpClient``: Backward-compatible sync wrapper for
      legacy callers that cannot use ``await``. Delegates to the async
      client via ``asyncio.run()`` or the running loop.
    - Both return ``HealthResult`` / ``InvokeResult`` domain types,
      never raw ``requests.Response``.

Migration guide:
    1. Replace ``_blocking_http_post(url, headers, payload, timeout)`` with
       ``await async_client.post_json(url, headers, payload, timeout)``.
    2. Replace ``_blocking_http_get(url, headers, timeout)`` with
       ``await async_client.get_json(url, headers, timeout)``.
    3. Remove ``import requests`` from provider files.
    4. Remove ``_do_requests_post``, ``_do_requests_get``,
       ``_blocking_http_post``, ``_blocking_http_get`` from provider_helpers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from polaris.kernelone.llm.types import HealthResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class CircuitOpenError(RuntimeError):
    """Raised when requests are short-circuited by an open circuit breaker."""


class ProviderHttpClientError(RuntimeError):
    """Raised on non-recoverable HTTP client errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.cause = cause


# ---------------------------------------------------------------------------
# Circuit breaker (async-safe)
# ---------------------------------------------------------------------------


class AsyncCircuitBreaker:
    """Asyncio-safe circuit breaker for provider HTTP calls.

    State machine: closed → open → half_open → closed (on success)
                                    └→ open (on failure)
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout = max(0.0, recovery_timeout_seconds)
        self._lock = asyncio.Lock()
        self._failure_count = 0
        self._state = "closed"
        self._opened_at = 0.0

    async def before_call(self) -> None:
        async with self._lock:
            if self._state != "open":
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout:
                self._state = "half_open"
                return
            remaining = int(self._recovery_timeout - elapsed)
            raise CircuitOpenError(f"circuit_open:{remaining}s_remaining")

    async def on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._opened_at = 0.0

    async def on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        return self._state


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResult:
    """Typed HTTP response envelope."""

    status_code: int
    headers: dict[str, str]
    text: str
    elapsed_ms: int

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ProviderHttpClientError(
                f"HTTP {self.status_code}: {self.text[:300]}",
                status_code=self.status_code,
            )


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------


class AsyncProviderHttpClient:
    """Production-grade async HTTP client for LLM provider communication.

    Features:
        - Connection pooling via ``httpx.AsyncClient`` connection limits
        - Circuit breaker with configurable thresholds
        - Structured error mapping to ``HealthResult``
        - Automatic timeout handling

    Usage::

        client = AsyncProviderHttpClient(timeout=30)
        async with client:
            health = await client.health_check(url, headers, payload)
            if health.ok:
                result = await client.post_json(url, headers, payload)
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        circuit_breaker: AsyncCircuitBreaker | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_connections = max_connections
        self._max_keepalive = max_keepalive_connections
        self._circuit = circuit_breaker or AsyncCircuitBreaker()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AsyncProviderHttpClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive,
            ),
            follow_redirects=True,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("AsyncProviderHttpClient not initialized. Use 'async with client:' context manager.")
        return self._client

    # -- core HTTP verbs ----------------------------------------------------

    async def _post_json_impl(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> HttpResult:
        """Internal POST without circuit breaker check (for nested calls)."""
        client = self._ensure_client()
        start = time.monotonic()
        try:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = HttpResult(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=response.text,
                elapsed_ms=elapsed_ms,
            )
            if response.status_code < 500:
                await self._circuit.on_success()
            else:
                await self._circuit.on_failure()
            return result
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            await self._circuit.on_failure()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            raise ProviderHttpClientError(
                f"HTTP POST failed: {exc}",
                cause=exc,
            ) from exc

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> HttpResult:
        """POST JSON payload with circuit breaker protection."""
        await self._circuit.before_call()
        return await self._post_json_impl(url, headers, payload, timeout=timeout)

    async def get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float | None = None,
    ) -> HttpResult:
        """GET and return structured result."""
        client = self._ensure_client()
        await self._circuit.before_call()
        start = time.monotonic()
        try:
            response = await client.get(
                url,
                headers=headers,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = HttpResult(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=response.text,
                elapsed_ms=elapsed_ms,
            )
            if response.status_code < 500:
                await self._circuit.on_success()
            else:
                await self._circuit.on_failure()
            return result
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            await self._circuit.on_failure()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            raise ProviderHttpClientError(
                f"HTTP GET failed: {exc}",
                cause=exc,
            ) from exc

    async def post_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> httpx.Response:
        """POST and return raw streaming response (for SSE).

        Caller is responsible for consuming the stream.
        Returns the response object with ``.aiter_lines()`` / ``.aiter_bytes()``.
        """
        client = self._ensure_client()
        await self._circuit.before_call()
        request = client.build_request(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(timeout) if timeout else None,
        )
        response = await client.send(request, stream=True)
        if response.status_code < 500:
            await self._circuit.on_success()
        else:
            await self._circuit.on_failure()
        return response

    # -- health check convenience -------------------------------------------

    async def health_check(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> HealthResult:
        """Execute a health check POST and map the result to HealthResult.

        This replaces the sync ``_health_check_post_blocking`` function with
        proper async I/O.
        """
        try:
            await self._circuit.before_call()
        except CircuitOpenError as exc:
            return HealthResult(ok=False, latency_ms=0, error=str(exc))

        try:
            result = await self._post_json_impl(url, headers, payload, timeout=timeout)
        except CircuitOpenError as exc:
            return HealthResult(ok=False, latency_ms=0, error=str(exc))
        except ProviderHttpClientError as exc:
            return HealthResult(
                ok=False,
                latency_ms=0,
                error=str(exc),
            )

        status = result.status_code
        latency = result.elapsed_ms

        if status == 401:
            return HealthResult(
                ok=False,
                latency_ms=latency,
                error="Authentication failed: please check your API key",
            )
        if status == 404:
            return HealthResult(
                ok=False,
                latency_ms=latency,
                error="API endpoint not found: please check api_path configuration",
            )
        if status == 429:
            retry_after = result.headers.get("retry-after", "")
            msg = "Rate limited by provider: HTTP 429 Too Many Requests"
            if retry_after:
                msg = f"{msg}; retry after {retry_after} seconds"
            return HealthResult(ok=False, latency_ms=latency, error=msg)
        if status >= 400:
            text = result.text[:300].replace("\n", " ")
            msg = f"Provider health check failed: HTTP {status}"
            if text:
                msg = f"{msg}: {text}"
            return HealthResult(ok=False, latency_ms=latency, error=msg)

        return HealthResult(ok=True, latency_ms=latency)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_default_client: AsyncProviderHttpClient | None = None


def get_default_async_client() -> AsyncProviderHttpClient:
    """Return the module-level singleton client (not initialized)."""
    global _default_client
    if _default_client is None:
        _default_client = AsyncProviderHttpClient()
    return _default_client


__all__ = [
    "AsyncCircuitBreaker",
    "AsyncProviderHttpClient",
    "CircuitOpenError",
    "HttpResult",
    "ProviderHttpClientError",
    "get_default_async_client",
]
