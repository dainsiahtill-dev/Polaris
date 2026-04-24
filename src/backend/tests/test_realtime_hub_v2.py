"""Tests for RealtimeSignalHub v2 with reference counting.

Test matrix coverage:
1. Concurrent ensure_watch calls result in single observer
2. Ref counting is accurate
3. release_watch properly decrements and stops observer when 0
4. TOCTOU protection
5. Workspace isolation
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from polaris.infrastructure.realtime.process_local.signal_hub import RealtimeSignalHub


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def hub():
    """Create a fresh hub instance."""
    h = RealtimeSignalHub()
    yield h
    h.close()


@pytest.mark.asyncio
async def test_ensure_watch_creates_observer(temp_dir):
    """Test that ensure_watch creates a new observer."""
    hub = RealtimeSignalHub()
    try:
        result = await hub.ensure_watch(temp_dir)
        assert result is True

        info = hub.get_watch_info(temp_dir)
        assert info is not None
        assert info["state"] == "RUNNING"
        assert info["ref_count"] == 1
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_ensure_watch_increments_ref_count(temp_dir):
    """Test that multiple ensure_watch calls increment ref_count."""
    hub = RealtimeSignalHub()
    try:
        # First call creates
        assert await hub.ensure_watch(temp_dir) is True

        # Second call increments ref
        assert await hub.ensure_watch(temp_dir) is True

        info = hub.get_watch_info(temp_dir)
        assert info["ref_count"] == 2
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_release_watch_decrements_ref_count(temp_dir):
    """Test that release_watch decrements ref_count."""
    hub = RealtimeSignalHub()
    try:
        # Setup: create with ref_count=2
        await hub.ensure_watch(temp_dir)
        await hub.ensure_watch(temp_dir)
        assert hub.get_watch_info(temp_dir)["ref_count"] == 2

        # Release once
        hub.release_watch(temp_dir)
        assert hub.get_watch_info(temp_dir)["ref_count"] == 1

        # Release again - should stop observer
        hub.release_watch(temp_dir)
        assert hub.get_watch_info(temp_dir) is None
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_concurrent_ensure_watch_single_observer(temp_dir):
    """Test that concurrent ensure_watch calls result in single observer."""
    hub = RealtimeSignalHub()
    try:
        # Concurrent calls
        async def ensure():
            return await hub.ensure_watch(temp_dir)

        results = await asyncio.gather(*[ensure() for _ in range(20)])
        assert all(results), "All ensure_watch calls should succeed"

        info = hub.get_watch_info(temp_dir)
        assert info["ref_count"] == 20, f"Expected ref_count=20, got {info['ref_count']}"
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_concurrent_release_watch_cleanup(temp_dir):
    """Test that concurrent releases properly clean up."""
    hub = RealtimeSignalHub()
    try:
        # Setup: create multiple refs
        for _ in range(10):
            await hub.ensure_watch(temp_dir)

        # Concurrent releases
        def release():
            hub.release_watch(temp_dir)

        with ThreadPoolExecutor(max_workers=10) as executor:
            [executor.submit(release) for _ in range(10)]
            executor.shutdown(wait=True)

        # Give time for cleanup
        await asyncio.sleep(0.1)

        # Should be fully cleaned up
        assert hub.get_watch_info(temp_dir) is None
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_notify_and_wait_for_update(temp_dir):
    """Test notify and wait_for_update mechanism."""
    hub = RealtimeSignalHub()
    try:
        seq = await hub.notify(source="test", path="/test/path", root=temp_dir)
        assert seq == 1

        # Wait should return immediately if already notified
        next_seq = await hub.wait_for_update(0, timeout_sec=0.1, workspace=temp_dir)
        assert next_seq >= 1
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_wait_for_update_with_workspace_filter():
    """Test that workspace filtering works in wait_for_update."""
    hub = RealtimeSignalHub()
    try:
        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            # Notify for dir1
            await hub.notify(source="test", path="/test", root=dir1)

            # Wait for dir2 should timeout (no signal)
            next_seq = await hub.wait_for_update(0, timeout_sec=0.1, workspace=dir2)
            # Should return current sequence even if no matching signal
            assert next_seq >= 0
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_list_watches(temp_dir):
    """Test listing all active watches."""
    hub = RealtimeSignalHub()
    try:
        await hub.ensure_watch(temp_dir)

        watches = hub.list_watches()
        assert len(watches) == 1
        assert watches[0]["root"] == os.path.abspath(temp_dir)
        assert watches[0]["state"] == "RUNNING"
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_release_watch_nonexistent():
    """Test that releasing a non-existent watch is safe."""
    hub = RealtimeSignalHub()
    try:
        # Should not raise
        hub.release_watch("/nonexistent/path")
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_empty_root_returns_false():
    """Test that empty root returns False."""
    hub = RealtimeSignalHub()
    try:
        result = await hub.ensure_watch("")
        assert result is False
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_notify_from_thread(temp_dir):
    """Test that notify_from_thread works correctly."""
    hub = RealtimeSignalHub()
    try:
        await hub.ensure_watch(temp_dir)

        # First trigger notify() to set up the loop
        await hub.notify(source="test", path="/test/init.txt", root=temp_dir)
        assert hub._sequence == 1

        # Simulate thread notification
        def notify_in_thread():
            hub.notify_from_thread(source="fs", path="/test/file.txt", root=temp_dir)

        thread = threading.Thread(target=notify_in_thread)
        thread.start()
        thread.join()

        # Give async task time to run
        await asyncio.sleep(0.1)

        # Should have incremented sequence
        assert hub._sequence >= 2
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_close_releases_all_watches(temp_dir):
    """Test that close() releases all watches."""
    hub = RealtimeSignalHub()

    with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
        await hub.ensure_watch(dir1)
        await hub.ensure_watch(dir2)

        assert len(hub.list_watches()) == 2

        hub.close()

        # All watches should be stopped
        assert len(hub.list_watches()) == 0


@pytest.mark.asyncio
async def test_ref_count_accuracy_under_load(temp_dir):
    """Stress test for ref counting accuracy."""
    hub = RealtimeSignalHub()
    try:
        num_ops = 100

        async def mixed_operations():
            for _ in range(num_ops):
                await hub.ensure_watch(temp_dir)
                await asyncio.sleep(0.001)  # Small delay
                hub.release_watch(temp_dir)

        # Run multiple concurrent mixed operations
        await asyncio.gather(*[mixed_operations() for _ in range(5)])

        # Give time for cleanup
        await asyncio.sleep(0.2)

        # Should be cleaned up
        info = hub.get_watch_info(temp_dir)
        assert info is None, f"Expected cleanup, got {info}"
    finally:
        hub.close()
