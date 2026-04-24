"""Concurrency safety tests for KernelOne modules.

Tests cover:
  - Storage roots cache thread safety
  - Context cache concurrent access
  - Message bus concurrent operations
  - Executor manager thread safety
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest


class TestContextCacheConcurrency:
    """Tests for ContextCache thread safety."""

    def test_cache_concurrent_access_no_errors(self) -> None:
        """ContextCache must handle concurrent read/write without errors."""
        from polaris.kernelone.context.engine.cache import ContextCache
        from polaris.kernelone.context.engine.models import ContextItem, ContextPack

        cache = ContextCache()
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                pack = ContextPack(
                    request_hash=f"key_{i}",
                    items=[ContextItem(id=f"item_{i}", kind="code", content_or_pointer=f"content_{i}", size_est=i)],
                    total_tokens=i,
                    total_chars=i * 10,
                )
                cache.cache_pack(pack)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        def reader(i: int) -> None:
            try:
                key = f"key_{i % 100}"
                cache.get_cached_pack(key)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        threads = []
        # Create 100 items first
        for i in range(100):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Now do concurrent read/write
        threads = []
        for i in range(200):
            t = threading.Thread(target=writer, args=(i,)) if i % 3 == 0 else threading.Thread(target=reader, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Concurrent access errors: {errors}"

    def test_cache_write_after_write_consistency(self) -> None:
        """Multiple writes to same key must be consistent."""
        from polaris.kernelone.context.engine.cache import ContextCache
        from polaris.kernelone.context.engine.models import ContextItem, ContextPack

        cache = ContextCache()
        final_values: list[int] = []

        def writer(value: int) -> None:
            pack = ContextPack(
                request_hash="same_key",
                items=[
                    ContextItem(id=f"item_{value}", kind="code", content_or_pointer=f"content_{value}", size_est=value)
                ],
                total_tokens=value,
                total_chars=value * 10,
            )
            cache.cache_pack(pack)

        def reader() -> None:
            pack = cache.get_cached_pack("same_key")
            if pack:
                final_values.append(pack.total_tokens)

        # Write 100 times rapidly
        threads = []
        for i in range(100):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Read final value
        reader()

        # Final value should be one of the written values (0-99)
        assert len(final_values) == 1
        assert 0 <= final_values[0] < 100


class TestMessageBusConcurrency:
    """Tests for MessageBus concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self) -> None:
        """Concurrent subscribe/unsubscribe must be thread-safe."""
        from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

        bus = MessageBus()
        errors: list[Exception] = []

        async def subscriber(i: int) -> None:
            async def handler(msg: Message) -> None:
                pass

            try:
                await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
                await asyncio.sleep(0.001)
                await bus.unsubscribe(MessageType.TASK_SUBMITTED, handler)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        tasks = [subscriber(i) for i in range(50)]
        await asyncio.gather(*tasks)

        assert not errors

    @pytest.mark.asyncio
    async def test_concurrent_publish_subscribe(self) -> None:
        """Concurrent publish while subscribing must be safe."""
        from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

        bus = MessageBus()
        received_count = 0
        lock = asyncio.Lock()

        async def counting_handler(msg: Message) -> None:
            nonlocal received_count
            async with lock:
                received_count += 1

        async def publisher(i: int) -> None:
            await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"pub_{i}"))

        async def subscriber(i: int) -> None:
            await bus.subscribe(MessageType.TASK_SUBMITTED, counting_handler)

        # Start subscribers first
        sub_tasks = [subscriber(i) for i in range(10)]
        await asyncio.gather(*sub_tasks)

        # Publish concurrently
        pub_tasks = [publisher(i) for i in range(100)]
        await asyncio.gather(*pub_tasks)

        await asyncio.sleep(0.1)

        # All messages should be received
        assert received_count == 100


class TestStorageConcurrency:
    """Tests for storage-related concurrency safety."""

    def test_storage_workspace_path_concurrent(self) -> None:
        """Workspace path resolution must handle concurrent calls safely."""
        from polaris.kernelone.storage.io_paths import find_workspace_root

        results: list[Any] = []
        errors: list[Exception] = []

        def resolve_workspace(i: int) -> None:
            try:
                # Use a real temp directory for testing
                import tempfile

                with tempfile.TemporaryDirectory() as tmpdir:
                    workspace = find_workspace_root(tmpdir)
                    results.append(workspace)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        threads = []
        for i in range(20):
            t = threading.Thread(target=resolve_workspace, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Concurrent storage access errors: {errors}"
        assert len(results) == 20

    def test_build_cache_root_concurrent(self) -> None:
        """build_cache_root must handle concurrent calls safely."""
        from polaris.kernelone.storage.io_paths import build_cache_root

        results: list[Any] = []
        errors: list[Exception] = []

        def build_cache(i: int) -> None:
            try:
                import tempfile

                with tempfile.TemporaryDirectory() as ramdisk:
                    cache_root = build_cache_root(ramdisk, f"/tmp/project_{i % 3}")
                    results.append(cache_root)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        threads = []
        for i in range(20):
            t = threading.Thread(target=build_cache, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Concurrent cache root build errors: {errors}"
        assert len(results) == 20


class TestExecutorManagerConcurrency:
    """Tests for WorkspaceExecutorManager concurrency."""

    def test_executor_manager_thread_safe_creation(self) -> None:
        """WorkspaceExecutorManager must handle concurrent executor creation safely."""
        from polaris.kernelone.llm.engine.executor import WorkspaceExecutorManager

        manager = WorkspaceExecutorManager()
        executors: list[Any] = []
        errors: list[Exception] = []

        def get_executor(workspace: str) -> None:
            try:
                executor = manager.get_executor_sync(workspace)
                executors.append(executor)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        threads = []
        for i in range(50):
            workspace = f"/tmp/workspace_{i % 10}"
            t = threading.Thread(target=get_executor, args=(workspace,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Executor creation errors: {errors}"
        # Should have 10 unique executors (one per unique workspace)
        unique = {id(e) for e in executors}
        assert len(unique) == 10


class TestAsyncConcurrency:
    """Tests for async concurrency patterns."""

    @pytest.mark.asyncio
    async def test_semaphore_concurrency_limit(self) -> None:
        """Semaphore must correctly limit concurrency."""
        from polaris.kernelone.llm.engine.executor import _get_global_semaphore

        # Get the global semaphore
        semaphore = await _get_global_semaphore()

        assert isinstance(semaphore, asyncio.Semaphore)
        # Semaphore should exist and be usable
        assert semaphore._value > 0

    @pytest.mark.asyncio
    async def test_executor_manager_async_lock_safety(self) -> None:
        """WorkspaceExecutorManager async operations must be lock-safe."""
        from polaris.kernelone.llm.engine.executor import WorkspaceExecutorManager

        manager = WorkspaceExecutorManager()
        executors: list[Any] = []
        errors: list[Exception] = []

        async def get_executor_async(workspace: str) -> None:
            try:
                executor = await manager.get_executor(workspace)
                executors.append(executor)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        tasks = [get_executor_async(f"/tmp/workspace_{i % 10}") for i in range(100)]
        await asyncio.gather(*tasks)

        assert not errors, f"Async executor creation errors: {errors}"
        # Should have 10 unique executors
        unique = {id(e) for e in executors}
        assert len(unique) == 10


class TestRaceConditionScenarios:
    """Tests for specific race condition scenarios."""

    def test_cache_miss_then_fill_race(self) -> None:
        """Cache miss followed by fill must not cause inconsistency."""
        from polaris.kernelone.context.engine.cache import ContextCache
        from polaris.kernelone.context.engine.models import ContextItem, ContextPack

        cache = ContextCache()
        results: list[Any] = []

        def writer_reader(i: int) -> None:
            key = f"key_{i % 10}"
            # Write
            pack = ContextPack(
                request_hash=key,
                items=[ContextItem(id=f"item_{i}", kind="code", content_or_pointer=f"content_{i}", size_est=i)],
                total_tokens=i,
                total_chars=i * 10,
            )
            cache.cache_pack(pack)
            # Read
            result = cache.get_cached_pack(key)
            results.append(result)

        threads = [threading.Thread(target=writer_reader, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be valid ContextPacks
        for result in results:
            assert result is not None
            assert isinstance(result, ContextPack)

    @pytest.mark.asyncio
    async def test_message_bus_burst_publish(self) -> None:
        """Message bus must handle burst publishes without dropping or corrupting."""
        from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

        bus = MessageBus()
        received: list[Message] = []
        lock = asyncio.Lock()

        async def handler(msg: Message) -> None:
            async with lock:
                received.append(msg)

        await bus.subscribe(MessageType.TASK_SUBMITTED, handler)

        # Burst of 500 messages
        for i in range(500):
            await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"burst_{i}"))

        await asyncio.sleep(0.2)

        assert len(received) == 500

    @pytest.mark.asyncio
    async def test_rapid_subscribe_publish(self) -> None:
        """Rapid subscribe/publish cycles must not lose messages."""
        from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

        bus = MessageBus()
        total_received = 0

        for iteration in range(20):
            received_this_iteration = []

            async def handler(msg: Message) -> None:
                received_this_iteration.append(msg)  # noqa: B023

            await bus.subscribe(MessageType.DIRECTOR_START, handler)
            await bus.publish(Message(type=MessageType.DIRECTOR_START, sender=f"iter_{iteration}"))
            await asyncio.sleep(0.01)

            total_received += len(received_this_iteration)
            await bus.unsubscribe(MessageType.DIRECTOR_START, handler)

        assert total_received == 20


class TestLockFreePatterns:
    """Tests for lock-free concurrent patterns."""

    def test_message_bus_lock_performance(self) -> None:
        """Message bus lock must not become a bottleneck."""
        from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        async def setup() -> None:
            await bus.subscribe(MessageType.TASK_SUBMITTED, handler)

        async def run_test():
            start = time.perf_counter()
            for i in range(100):
                await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"perf_{i}"))
            elapsed = time.perf_counter() - start
            return elapsed

        async def main():
            await setup()
            return await run_test()

        elapsed = asyncio.run(main())

        # 100 publishes should complete in reasonable time (< 5 seconds)
        assert elapsed < 5.0, f"Publish took too long: {elapsed}s"

    def test_context_cache_lock_efficiency(self) -> None:
        """Context cache lock must not cause contention."""
        from polaris.kernelone.context.engine.cache import ContextCache
        from polaris.kernelone.context.engine.models import ContextItem, ContextPack

        cache = ContextCache()

        def write_benchmark():
            start = time.perf_counter()
            for i in range(1000):
                pack = ContextPack(
                    request_hash=f"key_{i}",
                    items=[ContextItem(id=f"item_{i}", kind="code", content_or_pointer=f"content_{i}", size_est=i)],
                    total_tokens=i,
                    total_chars=i * 10,
                )
                cache.cache_pack(pack)
            return time.perf_counter() - start

        def read_benchmark():
            start = time.perf_counter()
            for i in range(1000):
                cache.get_cached_pack(f"key_{i}")
            return time.perf_counter() - start

        # Run in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            write_future = executor.submit(write_benchmark)
            read_future = executor.submit(read_benchmark)

            write_time = write_future.result()
            read_time = read_future.result()

        # Both should complete in reasonable time
        assert write_time < 10.0, f"Write took too long: {write_time}s"
        assert read_time < 10.0, f"Read took too long: {read_time}s"
