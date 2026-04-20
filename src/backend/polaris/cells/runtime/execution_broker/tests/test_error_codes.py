from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionErrorCode,
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import ExecutionBrokerService
from polaris.kernelone.runtime import ExecutionFacade, ExecutionRuntime

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_successful_launch_has_no_error_code(tmp_path: Path) -> None:
    """Verify successful launch returns no error_code."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="echo-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None
        assert launch.error_message is None
        assert launch.error_code is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_launch_nonexistent_command_returns_error_code(tmp_path: Path) -> None:
    """Verify launching nonexistent command returns LAUNCH_FAILED error_code."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="nonexistent-command-xyz",
        args=("nonexistent-command-xyz",),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is False
        assert launch.handle is None
        assert launch.error_message is not None
        assert launch.error_code == ExecutionErrorCode.LAUNCH_FAILED
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wait_timeout_returns_error_code(tmp_path: Path) -> None:
    """Verify wait timeout returns TIMEOUT_EXCEEDED error_code."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="sleep-test",
        args=(sys.executable, "-c", "import time; time.sleep(10)"),
        workspace=str(tmp_path),
        timeout_seconds=30.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None

        # Wait with very short timeout to trigger timeout error
        wait_result = await broker.wait_process(launch.handle, timeout_seconds=0.1)
        assert wait_result.success is False
        assert wait_result.timed_out is True
        assert wait_result.error_code == ExecutionErrorCode.TIMEOUT_EXCEEDED
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_error_codes_are_enum_values() -> None:
    """Verify all error codes are valid ExecutionErrorCode enum members."""
    for code in ExecutionErrorCode:
        assert isinstance(code, ExecutionErrorCode)
        assert isinstance(code.value, str)
        assert code.value.startswith("execution_broker.")


@pytest.mark.asyncio
async def test_error_code_in_result_dataclass(tmp_path: Path) -> None:
    """Verify result dataclasses accept error_code parameter."""
    from polaris.cells.runtime.execution_broker.public.contracts import (
        ExecutionProcessHandleV1,
        ExecutionProcessLaunchResultV1,
        ExecutionProcessWaitResultV1,
    )

    # Test ExecutionProcessLaunchResultV1 with error_code
    launch_result = ExecutionProcessLaunchResultV1(
        success=False,
        error_message="test error",
        error_code=ExecutionErrorCode.LAUNCH_FAILED,
    )
    assert launch_result.error_code == ExecutionErrorCode.LAUNCH_FAILED
    assert launch_result.error_code.value == "execution_broker.launch_failed"

    # Test ExecutionProcessWaitResultV1 with error_code
    handle = ExecutionProcessHandleV1(
        execution_id="test-123",
        pid=1234,
        name="test-process",
        workspace=str(tmp_path),
    )
    wait_result = ExecutionProcessWaitResultV1(
        handle=handle,
        status=ExecutionProcessStatusV1.TIMED_OUT,
        success=False,
        timed_out=True,
        error_message="timeout",
        error_code=ExecutionErrorCode.TIMEOUT_EXCEEDED,
    )
    assert wait_result.error_code == ExecutionErrorCode.TIMEOUT_EXCEEDED
