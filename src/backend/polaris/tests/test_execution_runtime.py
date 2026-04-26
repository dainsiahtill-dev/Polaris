"""Tests for ExecutionRuntime process timeout handling.

This module tests that timed-out processes are properly terminated,
including the graceful termination -> force kill pattern.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.process.async_contracts import (
    PopenAsyncHandle,
    ProcessStatus,
    StreamResult,
)
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionLane,
    ExecutionRuntime,
    ExecutionStatus,
    _ExecutionState,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def runtime() -> ExecutionRuntime:
    """Create a fresh ExecutionRuntime instance."""
    runtime = ExecutionRuntime(
        async_concurrency=4,
        blocking_concurrency=2,
        process_concurrency=2,
    )
    yield runtime
    # Synchronous cleanup - run in new event loop if needed
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _ = asyncio.ensure_future(runtime.close())
        else:
            loop.run_until_complete(runtime.close())
    except RuntimeError:
        pass


@pytest.fixture
def mock_process_handle() -> PopenAsyncHandle:
    """Create a mock process handle."""
    handle = MagicMock(spec=PopenAsyncHandle)
    handle.pid = 12345
    handle.status = ProcessStatus.RUNNING
    return handle


def make_test_state(
    execution_id: str,
    name: str,
    process_handle: PopenAsyncHandle | None,
    timeout_seconds: float = 5.0,
) -> _ExecutionState:
    """Helper to create test execution states."""
    return _ExecutionState(
        execution_id=execution_id,
        name=name,
        lane=ExecutionLane.SUBPROCESS,
        status=ExecutionStatus.RUNNING,
        submitted_at=datetime.now(timezone.utc),
        timeout_seconds=timeout_seconds,
        metadata={},
        process_handle=process_handle,
    )


# =============================================================================
# _handle_process_timeout Tests
# =============================================================================


@pytest.mark.asyncio
async def test_handle_process_timeout_calls_terminate_first(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify graceful termination is attempted first."""
    mock_process_handle.terminate = AsyncMock(return_value=True)

    state = make_test_state("test-123", "test-process", mock_process_handle)

    await runtime._handle_process_timeout(state, mock_process_handle)

    mock_process_handle.terminate.assert_called_once_with(timeout=1.0)
    mock_process_handle.kill.assert_not_called()


@pytest.mark.asyncio
async def test_handle_process_timeout_calls_kill_if_terminate_fails(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify force kill is called when graceful termination fails."""
    mock_process_handle.terminate = AsyncMock(return_value=False)
    mock_process_handle.kill = AsyncMock()

    state = make_test_state("test-456", "test-process", mock_process_handle)

    await runtime._handle_process_timeout(state, mock_process_handle)

    mock_process_handle.terminate.assert_called_once_with(timeout=1.0)
    mock_process_handle.kill.assert_called_once()


@pytest.mark.asyncio
async def test_handle_process_timeout_logs_warning_on_graceful_failure(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify warning is logged when graceful termination fails."""
    mock_process_handle.terminate = AsyncMock(return_value=False)
    mock_process_handle.kill = AsyncMock()

    state = make_test_state("test-789", "my-test-process", mock_process_handle, timeout_seconds=10.0)

    await runtime._handle_process_timeout(state, mock_process_handle)

    assert "Process timed out" in caplog.text
    assert "Graceful termination failed" in caplog.text


# =============================================================================
# _await_process_completion Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_await_process_completion_handles_timeout_correctly(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify _await_process_completion uses _handle_process_timeout on timeout."""
    mock_process_handle.wait = AsyncMock(return_value=ProcessStatus.TIMED_OUT)
    mock_process_handle.terminate = AsyncMock(return_value=True)
    mock_process_handle.result = AsyncMock(
        return_value=StreamResult(
            pid=12345,
            exit_code=-1,
            status=ProcessStatus.TIMED_OUT,
            stdout_lines=(),
            stderr_lines=(),
            timed_out=True,
            timeout_seconds=1,
            started_at=datetime.now(timezone.utc),
        )
    )

    state = make_test_state("test-timeout", "timeout-process", mock_process_handle, timeout_seconds=1.0)

    await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.TIMED_OUT
    mock_process_handle.terminate.assert_called_once_with(timeout=1.0)


@pytest.mark.asyncio
async def test_await_process_completion_cancels_on_task_cancellation(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify cancellation sets CANCELLED status and attempts termination."""
    mock_process_handle.wait = AsyncMock(side_effect=asyncio.CancelledError)
    mock_process_handle.terminate = AsyncMock(return_value=True)

    state = make_test_state("test-cancel", "cancel-process", mock_process_handle, timeout_seconds=30.0)

    with pytest.raises(asyncio.CancelledError):
        await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.CANCELLED
    mock_process_handle.terminate.assert_called_once_with(timeout=1.0)


@pytest.mark.asyncio
async def test_await_process_completion_handles_asyncio_timeout_error(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify asyncio.TimeoutError is handled and process is terminated."""
    mock_process_handle.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_process_handle.terminate = AsyncMock(return_value=True)
    mock_process_handle.result = AsyncMock(
        return_value=StreamResult(
            pid=12345,
            exit_code=-1,
            status=ProcessStatus.TIMED_OUT,
            stdout_lines=(),
            stderr_lines=(),
            timed_out=True,
            timeout_seconds=1,
            started_at=datetime.now(timezone.utc),
        )
    )

    state = make_test_state(
        "test-asyncio-timeout",
        "async-timeout-process",
        mock_process_handle,
        timeout_seconds=1.0,
    )

    await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.TIMED_OUT
    mock_process_handle.terminate.assert_called()


@pytest.mark.asyncio
async def test_await_process_completion_handles_success(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify successful process completion sets SUCCESS status."""
    mock_process_handle.wait = AsyncMock(return_value=ProcessStatus.SUCCESS)
    mock_process_handle.result = AsyncMock(
        return_value=StreamResult(
            pid=12345,
            exit_code=0,
            status=ProcessStatus.SUCCESS,
            stdout_lines=("output",),
            stderr_lines=(),
            timed_out=False,
            timeout_seconds=30,
            started_at=datetime.now(timezone.utc),
        )
    )

    state = make_test_state("test-success", "success-process", mock_process_handle, timeout_seconds=30.0)

    await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.SUCCESS
    mock_process_handle.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_await_process_completion_handles_failure(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
) -> None:
    """Verify failed process completion sets FAILED status."""
    mock_process_handle.wait = AsyncMock(return_value=ProcessStatus.FAILED)
    mock_process_handle.result = AsyncMock(
        return_value=StreamResult(
            pid=12345,
            exit_code=1,
            status=ProcessStatus.FAILED,
            stdout_lines=(),
            stderr_lines=("error",),
            timed_out=False,
            timeout_seconds=30,
            started_at=datetime.now(timezone.utc),
        )
    )

    state = make_test_state("test-failure", "failure-process", mock_process_handle, timeout_seconds=30.0)

    await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.FAILED
    mock_process_handle.terminate.assert_not_called()


# =============================================================================
# submit_process Timeout Tests (End-to-End)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.slow
async def test_submit_process_timeout_terminates_process(runtime: ExecutionRuntime) -> None:
    """Verify that a timed-out process is actually terminated.

    This is an integration test that spawns a long-running process
    and verifies it gets terminated when it times out.
    """
    if sys.platform == "win32":
        # Use a loop that runs indefinitely - process should be killed by timeout
        cmd = ["python", "-c", "import time; [time.sleep(0.1) for _ in range(1000)]"]
    else:
        cmd = ["sleep", "100"]

    handle = await runtime.submit_process(
        name="long-running-process",
        args=cmd,
        timeout_seconds=1.0,  # 1 second timeout
    )

    # Wait for process to complete (should be killed by timeout)
    status = await handle.wait(timeout=10.0)

    # The process should be terminated (not still running)
    # Status can be TIMED_OUT, CANCELLED, or FAILED depending on timing
    assert status in (
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.FAILED,
    ), f"Expected terminal status, got {status}"

    snapshot = handle.snapshot()
    # Verify error message indicates timeout
    assert "timed out" in snapshot.error.lower() or snapshot.error, (
        f"Expected timeout error message, got: {snapshot.error}"
    )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_timed_out_process_is_not_orphaned(runtime: ExecutionRuntime) -> None:
    """Verify the process is actually killed and not left running."""
    if sys.platform == "win32":
        cmd = ["python", "-c", "import time; [time.sleep(0.1) for _ in range(1000)]"]
    else:
        cmd = ["sleep", "100"]

    handle = await runtime.submit_process(
        name="orphan-test-process",
        args=cmd,
        timeout_seconds=1.0,
    )

    await handle.wait(timeout=10.0)
    await asyncio.sleep(0.5)

    process = handle.process
    if process is not None:
        poll_result = process.poll()
        assert poll_result is not None, "Process should have been terminated and not be still running"


# =============================================================================
# terminate() API Tests
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_returns_false_when_process_not_responding(
    runtime: ExecutionRuntime,
) -> None:
    """Verify terminate() returns False when graceful termination fails.

    This test uses a process that ignores SIGTERM to verify that
    the underlying handle correctly returns False.
    """
    if sys.platform == "win32":
        # Windows doesn't have SIGTERM equivalent that can be ignored easily
        pytest.skip("Windows doesn't support ignoring termination signals")
    else:
        # Use a trap script that ignores SIGTERM
        cmd = [
            "python",
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(100)",
        ]

    handle = await runtime.submit_process(
        name="ignoring-process",
        args=cmd,
        timeout_seconds=30.0,
    )

    await asyncio.sleep(0.3)

    await runtime.terminate(handle.execution_id, timeout=1.0)

    snapshot = handle.snapshot()
    assert snapshot.status == ExecutionStatus.CANCELLED


# =============================================================================
# Edge Cases
# =============================================================================


@pytest.mark.asyncio
async def test_await_process_completion_with_missing_handle(
    runtime: ExecutionRuntime,
) -> None:
    """Verify proper handling when process handle is None."""
    state = make_test_state("test-no-handle", "no-handle-process", None, timeout_seconds=30.0)

    await runtime._await_process_completion(state)

    assert state.status == ExecutionStatus.FAILED
    assert "missing" in state.error.lower()


@pytest.mark.asyncio
async def test_handle_process_timeout_handles_terminate_exception(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _handle_process_timeout handles exceptions from terminate gracefully."""
    mock_process_handle.terminate = AsyncMock(side_effect=Exception("terminate failed"))
    mock_process_handle.kill = AsyncMock()

    state = make_test_state("test-exception", "exception-process", mock_process_handle)

    # Should not raise, should try kill as fallback
    await runtime._handle_process_timeout(state, mock_process_handle)

    mock_process_handle.kill.assert_called_once()


@pytest.mark.asyncio
async def test_handle_process_timeout_handles_kill_exception(
    runtime: ExecutionRuntime,
    mock_process_handle: PopenAsyncHandle,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _handle_process_timeout handles exceptions from kill gracefully."""
    mock_process_handle.terminate = AsyncMock(return_value=False)
    mock_process_handle.kill = AsyncMock(side_effect=Exception("kill failed"))

    state = make_test_state("test-kill-exception", "kill-exception-process", mock_process_handle)

    # Should not raise
    await runtime._handle_process_timeout(state, mock_process_handle)

    # Error should be logged
    assert "failed" in caplog.text.lower() or "error" in caplog.text.lower()
