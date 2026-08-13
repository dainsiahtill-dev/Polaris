"""Internal implementation module for the provider_helpers package (lossless split).

Owns: lightweight aiohttp session/pool emulation, blocking/async HTTP pools,
the LRU stream-session registry, and the sync ``requests``-based HTTP helpers
(``_blocking_http_post`` / ``_blocking_http_get`` / ``_blocking_sleep``).

Cross-module free names that are owned elsewhere and resolved at call time via
the package namespace are injected by the package ``__init__``
(``_wire_cross_module_namespace``). The internal HTTP helpers are referenced
through the package module at call time inside other submodules so that
``monkeypatch.setattr("...provider_helpers._blocking_http_post", ...)``
(still very common in the provider test-suite) is observed losslessly.

Static F821/F401 are expected and lossless; do not strip.
"""

from __future__ import annotations

import asyncio
import atexit
import codecs
import concurrent.futures
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import requests
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.engine.contracts import get_physical_provider_dispatch_port

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable

    import aiohttp
    from polaris.kernelone.llm.engine.contracts import PhysicalProviderDispatchPort

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
        _sleep_pool = _get_http_pool()
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


__all__ = [
    "_BACKGROUND_TASKS",
    "_BLOCKING_HTTP_POOL_LAZY",
    "_MAX_HTTP_WORKERS",
    "_MAX_SLEEP_WORKERS",
    "_REAL_CLIENT_SESSION_TYPE",
    "_SLEEP_POOL_LAZY",
    "_STREAM_SESSION_CLEANUP_REGISTERED",
    "_STREAM_SESSION_LOCK",
    "_STREAM_SESSION_REGISTRY",
    "HttpTimeout",
    "_LRUSessionRegistry",
    "_LazyPool",
    "_LightweightAiohttpModule",
    "_LightweightClientSession",
    "_LightweightClientTimeout",
    "_LightweightTCPConnector",
    "_aiohttp_module",
    "_blocking_http_get",
    "_blocking_http_pool",
    "_blocking_http_post",
    "_blocking_sleep",
    "_close_and_create_session",
    "_close_session_if_possible",
    "_do_requests_get",
    "_do_requests_post",
    "_ensure_aiohttp_imported",
    "_get_blocking_http_pool",
    "_get_blocking_sleep_pool",
    "_get_http_pool",
    "_get_sleep_pool",
    "_is_reusable_stream_session",
    "_raw_blocking_http_post",
    "_register_stream_session_cleanup_once",
    "_session_is_closed",
    "_should_use_lightweight_stream_session_mode",
    "_sleep_pool",
    "_thaw_physical_dispatch_value",
    "_track_background_task",
    "close_stream_sessions",
    "close_stream_sessions_sync",
    "get_stream_session",
    "iter_data_line_payloads",
]
