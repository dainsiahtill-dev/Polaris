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
import re
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    pass
import json
import logging
import random
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import TYPE_CHECKING

import requests
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.engine.contracts import get_physical_provider_dispatch_port
from polaris.kernelone.llm.response_parser import FinalizedResponse, LLMResponseParser
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelInfo,
    ModelListResult,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Callable

    import aiohttp
    from polaris.kernelone.llm.engine.contracts import (
        AsyncPhysicalProviderDispatchPort,
        PhysicalProviderDispatchPort,
    )

logger = logging.getLogger(__name__)

HttpTimeout = int | float | tuple[float, float] | None

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
    mode = str(os.environ.get("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS") or "").strip()
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
try:
    _MAX_HTTP_WORKERS: int = int(os.environ.get("KERNELONE_HTTP_POOL_WORKERS", "32"))
except (ValueError, TypeError):
    _MAX_HTTP_WORKERS = 32
try:
    _MAX_SLEEP_WORKERS: int = int(os.environ.get("KERNELONE_SLEEP_POOL_WORKERS", "4"))
except (ValueError, TypeError):
    _MAX_SLEEP_WORKERS = 4


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
    timeout: HttpTimeout,
):
    """Thread-safe requests.post call (for ThreadPoolExecutor wrapping)."""
    timeout_value: HttpTimeout | None = timeout
    if isinstance(timeout, (int, float)) and timeout <= 0:
        timeout_value = None
    return requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout_value,
    )


def _raw_blocking_http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: HttpTimeout,
):
    """Call requests.post without Factory governance; callers must fence it.

    When called from an async context (running event loop), the HTTP request
    is offloaded to a ThreadPoolExecutor so it does not block the asyncio event
    loop.  When called from a plain sync context (no event loop, e.g. unit
    tests), falls back to a direct call.

    This prevents sync requests.post() from freezing WebSocket heartbeats,
    provider data-line streams, and other async work when providers are invoked from
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


def _blocking_http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: HttpTimeout,
):
    """Dispatch one direct sync HTTP attempt through a bound Factory gate."""

    bound_port = get_physical_provider_dispatch_port()
    if bound_port is None:
        return _raw_blocking_http_post(url, headers, payload, timeout)
    if not hasattr(bound_port, "dispatch_sync"):
        raise RuntimeError("bound physical provider dispatch port does not support dispatch_sync")
    physical_dispatch_port = cast("PhysicalProviderDispatchPort", bound_port)
    return physical_dispatch_port.dispatch_sync(
        wire_request={
            "endpoint": url,
            "headers": headers,
            "body": payload,
            "transport": {"kind": "requests.post", "timeout": timeout},
        },
        send=lambda frozen: _raw_blocking_http_post(
            str(frozen["endpoint"]),
            _thaw_physical_dispatch_value(frozen["headers"]),
            _thaw_physical_dispatch_value(frozen["body"]),
            frozen["transport"]["timeout"],
        ),
    )


def _thaw_physical_dispatch_value(value: Any) -> Any:
    """Create the sole mutable transport copy immediately before requests.post."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_physical_dispatch_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_physical_dispatch_value(item) for item in value]
    return value


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
        # 0.0 is a coherent config — "immediately eligible for one half-open
        # trial" (the breaker still enforces single-trial recovery and reopens
        # on a half-open failure). The floor only guards against negatives; an
        # over-aggressive 1.0 floor silently defeated a 0-second recovery and
        # its state-machine tests. No production caller passes < 60.0.
        self.recovery_timeout_seconds = max(0.0, float(recovery_timeout_seconds))
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


async def iter_data_line_payloads(
    stream: AsyncIterable[bytes | str],
) -> AsyncGenerator[str, None]:
    """Iterate decoded provider ``data:`` payloads from a byte stream.

    Guarantees:
    1. UTF-8 decoding is incremental, so multi-byte chars split across TCP chunks
       are preserved instead of being silently dropped.
    2. Multi-line provider events are reassembled using blank-line frame boundaries.
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
                # Provider comment line
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


_CONTEXT_OVERFLOW_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?(\d+) output tokens.*?at least (\d+) input tokens",
    re.DOTALL,
)


def shrink_max_tokens_for_context_overflow(payload: dict[str, Any], error_body: str) -> bool:
    """Self-heal a server-side context-overflow 400 using the SERVER's numbers.

    vLLM rejects requests where prompt + max_tokens exceeds max_model_len and
    reports the exact window/input/output counts. Client-side token estimation
    can never match the server tokenizer exactly (live: a planning payload
    estimated under budget was counted as 8193 by the server, three retries of
    the identical request all failed and killed the run). When the error body
    carries the numbers, recompute max_tokens from the server truth and let
    the caller retry once. Returns True when payload was adjusted.
    """
    match = _CONTEXT_OVERFLOW_RE.search(error_body or "")
    if not match:
        return False
    window, requested_output, reported_input = (int(g) for g in match.groups())
    new_max = window - reported_input - 16
    try:
        current = int(payload.get("max_tokens") or 0)
    except (TypeError, ValueError):
        current = 0
    if new_max < 64 or (current and new_max >= current):
        return False
    payload["max_tokens"] = new_max
    logger.warning(
        "[provider-helpers] context overflow self-heal: window=%s input=%s requested_output=%s -> max_tokens=%s",
        window,
        reported_input,
        requested_output,
        new_max,
    )
    return True


_RATE_LIMIT_MIN_RETRIES = 4


def _rate_limit_min_retries() -> int:
    """429 retry budget, env-tunable for high-saturation conditions.

    Defaults to ``_RATE_LIMIT_MIN_RETRIES``. Raise
    ``KERNELONE_LLM_RATE_LIMIT_MAX_RETRIES`` to ride out longer shared-provider
    saturation (at the cost of slower calls while the provider rate limits).
    """
    raw = os.environ.get("KERNELONE_LLM_RATE_LIMIT_MAX_RETRIES", "").strip()
    if not raw:
        return _RATE_LIMIT_MIN_RETRIES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _RATE_LIMIT_MIN_RETRIES
    return value if value >= 0 else _RATE_LIMIT_MIN_RETRIES


def _parse_retry_after_seconds(response: Any) -> float | None:
    """Best-effort parse of a 429 ``Retry-After`` header into seconds.

    Honors the integer/float-seconds form (the common case for LLM gateways).
    Returns ``None`` when the header is absent or non-numeric, letting the
    caller fall back to exponential backoff.
    """
    headers_value = getattr(response, "headers", None)
    if headers_value is None or not hasattr(headers_value, "get"):
        return None
    raw = str(headers_value.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class _GovernedHttpResponseError(RuntimeError):
    """Carry one non-success physical HTTP response through the attempt gate."""

    def __init__(self, response: Any) -> None:
        self.response = response
        status_code = getattr(response, "status_code", "unknown")
        super().__init__(f"governed HTTP response status {status_code}")


class _GovernedHttpAttemptResult:
    """Parsed result of one successful governed physical HTTP attempt."""

    __slots__ = ("data", "finalized", "latency_ms", "output", "response", "usage")

    def __init__(
        self,
        *,
        response: Any,
        data: dict[str, Any],
        latency_ms: int,
        output: str,
        finalized: FinalizedResponse,
        usage: Usage,
    ) -> None:
        self.response = response
        self.data = data
        self.latency_ms = latency_ms
        self.output = output
        self.finalized = finalized
        self.usage = usage


def _http_response_is_ok(response: Any) -> bool:
    response_ok = getattr(response, "ok", None)
    if response_ok is not None:
        return bool(response_ok)
    status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and status_code < 400


def _send_governed_http_attempt(
    frozen: Mapping[str, Any],
    *,
    prompt: str,
    clock: ClockPort,
    start: float,
    extract_output: Callable[[dict[str, Any]], str],
    usage_from_response: Callable[[str, str, dict[str, Any]], Usage],
) -> _GovernedHttpAttemptResult:
    """Send and parse once inside the physical-attempt lifecycle boundary."""

    response = _raw_blocking_http_post(
        str(frozen["endpoint"]),
        _thaw_physical_dispatch_value(frozen["headers"]),
        _thaw_physical_dispatch_value(frozen["body"]),
        frozen["transport"]["timeout"],
    )
    if not _http_response_is_ok(response):
        raise _GovernedHttpResponseError(response)
    data: dict[str, Any] = response.json()
    # Preserve the legacy latency boundary: JSON parsing is included, while
    # provider extraction, semantic finalization, and usage projection are not.
    latency_ms = int((clock.time() - start) * 1000)
    output = extract_output(data)
    finalized = LLMResponseParser.finalize_response(data, visible_text=output)
    usage = usage_from_response(prompt, finalized.output, data)
    return _GovernedHttpAttemptResult(
        response=response,
        data=data,
        latency_ms=latency_ms,
        output=output,
        finalized=finalized,
        usage=usage,
    )


def invoke_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: HttpTimeout,
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
    physical_dispatch_port: PhysicalProviderDispatchPort | None = None,
) -> InvokeResult:
    """POST *url* with JSON *payload*, retrying up to *retries* times on failure.

    Args:
        clock: Optional injected clock for testability. Defaults to RealClock.
    """
    _clock: ClockPort = clock if clock is not None else RealClock()
    resolved_physical_dispatch_port = physical_dispatch_port
    if resolved_physical_dispatch_port is None:
        bound_port = get_physical_provider_dispatch_port()
        if bound_port is not None:
            if not hasattr(bound_port, "dispatch_sync"):
                raise RuntimeError("bound physical provider dispatch port does not support dispatch_sync")
            resolved_physical_dispatch_port = cast("PhysicalProviderDispatchPort", bound_port)
    attempt = 0
    retries = max(0, int(retries))
    breaker = circuit_breaker or get_circuit_breaker(
        circuit_key or f"invoke:{url}",
    )

    start = _clock.time()
    overflow_heal_attempts = 0
    rate_limit_attempt = 0
    while True:
        try:
            breaker.before_call()
        except CircuitOpenError as exc:
            usage = Usage.estimate(prompt, "")
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
            governed_result: _GovernedHttpAttemptResult | None = None
            if resolved_physical_dispatch_port is None:
                response = _blocking_http_post(url, headers, payload, timeout)
            else:
                try:
                    governed_result = resolved_physical_dispatch_port.dispatch_sync(
                        wire_request={
                            "endpoint": url,
                            "headers": headers,
                            "body": payload,
                            "transport": {
                                "kind": "requests.post",
                                "timeout": timeout,
                            },
                        },
                        send=lambda frozen: _send_governed_http_attempt(
                            frozen,
                            prompt=prompt,
                            clock=_clock,
                            start=start,
                            extract_output=extract_output,
                            usage_from_response=usage_from_response,
                        ),
                    )
                    response = governed_result.response
                except _GovernedHttpResponseError as governed_failure:
                    # Reuse the exact response object that crossed the wire. The
                    # gate has already recorded this physical attempt as failed;
                    # the helper now applies its existing body/status/Retry-After,
                    # overflow-heal, backoff, and breaker policy without a second
                    # POST or a reconstructed response.
                    response = governed_failure.response
            response_ok = _http_response_is_ok(response)
            if not response_ok:
                # Log response body for error debugging before raising
                error_body = ""
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
                if getattr(response, "status_code", None) == 400 and overflow_heal_attempts < 3:
                    healed = shrink_max_tokens_for_context_overflow(payload, error_body)
                    if not healed and overflow_heal_attempts > 0 and "maximum context length" in (error_body or ""):
                        # The server-reported input is a synthetic lower bound
                        # (window+1-output); when shrinking to it still
                        # overflows, halve instead of trusting the number.
                        try:
                            current_max = int(payload.get("max_tokens") or 0)
                        except (TypeError, ValueError):
                            current_max = 0
                        if current_max > 128:
                            payload["max_tokens"] = max(64, current_max // 2)
                            logger.warning(
                                "[provider-helpers] context overflow heal #%s: halving max_tokens -> %s",
                                overflow_heal_attempts + 1,
                                payload["max_tokens"],
                            )
                            healed = True
                    if healed:
                        overflow_heal_attempts += 1
                        continue
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and 500 <= status_code < 600:
                    breaker.on_failure()
                    latency_ms = int((_clock.time() - start) * 1000)
                    usage = Usage.estimate(prompt, "")
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=(
                            f"{status_code} Server Error from {url}: {error_body[:500] if error_body else '(empty)'}"
                        ),
                    )
                if isinstance(status_code, int) and status_code == 429:
                    # Rate limiting (429) is transient, NOT a hard client error:
                    # retry with backoff (honoring Retry-After) instead of failing
                    # the call. The provider's generic ``retries`` budget defaults
                    # to 0, which cannot ride out shared-provider saturation, so
                    # 429 gets its own minimum budget. Live defect: kimi-for-coding
                    # returned persistent 429 under concurrent factory_bench load
                    # and every CE / Director LLM call failed on the first 429,
                    # materializing 0 source files (prod=0) — a provider-resilience
                    # gap, not a generation-quality gap.
                    #
                    # Critically, a 429 must NOT trip the circuit breaker: rate
                    # limiting is upstream back-pressure, not a service outage.
                    # Counting each retry as a breaker failure opened the SHARED
                    # circuit after a handful of 429s, after which every subsequent
                    # call (CE + Director) fast-failed with ``circuit_open`` for the
                    # whole recovery window — turning transient saturation into a
                    # total outage (prod=0). Retry/backoff is the correct response
                    # to 429, so we deliberately do not call breaker.on_failure().
                    rate_limit_attempt += 1
                    if rate_limit_attempt > max(retries, _rate_limit_min_retries()):
                        latency_ms = int((_clock.time() - start) * 1000)
                        usage = Usage.estimate(prompt, "")
                        return InvokeResult(
                            ok=False,
                            output="",
                            latency_ms=latency_ms,
                            usage=usage,
                            error=(
                                f"429 Rate limited by {url} after "
                                f"{rate_limit_attempt - 1} retries: "
                                f"{error_body[:300] if error_body else '(empty)'}"
                            ),
                        )
                    retry_after = _parse_retry_after_seconds(response)
                    delay = (
                        retry_after
                        if retry_after is not None
                        else _build_backoff_seconds(
                            attempt=rate_limit_attempt,
                            base_delay_seconds=backoff_base_seconds,
                            max_delay_seconds=backoff_max_seconds,
                        )
                    )
                    _clock.sleep(delay)
                    continue
                if isinstance(status_code, int) and 400 <= status_code < 500:
                    breaker.on_failure()
                    latency_ms = int((_clock.time() - start) * 1000)
                    usage = Usage.estimate(prompt, "")
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=f"{status_code} Client Error from {url}: {error_body[:500] if error_body else '(empty)'}",
                    )
                response.raise_for_status()
            if governed_result is None:
                data = response.json()
                latency_ms = int((_clock.time() - start) * 1000)
                output = extract_output(data)
                finalized = LLMResponseParser.finalize_response(data, visible_text=output)
                usage = usage_from_response(prompt, finalized.output, data)
            else:
                data = governed_result.data
                latency_ms = governed_result.latency_ms
                output = governed_result.output
                finalized = governed_result.finalized
                usage = governed_result.usage
            # Canonical reasoning-aware finalization (DEFECT 2 SSoT): one funnel
            # recovers a content:null reasoning-model answer (qwen3.6/MiniMax-M3)
            # or fails closed on a mid-reasoning truncation, instead of silently
            # reporting an empty result. ``output`` is this provider's own content
            # extraction; the reasoning/finish_reason channels come from ``data``.
            breaker.on_success()  # transport succeeded even if the visible output was empty
            if not finalized.ok:
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=latency_ms,
                    usage=usage,
                    error=finalized.error,
                    raw=data,
                    thinking=finalized.thinking,
                )
            if finalized.thinking and not output.strip():
                logger.info(
                    "[provider-helpers] recovered answer from reasoning channel (reasoning_chars=%d)",
                    len(finalized.thinking),
                )
            return InvokeResult(
                ok=True,
                output=finalized.output,
                latency_ms=latency_ms,
                usage=usage,
                raw=data,
                thinking=finalized.thinking,
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
                usage = Usage.estimate(prompt, "")
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
    timeout: HttpTimeout,
) -> HealthResult:
    """POST-based health check with standard error classification.

    Uses _blocking_http_post so the blocking requests.post() does not freeze
    the asyncio event loop when called from async contexts.
    """
    start = time.time()
    try:
        response = _blocking_http_post(url, headers, payload, timeout)
        latency_ms = int((time.time() - start) * 1000)
        status_code = int(getattr(response, "status_code", 0) or 0)

        if status_code == 401:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="Authentication failed: please check your API key"
            )
        if status_code == 404:
            return HealthResult(
                ok=False, latency_ms=latency_ms, error="API endpoint not found: please check api_path configuration"
            )
        if status_code == 429:
            retry_after = ""
            headers_value = getattr(response, "headers", None)
            if headers_value is not None and hasattr(headers_value, "get"):
                retry_after = str(headers_value.get("Retry-After") or "").strip()
            message = "Rate limited by provider: HTTP 429 Too Many Requests"
            if retry_after:
                message = f"{message}; retry after {retry_after} seconds"
            return HealthResult(ok=False, latency_ms=latency_ms, error=message)
        if status_code >= 400:
            response_text = str(getattr(response, "text", "") or "").strip().replace("\n", " ")
            if response_text:
                response_text = response_text[:300]
            message = f"Provider health check failed: HTTP {status_code}"
            if response_text:
                message = f"{message}: {response_text}"
            return HealthResult(ok=False, latency_ms=latency_ms, error=message)

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
    except requests.exceptions.HTTPError as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=f"HTTP health check failed: {exc}")
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=f"Provider request failed: {exc}")
    except (RuntimeError, ValueError) as exc:
        latency_ms = int((time.time() - start) * 1000)
        return HealthResult(ok=False, latency_ms=latency_ms, error=str(exc))


def _do_requests_get(
    url: str,
    headers: dict[str, str],
    timeout: HttpTimeout,
):
    """Thread-safe requests.get call (for ThreadPoolExecutor wrapping)."""
    timeout_value: HttpTimeout | None = timeout
    if isinstance(timeout, (int, float)) and timeout <= 0:
        timeout_value = None
    return requests.get(url, headers=headers, timeout=timeout_value)


def _blocking_http_get(
    url: str,
    headers: dict[str, str],
    timeout: HttpTimeout,
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
    timeout: HttpTimeout,
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

_STREAM_RETRY_DELAY_SEC: float = float(os.environ.get("KERNELONE_STREAM_RETRY_DELAY_SEC", "5.0"))
_STREAM_RETRY_MAX_ATTEMPTS: int = int(os.environ.get("KERNELONE_STREAM_RETRY_MAX_ATTEMPTS", "3"))


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


_AsyncStreamGovernanceMode = Literal["legacy_ungoverned", "governed_required"]


def _validate_async_stream_governance(
    *,
    governance_mode: _AsyncStreamGovernanceMode,
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
) -> None:
    if governance_mode not in {"legacy_ungoverned", "governed_required"}:
        raise ValueError("async stream governance_mode is invalid")
    if governance_mode == "governed_required" and physical_dispatch_port is None:
        raise RuntimeError("governed async stream requires a physical dispatch port")


def _resolve_async_stream_governance(
    *,
    governance_mode: _AsyncStreamGovernanceMode,
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
) -> tuple[_AsyncStreamGovernanceMode, AsyncPhysicalProviderDispatchPort | None]:
    """Promote a request-scoped Factory binding to governed stream dispatch."""

    resolved_port = physical_dispatch_port
    resolved_mode = governance_mode
    if resolved_port is None:
        bound_port = get_physical_provider_dispatch_port()
        if bound_port is not None:
            if not hasattr(bound_port, "dispatch_stream_async"):
                raise RuntimeError("bound physical provider dispatch port does not support dispatch_stream_async")
            resolved_port = cast("AsyncPhysicalProviderDispatchPort", bound_port)
            resolved_mode = "governed_required"
    _validate_async_stream_governance(
        governance_mode=resolved_mode,
        physical_dispatch_port=resolved_port,
    )
    return resolved_mode, resolved_port


def _build_async_stream_wire_request(
    *,
    url: str,
    headers: Mapping[str, Any],
    payload: Mapping[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "endpoint": str(url),
        "headers": dict(headers),
        "body": dict(payload),
        "transport": {
            "kind": "aiohttp.ClientSession.post",
            "timeout_seconds": timeout_seconds if timeout_seconds > 0 else 60,
        },
    }


@asynccontextmanager
async def _open_frozen_aiohttp_stream(
    frozen_wire: Mapping[str, Any],
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Open one response/session pair from the gate's immutable dispatch view."""

    session: aiohttp.ClientSession | None = None
    try:
        session = await _close_and_create_session(None)
        endpoint = str(frozen_wire["endpoint"])
        headers = _thaw_physical_dispatch_value(frozen_wire["headers"])
        body = _thaw_physical_dispatch_value(frozen_wire["body"])
        transport = frozen_wire["transport"]
        if not isinstance(headers, dict) or not isinstance(body, dict) or not isinstance(transport, Mapping):
            raise TypeError("frozen async HTTP dispatch view is malformed")
        timeout_total = int(transport["timeout_seconds"])
        timeout = _ensure_aiohttp_imported().ClientTimeout(total=timeout_total)
        async with session.post(
            endpoint,
            headers=headers,
            json=body,
            timeout=timeout,
        ) as response:
            yield response
    finally:
        if session is not None:
            await _close_session_if_possible(session)


def _dispatch_async_stream_attempt(
    *,
    wire_request: Mapping[str, Any],
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None,
    consume: Callable[[aiohttp.ClientResponse], AsyncIterator[Any]],
) -> AsyncIterator[Any]:
    if physical_dispatch_port is not None:
        return physical_dispatch_port.dispatch_stream_async(
            wire_request=wire_request,
            open_stream=_open_frozen_aiohttp_stream,
            consume=consume,
        )

    async def _legacy_dispatch() -> AsyncIterator[Any]:
        async with _open_frozen_aiohttp_stream(wire_request) as response:
            async for item in consume(response):
                yield item

    return _legacy_dispatch()


async def _aclose_stream_resisting_cancellation(stream: AsyncIterator[Any]) -> bool:
    close = getattr(stream, "aclose", None)
    if not callable(close):
        return False
    close_task = asyncio.create_task(close())
    cancellation_observed = False
    while True:
        try:
            await asyncio.shield(close_task)
            return cancellation_observed
        except asyncio.CancelledError:
            if close_task.cancelled():
                raise
            cancellation_observed = True


async def _consume_data_line_response(
    response: aiohttp.ClientResponse,
    *,
    url: str,
) -> AsyncIterator[dict[str, Any]]:
    if not response.ok:
        try:
            error_body = await response.text()
            logger.warning(
                "[provider-helpers] HTTP %s from %s: %s",
                response.status,
                url,
                error_body[:500] if error_body else "(empty)",
            )
        except (RuntimeError, ValueError):
            logger.warning("[provider-helpers] HTTP %s from %s (failed to read body)", response.status, url)
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type", "") or "").lower()
    if "application/json" in content_type:
        payload_obj = await response.json()
        if isinstance(payload_obj, dict):
            yield payload_obj
            return
        raise RuntimeError(
            f"provider_stream_invalid_json: expected JSON object from {url}, got {type(payload_obj).__name__}"
        )

    decoded_event_count = 0
    async for data_str in iter_data_line_payloads(response.content):
        if data_str == "[DONE]":
            break
        try:
            payload_obj = json.loads(data_str)
        except (RuntimeError, ValueError) as exc:
            logger.debug("[provider-helpers] Failed to decode provider JSON payload from %s: %s", url, exc)
            continue
        if isinstance(payload_obj, dict):
            decoded_event_count += 1
            yield payload_obj
    if decoded_event_count == 0:
        raise RuntimeError(f"provider_stream_empty: no structured events decoded from streaming response {url}")


def _stream_timeout_budget_seconds(timeout_seconds: int | float) -> float:
    return max(0.001, float(timeout_seconds if timeout_seconds > 0 else 60))


def _stream_deadline_remaining_seconds(*, deadline: float, total_timeout_seconds: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError(f"provider_stream_timeout:{total_timeout_seconds:g}s")
    return remaining


async def _iterate_physical_stream_under_deadline(
    stream: AsyncIterator[Any],
    *,
    deadline: float,
    total_timeout_seconds: float,
) -> AsyncIterator[Any]:
    """Apply the provider deadline only while awaiting physical stream data.

    The Factory physical-attempt gate durably terminalizes the provider
    response before replaying its buffered items to the role runtime.  A
    timeout context spanning this helper's outward ``yield`` would therefore
    remain armed during post-terminal replay/backpressure and could cancel the
    consumer task after the physical attempt had already completed.  Keep the
    deadline around each physical ``anext`` only; downstream processing is not
    provider response time.
    """

    iterator = stream.__aiter__()
    while True:
        remaining_seconds = _stream_deadline_remaining_seconds(
            deadline=deadline,
            total_timeout_seconds=total_timeout_seconds,
        )
        try:
            async with asyncio.timeout(remaining_seconds):
                item = await anext(iterator)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(f"provider_stream_timeout:{total_timeout_seconds:g}s") from exc
        yield item


async def invoke_stream_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
    governance_mode: _AsyncStreamGovernanceMode = "legacy_ungoverned",
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """POST provider data-line streams with one physical gate per retry."""

    _, physical_dispatch_port = _resolve_async_stream_governance(
        governance_mode=governance_mode,
        physical_dispatch_port=physical_dispatch_port,
    )
    last_exc: BaseException | None = None
    total_timeout_seconds = _stream_timeout_budget_seconds(timeout_seconds)
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    for attempt in range(1, max_attempts + 1):
        attempt_stream: AsyncIterator[Any] | None = None
        try:
            remaining_seconds = _stream_deadline_remaining_seconds(
                deadline=deadline,
                total_timeout_seconds=total_timeout_seconds,
            )
            wire_request = _build_async_stream_wire_request(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=max(1, int(remaining_seconds + 0.999)),
            )

            async def _consume(response: aiohttp.ClientResponse) -> AsyncIterator[dict[str, Any]]:
                physical_stream = _consume_data_line_response(response, url=url)
                async for item in _iterate_physical_stream_under_deadline(
                    physical_stream,
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                ):
                    yield item

            attempt_stream = _dispatch_async_stream_attempt(
                wire_request=wire_request,
                physical_dispatch_port=physical_dispatch_port,
                consume=_consume,
            )
            async for item in attempt_stream:
                yield item
            return
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except BaseException as exc:
            last_exc = exc
            if not _is_retryable_network_error(exc):
                raise
            if attempt < max_attempts:
                logger.warning(
                    "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    retry_delay_seconds,
                )
                remaining_seconds = _stream_deadline_remaining_seconds(
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                )
                await asyncio.sleep(min(retry_delay_seconds, remaining_seconds))
            else:
                logger.error(
                    "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                    max_attempts,
                    str(exc),
                )
        finally:
            if attempt_stream is not None:
                close_cancelled = await _aclose_stream_resisting_cancellation(attempt_stream)
                if close_cancelled:
                    raise asyncio.CancelledError

    if last_exc is not None:
        raise last_exc


async def invoke_stream_with_retry_and_handler(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
    stream_handler: Callable[[aiohttp.ClientResponse], AsyncGenerator[Any, None]],
    *,
    max_attempts: int = _STREAM_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds: float = _STREAM_RETRY_DELAY_SEC,
    governance_mode: _AsyncStreamGovernanceMode = "legacy_ungoverned",
    physical_dispatch_port: AsyncPhysicalProviderDispatchPort | None = None,
) -> AsyncGenerator[Any, None]:
    """POST custom provider streams with one physical gate per retry."""

    _, physical_dispatch_port = _resolve_async_stream_governance(
        governance_mode=governance_mode,
        physical_dispatch_port=physical_dispatch_port,
    )
    last_exc: BaseException | None = None
    total_timeout_seconds = _stream_timeout_budget_seconds(timeout_seconds)
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    for attempt in range(1, max_attempts + 1):
        attempt_stream: AsyncIterator[Any] | None = None
        try:
            remaining_seconds = _stream_deadline_remaining_seconds(
                deadline=deadline,
                total_timeout_seconds=total_timeout_seconds,
            )
            wire_request = _build_async_stream_wire_request(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=max(1, int(remaining_seconds + 0.999)),
            )

            async def _consume(response: aiohttp.ClientResponse) -> AsyncIterator[Any]:
                response.raise_for_status()
                consumed = 0
                physical_stream = stream_handler(response)
                async for item in _iterate_physical_stream_under_deadline(
                    physical_stream,
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                ):
                    consumed += 1
                    yield item
                if consumed == 0:
                    raise RuntimeError(f"provider_stream_empty: custom handler decoded no events from {url}")

            attempt_stream = _dispatch_async_stream_attempt(
                wire_request=wire_request,
                physical_dispatch_port=physical_dispatch_port,
                consume=_consume,
            )
            async for item in attempt_stream:
                yield item
            return
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except BaseException as exc:
            last_exc = exc
            if not _is_retryable_network_error(exc):
                raise
            if attempt < max_attempts:
                logger.warning(
                    "[provider-helpers] Network jitter detected (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    retry_delay_seconds,
                )
                remaining_seconds = _stream_deadline_remaining_seconds(
                    deadline=deadline,
                    total_timeout_seconds=total_timeout_seconds,
                )
                await asyncio.sleep(min(retry_delay_seconds, remaining_seconds))
            else:
                logger.error(
                    "[provider-helpers] Network jitter retry exhausted (all %d attempts failed): %s",
                    max_attempts,
                    str(exc),
                )
        finally:
            if attempt_stream is not None:
                close_cancelled = await _aclose_stream_resisting_cancellation(attempt_stream)
                if close_cancelled:
                    raise asyncio.CancelledError

    if last_exc is not None:
        raise last_exc


def build_chat_messages_payload(
    chat_messages: Any,
    prompt: str,
    system_prompt: str | None = None,
) -> list[dict[str, str]]:
    """Build a chat-completions ``messages`` array, preserving real role structure.

    ADR-0090 W1.5: weak local models depend heavily on their chat template's
    role anchoring. When the caller supplies a structured ``chat_messages``
    array, use it (system/user/assistant pass through; tool results become
    user turns with a marker; consecutive same-role turns merge; supplemental
    mid-conversation system turns are downgraded to marked user turns because
    strict templates such as vLLM's reject non-leading system messages).
    Otherwise fall back to the legacy single-user-message flattening.

    Shared by openai_compat AND ollama providers — keep provider-agnostic.
    """
    if not isinstance(chat_messages, list) or not chat_messages:
        fallback: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        if system_prompt:
            fallback.insert(0, {"role": "system", "content": str(system_prompt)})
        return fallback

    normalized: list[dict[str, str]] = []
    seen_non_system = False
    for item in chat_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        if role == "tool":
            role, content = "user", f"【工具结果】\n{content}"
        elif role == "system":
            if seen_non_system:
                role, content = "user", f"【系统提示】\n{content}"
        elif role not in ("user", "assistant"):
            role = "user"
        if role != "system":
            seen_non_system = True
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] = f"{normalized[-1]['content']}\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})

    if not normalized:
        normalized = [{"role": "user", "content": prompt}]
    if not any(m["role"] == "user" for m in normalized):
        # Strict chat templates (vLLM qwen3) REJECT conversations without a
        # user turn — observed live (factory-bench 2026-06-12) as intermittent
        # 400 "No user query found in messages" killing whole planning runs:
        # an all-system chat_messages array (user content empty → stripped)
        # passed through untouched. This builder is the SSOT for
        # template-acceptable messages, so the guarantee lives here; the
        # warning keeps a trail to whichever upstream produced the userless
        # array.
        logger.warning(
            "chat_messages contained no user turn (roles=%s); appending user turn",
            [m["role"] for m in normalized],
        )
        prompt_text = str(prompt or "").strip()
        combined_len = sum(len(m["content"]) for m in normalized)
        # W1.5c-5: in the roles-kernel path prompt and chat_messages derive
        # from the SAME messages — appending the full prompt would nearly
        # double the payload (and the duplicate is never budget-accounted).
        # When the array already carries (most of) the prompt content, a short
        # continuation turn satisfies strict templates without the bloat.
        if prompt_text and len(prompt_text) <= combined_len * 0.9:
            normalized.append({"role": "user", "content": "(continue)"})
        else:
            normalized.append({"role": "user", "content": prompt_text or "(continue)"})
    return normalized
