"""Unified Concurrency Management for Polaris.

This module provides a per-event-loop singleton manager for thread pools and
process pools, ensuring consistent resource management across the codebase.

Architecture:
    UnifiedConcurrencyManager (singleton per event loop)
        ├── _io_pools: Dict[int, ThreadPoolExecutor]   # I/O-bound tasks
        ├── _cpu_pools: Dict[int, ThreadPoolExecutor]  # CPU-bound tasks
        └── _proc_pools: Dict[int, ProcessPoolExecutor] # Multiprocessing

Usage:
    from polaris.kernelone.concurrency import get_concurrency_manager

    manager = get_concurrency_manager()
    io_pool = manager.get_io_pool(max_workers=32)
    cpu_pool = manager.get_cpu_pool()
    proc_pool = manager.get_process_pool()
"""

from __future__ import annotations

import atexit
import concurrent.futures
import os
import threading
from typing import TYPE_CHECKING, Final, TypedDict

if TYPE_CHECKING:
    pass

__all__ = [
    "ConcurrencyPoolConfig",
    "ConcurrencyPoolType",
    "UnifiedConcurrencyManager",
    "get_concurrency_manager",
]


# -------------------------------------------------------------------------------
# Types
# -------------------------------------------------------------------------------

class ConcurrencyPoolConfig(TypedDict, total=False):
    """Configuration for a concurrency pool."""

    max_workers: int
    thread_name_prefix: str
    pool_type: str


class ConcurrencyPoolType:
    """Enum-like class for pool types."""

    IO: Final[str] = "io"
    CPU: Final[str] = "cpu"
    PROCESS: Final[str] = "process"


# -------------------------------------------------------------------------------
# Default Values
# -------------------------------------------------------------------------------

DEFAULT_IO_WORKERS: Final[int] = int(os.environ.get("KERNELONE_IO_POOL_WORKERS", "32"))
DEFAULT_CPU_WORKERS: Final[int] = int(os.environ.get("KERNELONE_CPU_POOL_WORKERS", str(os.cpu_count() or 4)))
DEFAULT_PROCESS_WORKERS: Final[int] = int(os.environ.get("KERNELONE_PROCESS_POOL_WORKERS", str(os.cpu_count() or 4)))


# -------------------------------------------------------------------------------
# Singleton Manager
# -------------------------------------------------------------------------------

class UnifiedConcurrencyManager:
    """Per-event-loop singleton manager for thread/process pools.

    This class provides centralized management of all thread pools and process
    pools in the application. Each event loop gets its own manager instance,
    ensuring proper isolation in async contexts.

    The manager caches pools by max_workers configuration, allowing efficient
    reuse of identically-configured pools while preventing unbounded pool creation.

    Example:
        >>> import asyncio
        >>> async def main():
        ...     manager = get_concurrency_manager()
        ...     io_pool = manager.get_io_pool(max_workers=16)
        ...     cpu_pool = manager.get_cpu_pool()
        ...     # Use pools...
        ...     await manager.shutdown_all()
        >>> asyncio.run(main())
    """

    __slots__ = (
        "_cpu_pools",
        "_io_pools",
        "_lock",
        "_proc_pools",
        "_shutdown",
    )

    _instances: dict[int, UnifiedConcurrencyManager] = {}

    def __new__(cls) -> UnifiedConcurrencyManager:
        """Create or return the singleton for the current event loop."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop - return a process-wide singleton
            loop = None

        key = id(loop) if loop else 0
        if key not in cls._instances:
            instance = super().__new__(cls)
            instance._init()
            cls._instances[key] = instance
            _register_atexit_callback(instance)

        return cls._instances[key]

    def _init(self) -> None:
        """Initialize the manager's internal state."""
        self._io_pools: dict[int, concurrent.futures.ThreadPoolExecutor] = {}
        self._cpu_pools: dict[int, concurrent.futures.ThreadPoolExecutor] = {}
        self._proc_pools: dict[int, concurrent.futures.ProcessPoolExecutor] = {}
        self._lock = threading.Lock()
        self._shutdown = False

    def get_io_pool(self, max_workers: int = DEFAULT_IO_WORKERS) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create an I/O-bound thread pool.

        Args:
            max_workers: Maximum number of worker threads. Defaults to 32.

        Returns:
            A ThreadPoolExecutor configured for I/O-bound work.

        Raises:
            RuntimeError: If the manager has been shut down.
        """
        if self._shutdown:
            raise RuntimeError("ConcurrencyManager has been shut down")
        if max_workers < 1:
            max_workers = 1

        with self._lock:
            if max_workers not in self._io_pools:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="kernelone-io",
                )
                self._io_pools[max_workers] = executor
            return self._io_pools[max_workers]

    def get_cpu_pool(self, max_workers: int | None = None) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create a CPU-bound thread pool.

        Args:
            max_workers: Maximum number of worker threads. Defaults to CPU count.

        Returns:
            A ThreadPoolExecutor configured for CPU-bound work.

        Raises:
            RuntimeError: If the manager has been shut down.
        """
        if self._shutdown:
            raise RuntimeError("ConcurrencyManager has been shut down")
        if max_workers is None:
            max_workers = DEFAULT_CPU_WORKERS
        if max_workers < 1:
            max_workers = 1

        with self._lock:
            if max_workers not in self._cpu_pools:
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="kernelone-cpu",
                )
                self._cpu_pools[max_workers] = executor
            return self._cpu_pools[max_workers]

    def get_process_pool(
        self,
        max_workers: int | None = None,
    ) -> concurrent.futures.ProcessPoolExecutor:
        """Get or create a process pool for CPU-intensive work.

        Args:
            max_workers: Maximum number of worker processes. Defaults to CPU count.

        Returns:
            A ProcessPoolExecutor configured for CPU-intensive work.

        Raises:
            RuntimeError: If the manager has been shut down.
        """
        if self._shutdown:
            raise RuntimeError("ConcurrencyManager has been shut down")
        if max_workers is None:
            max_workers = DEFAULT_PROCESS_WORKERS
        if max_workers < 1:
            max_workers = 1

        with self._lock:
            if max_workers not in self._proc_pools:
                executor = concurrent.futures.ProcessPoolExecutor(
                    max_workers=max_workers,
                )
                self._proc_pools[max_workers] = executor
            return self._proc_pools[max_workers]

    def get_http_pool(self, max_workers: int = 32) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create a thread pool dedicated to HTTP requests.

        This is a specialized I/O pool optimized for HTTP calls with a
        default size appropriate for provider invocations.

        Args:
            max_workers: Maximum number of worker threads. Defaults to 32.

        Returns:
            A ThreadPoolExecutor configured for HTTP work.
        """
        return self.get_io_pool(max_workers=max_workers)

    def get_sleep_pool(self, max_workers: int = 4) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create a thread pool dedicated to blocking sleeps.

        This separates sleep operations from HTTP to avoid head-of-line
        blocking when slow HTTP calls occupy the main HTTP pool.

        Args:
            max_workers: Maximum number of worker threads. Defaults to 4.

        Returns:
            A ThreadPoolExecutor configured for sleep operations.
        """
        return self.get_io_pool(max_workers=max_workers)

    async def shutdown_all(self, timeout: float = 5.0) -> None:
        """Gracefully shut down all managed pools.

        Args:
            timeout: Maximum seconds to wait for each pool to shut down.
        """
        if self._shutdown:
            return

        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True

        # Shutdown in a separate thread to avoid blocking async
        def _shutdown() -> None:
            list(self._io_pools.values()).__class__  # ensure type
            for p in list(self._io_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=True)
            for p in list(self._cpu_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=True)
            for p in list(self._proc_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=True)

        shutdown_thread = threading.Thread(target=_shutdown, daemon=True)
        shutdown_thread.start()
        shutdown_thread.join(timeout=timeout)

    def get_stats(self) -> dict[str, dict[str, int | bool]]:
        """Get statistics about all managed pools.

        Returns:
            A dictionary with pool statistics including worker counts and shutdown status.
        """
        def _get_max_workers(p: concurrent.futures.Executor) -> int:
            """Get max_workers from executor, handling ProcessPoolExecutor safely."""
            return getattr(p, "_max_workers", 0) or 0

        io_workers = sum(_get_max_workers(p) for p in self._io_pools.values())
        cpu_workers = sum(_get_max_workers(p) for p in self._cpu_pools.values())
        proc_workers = sum(_get_max_workers(p) for p in self._proc_pools.values())
        return {
            "io_pools": {
                "count": len(self._io_pools),
                "total_workers": io_workers,
                "shutdown": self._shutdown,
            },
            "cpu_pools": {
                "count": len(self._cpu_pools),
                "total_workers": cpu_workers,
                "shutdown": self._shutdown,
            },
            "process_pools": {
                "count": len(self._proc_pools),
                "total_workers": proc_workers,
                "shutdown": self._shutdown,
            },
        }

    def health_check(self, timeout: float = 1.0) -> dict[str, object]:
        """Perform a health check on all managed pools.

        Args:
            timeout: Maximum seconds to wait for pool responsiveness check.

        Returns:
            A dictionary with health status, error messages, and stats.
        """
        errors: list[str] = []
        healthy = True

        # Check if manager is shut down
        if self._shutdown:
            errors.append("Manager has been shut down")
            healthy = False

        # Check each pool type (ThreadPoolExecutor has _shutdown, ProcessPoolExecutor doesn't)
        for pool in list(self._io_pools.values()):
            if getattr(pool, "_shutdown", False):
                errors.append("IO pool is shut down")
                healthy = False

        for pool in list(self._cpu_pools.values()):
            if getattr(pool, "_shutdown", False):
                errors.append("CPU pool is shut down")
                healthy = False

        # ProcessPoolExecutor doesn't have _shutdown attribute
        for pool in list(self._proc_pools.values()):  # type: ignore[assignment]
            pass  # No shutdown attribute on ProcessPoolExecutor

        return {
            "healthy": healthy,
            "errors": errors,
            "stats": self.get_stats(),
        }


# -------------------------------------------------------------------------------
# Module-level helpers
# -------------------------------------------------------------------------------

_manager_atexit_registered: bool = False
_manager_atexit_callback: list[UnifiedConcurrencyManager] = []
_manager_init_lock: threading.Lock = threading.Lock()


def _register_atexit_callback(manager: UnifiedConcurrencyManager) -> None:
    """Register a manager for atexit cleanup."""
    global _manager_atexit_registered, _manager_atexit_callback
    if manager not in _manager_atexit_callback:
        _manager_atexit_callback.append(manager)
    if not _manager_atexit_registered:
        atexit.register(_cleanup_managers)
        _manager_atexit_registered = True


def _cleanup_managers() -> None:
    """Clean up all managers on interpreter shutdown."""
    for manager in _manager_atexit_callback:
        try:
            for p in list(manager._io_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=False)
            for p in list(manager._cpu_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=False)
            for p in list(manager._proc_pools.values()):  # type: ignore[assignment]
                p.shutdown(wait=False)
        except Exception:
            pass  # Ignore errors during shutdown


# Cached manager instance for sync contexts
_sync_manager: UnifiedConcurrencyManager | None = None


def get_concurrency_manager() -> UnifiedConcurrencyManager:
    """Get the concurrency manager for the current context.

    In async context (with running event loop), returns the per-loop singleton.
    In sync context (no event loop), returns a process-wide singleton.

    Returns:
        The UnifiedConcurrencyManager instance.
    """
    global _sync_manager
    try:
        import asyncio
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop - use process-wide singleton
        if _sync_manager is None:
            with _manager_init_lock:
                if _sync_manager is None:
                    _sync_manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
                    _sync_manager._init()
                    _register_atexit_callback(_sync_manager)
        return _sync_manager

    key = id(loop)
    # Fast path: check without lock
    if key in UnifiedConcurrencyManager._instances:
        return UnifiedConcurrencyManager._instances[key]

    # Slow path: create new manager with lock
    with _manager_init_lock:
        # Double-check after acquiring lock
        if key not in UnifiedConcurrencyManager._instances:
            manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
            manager._init()
            UnifiedConcurrencyManager._instances[key] = manager
            _register_atexit_callback(manager)
        return UnifiedConcurrencyManager._instances[key]
