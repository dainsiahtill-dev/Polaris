from __future__ import annotations

import asyncio
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionRuntime,
    ExecutionStatus,
)
from polaris.kernelone.runtime.metrics import reset_metrics


@pytest.mark.asyncio
async def test_async_lane_applies_backpressure() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    release_first = asyncio.Event()
    started: list[str] = []

    async def first() -> str:
        started.append("first")
        await release_first.wait()
        return "done-first"

    async def second() -> str:
        started.append("second")
        return "done-second"

    handle_one = runtime.submit_async(name="first-task", coroutine_factory=first)
    handle_two = runtime.submit_async(name="second-task", coroutine_factory=second)

    try:
        await asyncio.sleep(0.05)
        assert handle_one.snapshot().status == ExecutionStatus.RUNNING
        assert handle_two.snapshot().status == ExecutionStatus.QUEUED

        release_first.set()

        assert await handle_one.wait(timeout=1.0) == ExecutionStatus.SUCCESS
        assert await handle_two.wait(timeout=1.0) == ExecutionStatus.SUCCESS
        assert started == ["first", "second"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_blocking_lane_runs_in_controlled_thread_pool() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)

    def blocking_call() -> str:
        time.sleep(0.05)
        return "blocking-ok"

    handle = runtime.submit_blocking(
        name="blocking-call",
        func=blocking_call,
        timeout_seconds=1.0,
    )

    try:
        assert await handle.wait(timeout=1.0) == ExecutionStatus.SUCCESS
        snapshot = handle.snapshot()
        assert snapshot.result == "blocking-ok"
        assert snapshot.status == ExecutionStatus.SUCCESS
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_process_lane_streams_output_and_completes() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    handle = await runtime.submit_process(
        name="echo-process",
        args=[sys.executable, "-c", "print('hello from execution runtime')"],
        timeout_seconds=5.0,
    )

    try:
        chunks: list[str] = []
        async for chunk in handle.stream():
            if chunk.line:
                chunks.append(chunk.line)

        assert await handle.wait(timeout=5.0) == ExecutionStatus.SUCCESS
        assert any("hello from execution runtime" in line for line in chunks)
        assert handle.snapshot().pid is not None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_process_lane_times_out_and_reclaims_subprocess() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    handle = await runtime.submit_process(
        name="sleep-process",
        args=[sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=0.2,
    )

    try:
        assert await handle.wait(timeout=3.0) == ExecutionStatus.TIMED_OUT
        process = handle.process
        assert process is not None
        assert process.poll() is not None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_process_semaphore_released_on_spawn_failure() -> None:
    """Verify semaphore is released when runner.spawn() raises an exception.

    This tests the fix for semaphore leak where _mark_failed() could throw,
    preventing the release of the acquired semaphore.
    """
    mock_runner = MagicMock()
    mock_runner.spawn = MagicMock(side_effect=RuntimeError("spawn failed"))

    runtime = ExecutionRuntime(
        async_concurrency=1,
        blocking_concurrency=1,
        process_concurrency=1,
        process_runner_factory=lambda: mock_runner,
    )

    try:
        # First submission fails
        with pytest.raises(RuntimeError, match="spawn failed"):
            await runtime.submit_process(
                name="failing-process",
                args=["invalid", "args"],
                timeout_seconds=5.0,
            )

        # Semaphore should be released - verify by checking internal state
        assert runtime._process_semaphore.locked() is False, "Semaphore should not be locked after spawn failure"

        # Second submission fails with different error
        mock_runner.spawn = MagicMock(side_effect=OSError("different error"))
        with pytest.raises(OSError, match="different error"):
            await runtime.submit_process(
                name="another-failing-process",
                args=["invalid", "args"],
                timeout_seconds=5.0,
            )

        # Semaphore should still be released
        assert runtime._process_semaphore.locked() is False, (
            "Semaphore should not be locked after multiple spawn failures"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_process_semaphore_released_on_mark_failed_exception() -> None:
    """Verify semaphore release even when _mark_failed() itself raises.

    This is a defensive test ensuring the try-finally pattern correctly
    releases the semaphore regardless of exceptions in _mark_failed().
    """
    # Track semaphore acquire/release calls
    acquire_count = 0
    release_count = 0

    class TrackingSemaphore:
        def __init__(self, original: asyncio.Semaphore) -> None:
            self._original = original

        async def acquire(self) -> None:
            nonlocal acquire_count
            await self._original.acquire()
            acquire_count += 1

        def release(self) -> None:
            nonlocal release_count
            self._original.release()
            release_count += 1

        def locked(self) -> bool:
            return self._original.locked()

    mock_runner = MagicMock()

    async def failing_spawn(**kwargs: object) -> MagicMock:
        raise OSError("simulated spawn failure")

    mock_runner.spawn = failing_spawn

    runtime = ExecutionRuntime(
        async_concurrency=1,
        blocking_concurrency=1,
        process_concurrency=1,
        process_runner_factory=lambda: mock_runner,
    )

    # Replace semaphore with tracking version
    tracking_sem = TrackingSemaphore(runtime._process_semaphore)
    runtime._process_semaphore = tracking_sem  # type: ignore[assignment]

    try:
        with pytest.raises(OSError):
            await runtime.submit_process(
                name="tracking-process",
                args=["test"],
                timeout_seconds=5.0,
            )

        # Verify semaphore is not locked after exception
        assert tracking_sem.locked() is False, "Semaphore must be released after spawn failure"

        # Verify semaphore was acquired and released exactly once
        assert acquire_count == 1, "Semaphore should have been acquired once"
        assert release_count == 1, "Semaphore should have been released once"
    finally:
        await runtime.close()


class TestExecutionRuntimeHealthCheck:
    """Tests for ExecutionRuntime health check functionality."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_metrics()

    def teardown_method(self) -> None:
        """Reset metrics after each test."""
        reset_metrics()

    def test_health_check_returns_expected_structure(self) -> None:
        """Verify health_check returns the correct structure."""
        runtime = ExecutionRuntime(
            async_concurrency=10,
            blocking_concurrency=5,
            process_concurrency=3,
        )

        health = runtime.health_check()

        assert "healthy" in health
        assert "timestamp" in health
        assert "runtime" in health
        assert "concurrency" in health

        assert health["healthy"] is True
        assert health["runtime"]["closed"] is False

        runtime._closed = True

    def test_health_check_concurrency_availability(self) -> None:
        """Verify health_check reports correct concurrency slots."""
        runtime = ExecutionRuntime(
            async_concurrency=10,
            blocking_concurrency=5,
            process_concurrency=3,
        )

        health = runtime.health_check()

        assert health["concurrency"]["async_available"] == 10
        assert health["concurrency"]["blocking_available"] == 5
        assert health["concurrency"]["process_available"] == 3

        runtime._closed = True

    def test_health_check_reflects_closed_state(self) -> None:
        """Verify health_check reflects runtime closed state."""
        runtime = ExecutionRuntime()
        runtime._closed = True

        health = runtime.health_check()
        assert health["runtime"]["closed"] is True

    def test_health_check_active_executions(self) -> None:
        """Verify health_check shows active executions count."""
        runtime = ExecutionRuntime(
            async_concurrency=32,
            blocking_concurrency=8,
            process_concurrency=4,
        )

        # Before any submission
        health = runtime.health_check()
        assert health["runtime"]["active_executions"]["async_task"] == 0
        assert health["runtime"]["active_executions"]["subprocess"] == 0

        runtime._closed = True

    def test_health_check_states_tracking(self) -> None:
        """Verify health_check tracks state counts."""
        runtime = ExecutionRuntime(
            async_concurrency=1,
            blocking_concurrency=1,
            process_concurrency=1,
        )

        health = runtime.health_check()
        assert "states_retained" in health["runtime"]
        assert "states_active" in health["runtime"]

        runtime._closed = True

    def test_get_metrics_text_returns_prometheus_format(self) -> None:
        """Verify get_metrics_text returns Prometheus text format."""
        runtime = ExecutionRuntime()

        text = runtime.get_metrics_text()

        assert "kernelone_execution_active_current" in text
        assert "kernelone_execution_completed_total" in text
        assert "# HELP" in text
        assert "# TYPE" in text

        runtime._closed = True

    def test_health_check_with_mocked_tracer(self) -> None:
        """Verify health_check works with mocked tracer."""
        runtime = ExecutionRuntime(
            async_concurrency=4,
            blocking_concurrency=2,
            process_concurrency=1,
        )

        mock_span = MagicMock()

        with patch("polaris.kernelone.runtime.execution_runtime.get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_tracer.span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.span.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_tracer.return_value = mock_tracer

            health = runtime.health_check()
            assert health["healthy"] is True

        runtime._closed = True


class TestExecutionRuntimeTracing:
    """Tests for ExecutionRuntime span creation and tracing."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_metrics()

    def teardown_method(self) -> None:
        """Reset metrics after each test."""
        reset_metrics()

    @pytest.mark.asyncio
    async def test_execution_records_metrics_on_success(self) -> None:
        """Verify execution records success metrics."""
        runtime = ExecutionRuntime(
            async_concurrency=1,
            blocking_concurrency=1,
            process_concurrency=1,
        )

        handle = runtime.submit_async(
            name="success-task",
            coroutine_factory=lambda: asyncio.sleep(0.01),
        )

        try:
            await handle.wait(timeout=1.0)
            assert handle.snapshot().status == ExecutionStatus.SUCCESS
        finally:
            await runtime.close()

    @pytest.mark.asyncio
    async def test_execution_records_metrics_on_timeout(self) -> None:
        """Verify execution records timeout metrics."""
        runtime = ExecutionRuntime(
            async_concurrency=1,
            blocking_concurrency=1,
            process_concurrency=1,
        )

        handle = runtime.submit_async(
            name="timeout-task",
            coroutine_factory=lambda: asyncio.sleep(10),
            timeout_seconds=0.1,
        )

        try:
            status = await handle.wait(timeout=2.0)
            assert status == ExecutionStatus.TIMED_OUT
        finally:
            await runtime.close()
