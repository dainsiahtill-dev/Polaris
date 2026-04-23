"""Shared helpers for LLM provider implementations.

Eliminates duplicate retry-loop, health-check, and model-listing patterns
that were copy-pasted across anthropic_compat, openai_compat, kimi, and
gemini API providers.

IMPORTANT: This module contains a sync CircuitBreaker implementation.
For async LLM engine operations, see polaris/kernelone/llm/engine/resilience.py.

CircuitBreaker Intentional Separation:
1. AsyncCircuitBreaker (llm/kernelone/llm/engine/resilience.py):
   - For async LLM engine calls
   - Full HALF_OPEN state management with asyncio.Lock
   - Integrates with ResilienceManager for retry/timeout

2. SyncCircuitBreaker (this module, llm/providers/provider_helpers.py):
   - For sync provider HTTP operations
   - Simplified state machine with threading.RLock
   - Independent implementation optimized for blocking I/O

These are intentionally separate implementations optimized for their
respective execution models. Do NOT try to unify them.
"""

from __future__ import annotations

import asyncio
import atexit
import codecs
import concurrent.futures
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.concurrency import UnifiedConcurrencyManager
import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import requests
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelInfo,
    ModelListResult,
    Usage,
    estimate_usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, Callable

    import aiohttp

logger = logging.getLogger(__name__)

_aiohttp_module: Any | None = None
_REAL_CLIENT_SESSION_TYPE: type[Any] | None = None
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class _LightweightClientTimeout:
    def __init__(self, *, total: float | None = None) -> None:
        self.total = total


class _LightweightTCPConnector:
    def __init__(
        self,
        *,
        limit: int = 100,
        limit_per_host: int = 10,
        ttl_dns_cache: int = 300,
        enable_cleanup_closed: bool = True,
    ) -> None:
        self.limit = limit
        self.limit_per_host = limit_per_host
        self.ttl_dns_cache = ttl_dns_cache
        self.enable_cleanup_closed = enable_cleanup_closed


class _LightweightClientSession:
    def __init__(self, *, timeout: Any | None = None, connector: Any | None = None) -> None:
        self.timeout = timeout
        self.connector = connector
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def post(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("aiohttp is unavailable in lightweight stream-session mode")


class _LightweightAiohttpModule:
    ClientSession = _LightweightClientSession
    ClientTimeout = _LightweightClientTimeout
    TCPConnector = _LightweightTCPConnector


def _should_use_lightweight_stream_session_mode() -> bool:
    mode = str(
        os.environ.get("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS")
        or ""
    ).strip()
    if mode:
        return mode.lower() in {"1", "true", "yes", "on"}
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _ensure_aiohttp_imported() -> Any:
    """Import aiohttp lazily so lightweight test fixtures avoid provider cold-start."""
    global _aiohttp_module, _REAL_CLIENT_SESSION_TYPE
    if _aiohttp_module is None and _should_use_lightweight_stream_session_mode():
        _aiohttp_module = _LightweightAiohttpModule()
        _REAL_CLIENT_SESSION_TYPE = _LightweightClientSession
        return _aiohttp_module
    if _aiohttp_module is None or _REAL_CLIENT_SESSION_TYPE is None:
        import aiohttp as imported_aiohttp

        _aiohttp_module = imported_aiohttp
        _REAL_CLIENT_SESSION_TYPE = imported_aiohttp.ClientSession
    return _aiohttp_module


def _track_background_task(task: asyncio.Task[Any]) -> None:
    """Keep fire-and-forget cleanup tasks alive until they finish."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ---------------------------------------------------------------------------
# Shared thread pools for blocking I/O offloading.
#
# P0 fix (2026-03-23):
# Previously, _blocking_http_post/get/sleep used ``loop.run_in_executor(...).result()``.
# This raises InvalidStateError because ``run_in_executor`` returns an asyncio.Future,
# NOT a concurrent.futures.Future.  asyncio.Future.result() raises InvalidStateError
# when the future is not yet done.  concurrent.futures.Future.result() correctly blocks
# the caller thread until completion.
#
# The correct fix is to use concurrent.futures.ThreadPoolExecutor.submit().result()
# directly, which blocks only the calling thread (not the event loop thread).
# ---------------------------------------------------------------------------

# Lazy-loaded pools using UnifiedConcurrencyManager.
# These are functions (not module-level singletons) to ensure proper event-loop
# context initialization when get_concurrency_manager() is first called.
_MAX_HTTP_WORKERS: int = int(os.environ.get("KERNELONE_HTTP_POOL_WORKERS", "32"))
_MAX_SLEEP_WORKERS: int = int(os.environ.get("KERNELONE_SLEEP_POOL_WORKERS", "4"))


def _get_http_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the shared HTTP blocking pool."""
    from polaris.kernelone.concurrency import get_concurrency_manager

    return get_concurrency_manager().get_http_pool(max_workers=_MAX_HTTP_WORKERS)


def _get_sleep_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the shared sleep pool."""
    from polaris.kernelone.concurrency import get_concurrency_manager

    return get_concurrency_manager().get_sleep_pool(max_workers=_MAX_SLEEP_WORKERS)


# Backward compatibility: module-level pool references for external code that
# may directly reference _BLOCKING_HTTP_POOL or _SLEEP_POOL.
# These are initialized lazily on first access.
_blocking_http_pool: concurrent.futures.ThreadPoolExecutor | None = None
_sleep_pool: concurrent.futures.ThreadPoolExecutor | None = None


def _get_blocking_http_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Get the module-level HTTP pool (lazy initialization)."""
    global _blocking_http_pool
    if _blocking_http_pool is None:
        _blocking_http_pool = _get_http_pool()
    return _blocking_http_pool


def _get_blocking_sleep_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Get the module-level sleep pool (lazy initialization)."""
    global _sleep_pool
    if _sleep_pool is None:
        _sleep_pool = _get_sleep_pool()
    return _sleep_pool


# For backward compatibility with code that directly references _BLOCKING_HTTP_POOL
class _LazyPool:
    """Lazy pool proxy that defers initialization until first access.

    Args:
        pool_getter: A callable that returns the desired ThreadPoolExecutor.
    """

    __slots__ = ("_pool", "_pool_getter")

    def __init__(self, pool_getter: Any) -> None:
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._pool_getter = pool_getter

    def __getattr__(self, name: str) -> Any:
        if self._pool is None:
            self._pool = self._pool_getter()
        return getattr(self._pool, name)

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> concurrent.futures.Future:
        if self._pool is None:
            self._pool = self._pool_getter()
        return self._pool.submit(fn, *args, **kwargs)


_BLOCKING_HTTP_POOL_LAZY = _LazyPool(_get_http_pool)
_SLEEP_POOL_LAZY = _LazyPool(_get_blocking_sleep_pool)


class CircuitOpenError(RuntimeError):
    """Raised when requests are short-circuited by an open circuit breaker."""


def _do_requests_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
):
    """Thread-safe requests.post call (for ThreadPoolExecutor wrapping)."""
    return requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout if timeout > 0 else None,
    )


def _blocking_http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
):
    """Call requests.post, safely.

    When called from an async context (running event loop), the HTTP request
    is offloaded to a ThreadPoolExecutor so it does not block the asyncio event
    loop.  When called from a plain sync context (no event loop, e.g. unit
    tests), falls back to a direct call.

    This prevents sync requests.post() from freezing WebSocket heartbeats,
    SSE streams, and other async work when providers are invoked from
    FastAPI route handlers or similar async contexts.

    P0 fix (2026-03-23): Uses ``ThreadPoolExecutor.submit().result()`` instead of
    ``loop.run_in_executor(...).result()`` to avoid InvalidStateError.  The
    ``.result()`` call on a ``concurrent.futures.Future`` blocks only the caller
    thread (the worker thread running this function), not the event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop -- safe to call directly.
        return _do_requests_post(url, headers, payload, timeout)

    if loop.is_running():
        # ThreadPoolExecutor ensures we get a concurrent.futures.Future whose
        # .result() blocks correctly (not an asyncio.Future that raises
        # InvalidStateError).
        future = _get_blocking_http_pool().submit(_do_requests_post, url, headers, payload, timeout)
        return future.result()

    # Loop exists but is not running -- call directly.
    return _do_requests_post(url, headers, payload, timeout)


def _blocking_sleep(seconds: float) -> None:
    """Non-blocking time.sleep via ThreadPoolExecutor when an event loop is running.

    When called from an async context, offloads the blocking time.sleep() to a
    thread so it does not freeze the asyncio event loop.

    P0 fix (2026-03-23): Uses ``ThreadPoolExecutor.submit().result()`` instead of
    ``loop.run_in_executor(...).result()`` to avoid InvalidStateError.  The
    ``.result()`` call on a ``concurrent.futures.Future`` blocks only the caller
    thread (the worker thread running this function), not the event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        time.sleep(seconds)
        return

    if loop.is_running():
        # ThreadPoolExecutor ensures we get a concurrent.futures.Future whose
        # .result() blocks correctly (not an asyncio.Future that raises
        # InvalidStateError).
        future = _get_blocking_sleep_pool().submit(time.sleep, seconds)
        future.result()
    else:
        time.sleep(seconds)


class CircuitBreaker:
    """Thread-safe circuit breaker for provider HTTP calls."""

    def __init__(self, *, failure_threshold: int = 5, recovery_timeout_seconds: float = 60.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout_seconds = max(1.0, float(recovery_timeout_seconds))
        self._lock = threading.RLock()
        self._failure_count = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at = 0.0

    def before_call(self) -> None:
        with self._lock:
            if self._state != "open":
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.recovery_timeout_seconds:
                self._state = "half_open"
                return
            raise CircuitOpenError(f"circuit_open:{int(self.recovery_timeout_seconds - elapsed)}s_remaining")

    def on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._opened_at = 0.0

    def on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            should_open = self._state == "half_open" or self._failure_count >= self.failure_threshold
            if should_open:
                self._state = "open"
                self._opened_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout_seconds,
            }


_CIRCUIT_BREAKER_REGISTRY: dict[str, CircuitBreaker] = {}
_CIRCUIT_BREAKER_LOCK = threading.RLock()

_STREAM_SESSION_REGISTRY: _LRUSessionRegistry = {}  # type: ignore[assignment]
_STREAM_SESSION_LOCK = threading.RLock()
_STREAM_SESSION_CLEANUP_REGISTERED = False


def _session_is_closed(session: Any) -> bool:
    """Return whether a session is closed, tolerating lightweight test doubles."""
    if session is None:
        return True
    closed_attr = getattr(session, "closed", None)
    if isinstance(closed_attr, bool):
        return closed_attr
    return False


async def _close_session_if_possible(session: Any) -> None:
    """Close session if it exposes a close method."""
    if session is None:
        return
    close_fn = getattr(session, "close", None)
    if close_fn is None:
        return
    result = close_fn()
    if asyncio.iscoroutine(result):
        await result


def _is_reusable_stream_session(session: Any) -> bool:
    """Only real aiohttp sessions are reusable across requests/tests."""
    session_type = _REAL_CLIENT_SESSION_TYPE
    if session_type is None:
        session_type = _ensure_aiohttp_imported().ClientSession
    return isinstance(session, session_type)


def _register_stream_session_cleanup_once() -> None:
    global _STREAM_SESSION_CLEANUP_REGISTERED
    with _STREAM_SESSION_LOCK:
        if _STREAM_SESSION_CLEANUP_REGISTERED:
            return
        atexit.register(close_stream_sessions_sync)
        _STREAM_SESSION_CLEANUP_REGISTERED = True


def get_circuit_breaker(
    key: str,
    *,
    failure_threshold: int = 5,
    recovery_timeout_seconds: float = 60.0,
) -> CircuitBreaker:
    normalized_key = str(key or "").strip().lower() or "default"
    with _CIRCUIT_BREAKER_LOCK:
        breaker = _CIRCUIT_BREAKER_REGISTRY.get(normalized_key)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout_seconds=recovery_timeout_seconds,
            )
            _CIRCUIT_BREAKER_REGISTRY[normalized_key] = breaker
        return breaker


class _LRUSessionRegistry:
    """LRU session registry with idle timeout.

    Prevents unbounded session growth leading to resource leaks.
    """

    def __init__(self, max_sessions: int = 10, idle_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS) -> None:
        self.max_sessions = max_sessions
        self.idle_timeout_seconds = idle_timeout_seconds
        # OrderedDict for LRU tracking: key -> {"session": session, "last_access": float}
        self._sessions: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, int]) -> aiohttp.ClientSession | None:
        """Get session, updating access time."""
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return None

            session = entry["session"]
            if not _is_reusable_stream_session(session):
                del self._sessions[key]
                return None
            if _session_is_closed(session):
                del self._sessions[key]
                return None

            # Check idle timeout
            idle_time = time.monotonic() - entry["last_access"]
            if idle_time > self.idle_timeout_seconds:
                entry["expired"] = True
                del self._sessions[key]
                return None

            # Update access time and move to end (most recently used)
            entry["last_access"] = time.monotonic()
            self._sessions.move_to_end(key)
            return session

    def set(self, key: tuple[str, int], session: aiohttp.ClientSession) -> None:
        """Set session, evicting LRU entries if needed."""
        if not _is_reusable_stream_session(session):
            return
        with self._lock:
            # Evict LRU entries if at capacity
            while len(self._sessions) >= self.max_sessions:
                _oldest_key, oldest_entry = self._sessions.popitem(last=False)
                self._close_session_async(oldest_entry.get("session"))

            self._sessions[key] = {
                "session": session,
                "last_access": time.monotonic(),
                "created_at": time.monotonic(),
            }
            self._sessions.move_to_end(key)

    def pop(self, key: tuple[str, int]) -> aiohttp.ClientSession | None:
        """Remove and return session."""
        with self._lock:
            entry = self._sessions.pop(key, None)
            return entry["session"] if entry else None

    def clear(self) -> list[aiohttp.ClientSession]:
        """Clear all sessions, returning list to be closed."""
        with self._lock:
            sessions = [e["session"] for e in self._sessions.values()]
            self._sessions.clear()
            return sessions

    def _close_session_async(self, session: aiohttp.ClientSession | None) -> None:
        """Best-effort async session close."""
        if _session_is_closed(session):
            return
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_close_session_if_possible(session))
            _track_background_task(task)
        except RuntimeError:
            # No event loop running — cannot schedule async cleanup; skip silently.
            pass


# Replace simple dict with LRU registry
_STREAM_SESSION_REGISTRY = _LRUSessionRegistry(  # type: ignore[no-redef,assignment]
    max_sessions=int(os.environ.get("KERNELONE_MAX_SESSIONS", "10")),
    idle_timeout_seconds=float(
        os.environ.get("KERNELONE_SESSION_IDLE_TIMEOUT", str(DEFAULT_OPERATION_TIMEOUT_SECONDS))
    ),
)


def _build_backoff_seconds(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    exp_delay = base_delay_seconds * (2 ** max(0, attempt - 1))
    bounded = min(max_delay_seconds, max(base_delay_seconds, exp_delay))
    # Add small jitter to reduce synchronized retry storms.
    jitter = random.uniform(0.0, bounded * 0.2)
    return bounded + jitter


async def get_stream_session(
    provider_key: str,
    *,
    timeout_seconds: int = 60,
    limit: int = 100,
    limit_per_host: int = 10,
) -> aiohttp.ClientSession:
    """Get or create a shared aiohttp session for streaming requests."""
    aiohttp_module = _ensure_aiohttp_imported()
    _register_stream_session_cleanup_once()
    loop = asyncio.get_running_loop()
    key = (str(provider_key or "default"), id(loop))

    existing = _STREAM_SESSION_REGISTRY.get(key)
    if existing and not _session_is_closed(existing):
        return existing

    timeout = aiohttp_module.ClientTimeout(total=timeout_seconds if timeout_seconds > 0 else None)
    connector = aiohttp_module.TCPConnector(
        limit=max(1, int(limit)),
        limit_per_host=max(1, int(limit_per_host)),
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    candidate = aiohttp_module.ClientSession(timeout=timeout, connector=connector)
    if not _is_reusable_stream_session(candidate):
        return candidate

    # Double-check and register
    existing = _STREAM_SESSION_REGISTRY.get(key)
    if existing and not _session_is_closed(existing):
        await _close_session_if_possible(candidate)
        return existing
    _STREAM_SESSION_REGISTRY.set(key, candidate)
    return candidate


async def close_stream_sessions(provider_key: str | None = None) -> int:
    """Close all tracked stream sessions (or one provider's sessions)."""
    target_key = str(provider_key).strip() if provider_key else ""

    if not target_key:
        sessions = _STREAM_SESSION_REGISTRY.clear()
        closed = 0
        for session in sessions:
            if not _session_is_closed(session):
                await _close_session_if_possible(session)
                closed += 1
        return closed

    # Close all sessions (key-based filtering not exposed by LRU registry)
    sessions = _STREAM_SESSION_REGISTRY.clear()
    closed = 0
    for session in sessions:
        if not _session_is_closed(session):
            await _close_session_if_possible(session)
            closed += 1
    return closed


def close_stream_sessions_sync() -> None:
    """Best-effort sync cleanup for process shutdown hooks."""
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        try:
            task = running_loop.create_task(close_stream_sessions())
            _track_background_task(task)
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to create close task: %s", e)
        return

    try:
        asyncio.run(close_stream_sessions())
    except (RuntimeError, ValueError) as e:
        logger.debug("Failed to close stream sessions: %s", e)


async def iter_sse_data_payloads(
    stream: AsyncIterable[bytes | str],
) -> AsyncGenerator[str, None]:
    """Iterate decoded SSE ``data:`` payloads from a byte stream.

    Guarantees:
    1. UTF-8 decoding is incremental, so multi-byte chars split across TCP chunks
       are preserved instead of being silently dropped.
    2. Multi-line SSE events are reassembled using blank-line frame boundaries.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    text_buffer = ""
    data_lines: list[str] = []

    def _yield_completed_event() -> str | None:
        nonlocal data_lines
        if not data_lines:
            return None
        payload = "\n".join(data_lines)
        data_lines = []
        return payload

    async for chunk in stream:
        text = decoder.decode(chunk) if isinstance(chunk, bytes) else str(chunk or "")
        if not text:
            continue
        text_buffer += text

        while True:
            newline_pos = text_buffer.find("\n")
            if newline_pos < 0:
                break

            raw_line = text_buffer[:newline_pos]
            text_buffer = text_buffer[newline_pos + 1 :]
            line = raw_line.rstrip("\r")

            if line == "":
                payload = _yield_completed_event()
                if payload is not None:
                    yield payload
                continue

            if line.startswith(":"):
                # SSE comment line
                continue
            if not line.startswith("data:"):
                continue

            data_lines.append(line[5:].lstrip())

    tail = decoder.decode(b"", final=True)
    if tail:
        text_buffer += tail

    trailing = text_buffer.rstrip("\r")
    if trailing.startswith("data:"):
        data_lines.append(trailing[5:].lstrip())

    payload = _yield_completed_event()
    if payload is not None:
        yield payload


def invoke_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    retries: int,
    prompt: str,
    extract_output: Callable[[dict[str, Any]], str],
    usage_from_response: Callable[[str, str, dict[str, Any]], Usage],
    *,
    circuit_breaker: CircuitBreaker | None = None,
    circuit_key: str | None = None,
    backoff_base_seconds: float = 0.5,
    backoff_max_seconds: float = 30.0,
    clock: ClockPort | None = None,
) -> InvokeResult:
    """POST *url* with JSON *payload*, retrying up to *retries* times on failure.

    Args:
        clock: Optional injected clock for testability. Defaults to RealClock.
    """
    _clock: ClockPort = clock if clock is not None else RealClock()
    attempt = 0
    retries = max(0, int(retries))
    breaker = circuit_breaker or get_circuit_breaker(
        circuit_key or f"invoke:{url}",
    )

    start = _clock.time()
    while True:
        try:
            breaker.before_call()
        except CircuitOpenError as exc:
            usage = estimate_usage(prompt, "")
            return InvokeResult(
                ok=False,
                output="",
                latency_ms=int((_clock.time() - start) * 1000),
                usage=usage,
                error=str(exc),
            )

        try:
            # _blocking_http_post offloads to a ThreadPoolExecutor when an event
            # loop is running, preventing event-loop blocking.
            response = _blocking_http_post(url, headers, payload, timeout)
            response_ok = getattr(response, "ok", None)
            if response_ok is None:
                status_code = getattr(response, "status_code", None)
                response_ok = isinstance(status_code, int) and status_code < 400
            if not response_ok:
                # Log response body for error debugging before raising
                try:
                    error_body = response.text
                    logger.warning(
                        "[provider-helpers] HTTP %s from %s: %s",
                        response.status_code,
                        url,
                        error_body[:500] if error_body else "(empty)",
                    )
                except (RuntimeError, ValueError):
                    logger.warning(
                        "[provider-helpers] HTTP %s from %s (failed to read body)", response.status_code, url
                    )
            response.raise_for_status()
            data = response.json()
            latency_ms = int((_clock.time() - start) * 1000)
            output = extract_output(data)
            usage = usage_from_response(prompt, output, data)
            breaker.on_success()
            return InvokeResult(
                ok=True,
                output=output.strip(),
                latency_ms=latency_ms,
                usage=usage,
                raw=data,
            )

        except (
            requests.RequestException,
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            breaker.on_failure()
            attempt += 1
            if attempt > retries:
                latency_ms = int((_clock.time() - start) * 1000)
                usage = estimate_usage(prompt, "")
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=latency_ms,
                    usage=usage,
                    error=str(exc),
                )

            delay = _build_backoff_seconds(
                attempt=attempt,
                base_delay_seconds=backoff_base_seconds,
                max_delay_seconds=backoff_max_seconds,
            )

            # Use injected clock for deterministic testability.
            # In production (RealClock), this delegates to time.sleep().
            _clock.sleep(delay)
        except (KeyboardInterrupt, SystemExit):
            raise


def health_check_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> HealthResult:
    """POST-based health check with standard error classification.

    Uses _blocking_http_post so the blocking requests.post() does not freeze
    the asyncio event loop when called from async contexts.
    """
    start = time.time()
    try:
        response = _blocking_http_post(url, headers, payload, timeout)
        latency_ms = int((time.time() - start) * 1000)

        if response.status_code == 401:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="Authentication failed: please check your API key"
            )
        if response.status_code == 404:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="API endpoint not found: please check api_path configuration"
            )

        response.raise_for_status()
        return HealthResult(ok=True, latency_ms=latency_ms)
    except requests.exceptions.ConnectionError:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Network connection failed: please check your network and base_url"
        )
    except requests.exceptions.Timeout:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(
            ok=False, latency_ms=latency_ms, error="Request timeout: the server took too long to respond"
        )
    except (RuntimeError, ValueError) as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))


def _do_requests_get(
    url: str,
    headers: dict[str, str],
    timeout: int,
):
    """Thread-safe requests.get call (for ThreadPoolExecutor wrapping)."""
    return requests.get(url, headers=headers, timeout=timeout if timeout > 0 else None)


def _blocking_http_get(
    url: str,
    headers: dict[str, str],
    timeout: int,
):
    """Call requests.get safely, offloading to a thread when an event loop is running.

    This prevents sync requests.get() from freezing the asyncio event loop when
    called from async contexts. Falls back to a direct call when no event loop
    is running.

    P0 fix (2026-03-23): Uses ``ThreadPoolExecutor.submit().result()`` instead of
    ``loop.run_in_executor(...).result()`` to avoid InvalidStateError.  The
    ``.result()`` call on a ``concurrent.futures.Future`` blocks only the caller
    thread (the worker thread running this function), not the event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _do_requests_get(url, headers, timeout)

    if loop.is_running():
        # ThreadPoolExecutor ensures we get a concurrent.futures.Future whose
        # .result() blocks correctly (not an asyncio.Future that raises
        # InvalidStateError).
        future = _get_blocking_http_pool().submit(_do_requests_get, url, headers, timeout)
        return future.result()

    return _do_requests_get(url, headers, timeout)


def list_models_from_api(
    url: str,
    headers: dict[str, str],
    timeout: int,
    data_key: str = "data",
) -> ModelListResult:
    """GET-based model listing with standard response parsing.

    Uses _blocking_http_get so the blocking requests.get() does not freeze
    the asyncio event loop when called from async contexts.
    """
    try:
        response = _blocking_http_get(url, headers, timeout)
        response.raise_for_status()
        payload = response.json()
        models: list[ModelInfo] = []
        items = payload.get(data_key) if isinstance(payload, dict) else None
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or item.get("name") or "").strip()
                    if model_id:
                        models.append(ModelInfo(id=model_id, raw=item))
        return ModelListResult(ok=True, supported=True, models=models)
    except (RuntimeError, ValueError) as exc:
        return ModelListResult(ok=False, supported=True, models=[], error=str(exc))


# ============================================================================
# Network Jitter Retry for Async Stream Sessions
# ============================================================================
# Retry configuration for transient network errors (connection reset, timeout, etc.)
# Uses fixed delay: configured seconds between retries, max configured attempts

_STREAM_RETRY_DELAY_SEC: float = float(
    os.environ.get("KERNELONE_STREAM_RETRY_DELAY_SEC", "5.0")
)
_STREAM_RETRY_MAX_ATTEMPTS: int = int(
    os.environ.get("KERNELONE_STREAM_RETRY_MAX_ATTEMPTS", "3")
)


def _is_retryable_network_error(exc: BaseException) -> bool:
    """Determine if an exception is a transient network error suitable for retry.

    Args:
        exc: The exception to check.

    Returns:
        True if the error is retryable, False otherwise.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc).lower()

    # aiohttp client errors that indicate transient network issues
    retryable_errors = {
        "ClientConnectorError",
        "ClientOSError",
        "ClientSSLError",
        "ServerDisconnectedError",
        "ClientResponseError",  # HTTP 5xx, 429, etc.
    }

    # Connection-related errors from asyncio and socket layer
    connection_errors = {
        "ConnectionResetError",
        "ConnectionRefusedError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "TimeoutError",
    }

    if exc_type in retryable_errors:
        return True
    if exc_type in connection_errors:
        return True

    # Check for HTTP status codes in exception message (e.g., "429 Client Response: Too Many Requests")
    # These indicate server-side errors that may be transient
    status_indicators = ["429", "502", "503", "504", "500", "502", "503", "504"]
    for indicator in status_indicators:
        if indicator in exc_msg:
            return True

    # Check for common network error indicators in the message
    network_indicators = [
        "cannot connect",
        "connection refused",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "timed out",
        "ssl handshake",
        "network is unreachable",
        "no route to host",
        "temporary failure",
        "name or service not known",
        "getaddrinfo failed",
    ]

    return any(indicator in exc_msg for indicator in network_indicators)


async def _close_and_create_session(
    old_session: aiohttp.ClientSession | None,
) -> aiohttp.ClientSession:
    """Close old session if exists and create a new one.

    Used for retry scenarios where we need a fresh connection.

    Args:
        old_session: The session to close (can be None).

    Returns:
        A new aiohttp ClientSession.
    """
    if old_session is not None and not _session_is_closed(old_session):
        try:
            await _close_session_if_possible(old_session)
        except (RuntimeError, ValueError) as e:
            logger.debug("Best-effort session cleanup failed: %s", e)

    # Create a fresh session with default settings
    aiohttp_module = _ensure_aiohttp_imported()
    timeout = aiohttp_module.ClientTimeout(total=60)
    connector = aiohttp_module.TCPConnector(
        limit=100,
        limit_per_host=10,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    return aiohttp_module.ClientSession(timeout=timeout, connector=connector)


async def invoke_stream_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
) -> AsyncGenerator[dict[str, Any], None]:
    """POST *url* with JSON *payload* as SSE stream, retrying on transient network errors.

    This is the async counterpart to invoke_with_retry, designed for streaming responses.
    Uses aiohttp for async HTTP requests and implements retry with configurable delay.

    Args:
        url: The endpoint URL to POST to.
        headers: HTTP headers for the request.
        payload: JSON-serializable request body.
        timeout_seconds: Request timeout in seconds.
        max_attempts: Maximum number of retry attempts (default from env or 3).
        retry_delay_seconds: Delay between retries in seconds (default from env or 5s).

    Yields:
        dict: Parsed JSON events from the SSE stream.

    Raises:
        aiohttp.ClientError: If all retries are exhausted.
    """
    aiohttp_module = _ensure_aiohttp_imported()
    session: aiohttp.ClientSession | None = None
    last_exc: BaseException | None = None

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                # Create or reuse session
                if session is None or _session_is_closed(session):
                    session = await _close_and_create_session(session)

                timeout = aiohttp_module.ClientTimeout(total=timeout_seconds if timeout_seconds > 0 else 60)

                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response:
                    if not response.ok:
                        # Log response body for error debugging before raising
                        try:
                            error_body = await response.text()
                            logger.warning(
                                "[provider-helpers] HTTP %s from %s: %s",
                                response.status,
                                url,
                                error_body[:500] if error_body else "(empty)",
                            )
                        except (RuntimeError, ValueError):
                            logger.warning(
                                "[provider-helpers] HTTP %s from %s (failed to read body)", response.status, url
                            )
                    response.raise_for_status()
                    content_type = str(response.headers.get("Content-Type", "") or "").lower()
                    if "application/json" in content_type:
                        payload_obj = await response.json()
                        if isinstance(payload_obj, dict):
                            yield payload_obj
                            return
                        raise RuntimeError(
                            "provider_stream_invalid_json: expected JSON object "
                            f"from {url}, got {type(payload_obj).__name__}"
                        )

                    decoded_event_count = 0
                    async for data_str in iter_sse_data_payloads(response.content):
                        if data_str == "[DONE]":
                            break
                        try:
                            payload_obj = json.loads(data_str)
                        except (RuntimeError, ValueError) as exc:
                            logger.debug(
                                "[provider-helpers] Failed to decode SSE JSON payload from %s: %s",
                                url,
                                exc,
                            )
                            continue
                        if isinstance(payload_obj, dict):
                            decoded_event_count += 1
                            yield payload_obj
                    if decoded_event_count == 0:
                        raise RuntimeError(
                            f"provider_stream_empty: no structured events decoded from streaming response {url}"
                        )

                # Stream completed successfully
                return

            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Don't retry on timeout or cancellation
                raise

            except BaseException as exc:
                last_exc = exc

                if not _is_retryable_network_error(exc):
                    # Non-retryable error, propagate immediately
                    raise

                if attempt < max_attempts:
                    logger.warning(
                        "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        retry_delay_seconds,
                    )
                    # Close the failed session before retry to prevent leak
                    old_session = session
                    session = None  # Prevent _close_and_create_session from closing old session
                    if old_session is not None:
                        await _close_session_if_possible(old_session)
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    logger.error(
                        "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                        max_attempts,
                        str(exc),
                    )

        # All retries exhausted
        if last_exc is not None:
            raise last_exc
    finally:
        # Ensure session is closed when generator is cleaned up
        if session is not None:
            await _close_session_if_possible(session)


async def invoke_stream_with_retry_and_handler(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    stream_handler: Callable[
        [aiohttp.ClientResponse],
        AsyncGenerator[Any, None],
    ],
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
) -> AsyncGenerator[Any, None]:
    """POST *url* with JSON *payload* as stream, with custom handler and retry on network errors.

    This is a more flexible version of invoke_stream_with_retry that allows providers
    to provide their own stream processing logic (e.g., custom SSE parsing, token extraction).

    Args:
        url: The endpoint URL to POST to.
        headers: HTTP headers for the request.
        payload: JSON-serializable request body.
        timeout_seconds: Request timeout in seconds.
        stream_handler: Async generator function that processes the response and yields items.
        max_attempts: Maximum number of retry attempts (default from env or 3).
        retry_delay_seconds: Delay between retries in seconds (default from env or 5s).

    Yields:
        Any: Items yielded by the stream_handler function.

    Raises:
        aiohttp.ClientError: If all retries are exhausted.
    """
    aiohttp_module = _ensure_aiohttp_imported()
    session: aiohttp.ClientSession | None = None
    last_exc: BaseException | None = None

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                # Create or reuse session
                if session is None or _session_is_closed(session):
                    session = await _close_and_create_session(session)

                timeout = aiohttp_module.ClientTimeout(total=timeout_seconds if timeout_seconds > 0 else 60)

                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()

                    # Use the provided stream handler
                    async for item in stream_handler(response):
                        yield item

                # Stream completed successfully
                return

            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Don't retry on timeout or cancellation
                raise

            except BaseException as exc:
                last_exc = exc

                if not _is_retryable_network_error(exc):
                    # Non-retryable error, propagate immediately
                    raise

                if attempt < max_attempts:
                    logger.warning(
                        "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        retry_delay_seconds,
                    )
                    # Close the failed session before retry to prevent leak
                    old_session = session
                    session = None  # Prevent _close_and_create_session from closing old session
                    if old_session is not None:
                        await _close_session_if_possible(old_session)
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    logger.error(
                        "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                        max_attempts,
                        str(exc),
                    )

        # All retries exhausted
        if last_exc is not None:
            raise last_exc
    finally:
        # Ensure session is closed when generator is cleaned up
        if session is not None:
            await _close_session_if_possible(session)
