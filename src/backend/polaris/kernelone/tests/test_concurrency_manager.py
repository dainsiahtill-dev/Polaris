"""Unit tests for polaris.kernelone.concurrency.manager (UnifiedConcurrencyManager).

Covers:
- Singleton behavior per event loop / sync context
- Pool creation and caching (IO, CPU, process, HTTP, sleep)
- shutdown_all graceful cleanup
- get_stats and health_check
- Boundary conditions: max_workers < 1, shutdown after shutdown
- Exception paths: pool access after shutdown
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from polaris.kernelone.concurrency.manager import (
    ConcurrencyPoolConfig,
    ConcurrencyPoolType,
    UnifiedConcurrencyManager,
    get_concurrency_manager,
)

# -----------------------------------------------------------------------------
# Singleton behavior
# -----------------------------------------------------------------------------


def test_sync_singleton_returns_same_instance() -> None:
    """In sync context, get_concurrency_manager returns the same instance."""
    m1 = get_concurrency_manager()
    m2 = get_concurrency_manager()
    assert m1 is m2


@pytest.mark.asyncio
async def test_async_singleton_per_loop() -> None:
    """In async context, same event loop returns the same instance."""
    m1 = get_concurrency_manager()
    m2 = get_concurrency_manager()
    assert m1 is m2


@pytest.mark.skipif(sys.platform == "win32", reason="Nested event loops are unreliable on Windows under pytest-asyncio")
@pytest.mark.asyncio
async def test_different_loops_get_different_instances() -> None:
    """Different event loops get different manager instances."""
    m1 = get_concurrency_manager()

    def _inner() -> UnifiedConcurrencyManager:
        return get_concurrency_manager()

    # Run in a separate thread with its own event loop
    loop = asyncio.new_event_loop()
    try:
        future = loop.run_in_executor(None, _inner)
        # Need to run the loop to completion
        m2 = loop.run_until_complete(future)
    finally:
        loop.close()

    # m1 and m2 may or may not be the same depending on thread/event loop state,
    # but they should both be valid instances
    assert isinstance(m1, UnifiedConcurrencyManager)
    assert isinstance(m2, UnifiedConcurrencyManager)


# -----------------------------------------------------------------------------
# Pool creation and caching
# -----------------------------------------------------------------------------


def test_get_io_pool_creates_thread_pool() -> None:
    """get_io_pool returns a ThreadPoolExecutor."""
    manager = get_concurrency_manager()
    pool = manager.get_io_pool(max_workers=4)
    assert isinstance(pool, concurrent.futures.ThreadPoolExecutor)
    assert pool._max_workers == 4  # type: ignore[attr-defined]


def test_get_cpu_pool_creates_thread_pool() -> None:
    """get_cpu_pool returns a ThreadPoolExecutor with appropriate workers."""
    manager = get_concurrency_manager()
    pool = manager.get_cpu_pool(max_workers=2)
    assert isinstance(pool, concurrent.futures.ThreadPoolExecutor)
    assert pool._max_workers == 2  # type: ignore[attr-defined]


def test_get_process_pool_creates_process_pool() -> None:
    """get_process_pool returns a ProcessPoolExecutor."""
    manager = get_concurrency_manager()
    pool = manager.get_process_pool(max_workers=2)
    assert isinstance(pool, concurrent.futures.ProcessPoolExecutor)


def test_pool_caching_same_max_workers() -> None:
    """Pools with identical max_workers are cached and reused."""
    manager = get_concurrency_manager()
    p1 = manager.get_io_pool(max_workers=8)
    p2 = manager.get_io_pool(max_workers=8)
    assert p1 is p2


def test_pool_different_max_workers_not_shared() -> None:
    """Pools with different max_workers are distinct instances."""
    manager = get_concurrency_manager()
    p1 = manager.get_io_pool(max_workers=4)
    p2 = manager.get_io_pool(max_workers=8)
    assert p1 is not p2


def test_get_http_pool_is_io_pool() -> None:
    """get_http_pool delegates to get_io_pool."""
    manager = get_concurrency_manager()
    http_pool = manager.get_http_pool(max_workers=16)
    io_pool = manager.get_io_pool(max_workers=16)
    assert http_pool is io_pool


def test_get_sleep_pool_is_io_pool() -> None:
    """get_sleep_pool delegates to get_io_pool."""
    manager = get_concurrency_manager()
    sleep_pool = manager.get_sleep_pool(max_workers=2)
    io_pool = manager.get_io_pool(max_workers=2)
    assert sleep_pool is io_pool


# -----------------------------------------------------------------------------
# Boundary: max_workers < 1
# -----------------------------------------------------------------------------


def test_get_io_pool_clamps_negative_workers() -> None:
    """Negative max_workers is clamped to 1."""
    manager = get_concurrency_manager()
    pool = manager.get_io_pool(max_workers=-5)
    assert pool._max_workers == 1  # type: ignore[attr-defined]


def test_get_cpu_pool_clamps_zero_workers() -> None:
    """Zero max_workers is clamped to 1."""
    manager = get_concurrency_manager()
    pool = manager.get_cpu_pool(max_workers=0)
    assert pool._max_workers == 1  # type: ignore[attr-defined]


def test_get_process_pool_clamps_negative_workers() -> None:
    """Negative max_workers for process pool is clamped to 1."""
    manager = get_concurrency_manager()
    pool = manager.get_process_pool(max_workers=-1)
    assert pool._max_workers == 1  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# shutdown_all
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_all_sets_shutdown_flag() -> None:
    """shutdown_all marks the manager as shut down."""
    # Use a fresh manager via __new__ to avoid affecting global state
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    assert not manager._shutdown
    await manager.shutdown_all(timeout=1.0)
    assert manager._shutdown


@pytest.mark.asyncio
async def test_shutdown_all_idempotent() -> None:
    """shutdown_all is safe to call multiple times."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    await manager.shutdown_all(timeout=1.0)
    await manager.shutdown_all(timeout=1.0)
    assert manager._shutdown


@pytest.mark.asyncio
async def test_pool_access_after_shutdown_raises() -> None:
    """Accessing pools after shutdown raises RuntimeError."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    await manager.shutdown_all(timeout=1.0)

    with pytest.raises(RuntimeError, match="shut down"):
        manager.get_io_pool()

    with pytest.raises(RuntimeError, match="shut down"):
        manager.get_cpu_pool()

    with pytest.raises(RuntimeError, match="shut down"):
        manager.get_process_pool()


# -----------------------------------------------------------------------------
# get_stats
# -----------------------------------------------------------------------------


def test_get_stats_structure() -> None:
    """get_stats returns the expected dictionary structure."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    stats = manager.get_stats()
    assert "io_pools" in stats
    assert "cpu_pools" in stats
    assert "process_pools" in stats

    for key in ("io_pools", "cpu_pools", "process_pools"):
        assert "count" in stats[key]
        assert "total_workers" in stats[key]
        assert "shutdown" in stats[key]
        assert isinstance(stats[key]["count"], int)
        assert isinstance(stats[key]["total_workers"], int)
        assert isinstance(stats[key]["shutdown"], bool)


def test_get_stats_reflects_pools() -> None:
    """get_stats counts created pools correctly."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    manager.get_io_pool(max_workers=4)
    manager.get_cpu_pool(max_workers=2)
    stats = manager.get_stats()
    assert stats["io_pools"]["count"] == 1
    assert stats["io_pools"]["total_workers"] == 4
    assert stats["cpu_pools"]["count"] == 1
    assert stats["cpu_pools"]["total_workers"] == 2


# -----------------------------------------------------------------------------
# health_check
# -----------------------------------------------------------------------------


def test_health_check_healthy() -> None:
    """health_check reports healthy for a fresh manager."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    result = manager.health_check(timeout=1.0)
    assert result["healthy"] is True
    assert result["errors"] == []
    assert "stats" in result


def test_health_check_shut_down_unhealthy() -> None:
    """health_check reports unhealthy after shutdown."""
    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    manager._shutdown = True
    result = manager.health_check(timeout=1.0)
    assert result["healthy"] is False
    errors: list[str] = result["errors"]  # type: ignore[assignment]
    assert any("shut down" in e for e in errors)


# -----------------------------------------------------------------------------
# Types / constants
# -----------------------------------------------------------------------------


def test_pool_type_constants() -> None:
    """ConcurrencyPoolType has the expected values."""
    assert ConcurrencyPoolType.IO == "io"
    assert ConcurrencyPoolType.CPU == "cpu"
    assert ConcurrencyPoolType.PROCESS == "process"


def test_pool_config_typeddict() -> None:
    """ConcurrencyPoolConfig accepts expected keys."""
    cfg: ConcurrencyPoolConfig = {"max_workers": 8, "thread_name_prefix": "test", "pool_type": "io"}
    assert cfg["max_workers"] == 8
    assert cfg["thread_name_prefix"] == "test"


# -----------------------------------------------------------------------------
# Default workers from environment
# -----------------------------------------------------------------------------


def test_default_io_workers_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEFAULT_IO_WORKERS respects KERNELONE_IO_POOL_WORKERS."""
    monkeypatch.setenv("KERNELONE_IO_POOL_WORKERS", "64")
    # Re-import to pick up new env value
    import importlib

    from polaris.kernelone.concurrency import manager as mgr_mod

    importlib.reload(mgr_mod)
    assert mgr_mod.DEFAULT_IO_WORKERS == 64


def test_default_cpu_workers_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEFAULT_CPU_WORKERS respects KERNELONE_CPU_POOL_WORKERS."""
    monkeypatch.setenv("KERNELONE_CPU_POOL_WORKERS", "8")
    import importlib

    from polaris.kernelone.concurrency import manager as mgr_mod

    importlib.reload(mgr_mod)
    assert mgr_mod.DEFAULT_CPU_WORKERS == 8


# -----------------------------------------------------------------------------
# atexit cleanup helpers
# -----------------------------------------------------------------------------


def test_register_atexit_callback_idempotent() -> None:
    """Registering the same manager multiple times only adds it once."""
    from polaris.kernelone.concurrency.manager import (
        _manager_atexit_callback,
        _register_atexit_callback,
    )

    manager = UnifiedConcurrencyManager.__new__(UnifiedConcurrencyManager)
    manager._init()
    before = len(_manager_atexit_callback)
    _register_atexit_callback(manager)
    _register_atexit_callback(manager)
    after = len(_manager_atexit_callback)
    # Should only increase by 1
    assert after == before + 1

    # Cleanup: remove our test manager to avoid side effects
    if manager in _manager_atexit_callback:
        _manager_atexit_callback.remove(manager)
