from __future__ import annotations

import asyncio
import sys
import time

import pytest
from polaris.kernelone.runtime.execution_facade import (
    AsyncTaskSpec,
    BlockingIoSpec,
    ExecutionFacade,
    ProcessSpec,
)
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionRuntime,
    ExecutionStatus,
)


@pytest.mark.asyncio
async def test_submit_many_and_wait_many_across_lanes() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    facade = ExecutionFacade(runtime=runtime)

    async def async_job() -> str:
        await asyncio.sleep(0.05)
        return "async-ok"

    def blocking_job() -> str:
        time.sleep(0.03)
        return "blocking-ok"

    specs = [
        AsyncTaskSpec(name="async-job", coroutine_factory=async_job, timeout_seconds=1.0),
        BlockingIoSpec(name="blocking-job", func=blocking_job, timeout_seconds=1.0),
    ]

    handles = await facade.submit_many(specs)  # type: ignore[arg-type]
    result = await facade.wait_many(handles, timeout_per_item=1.0)

    try:
        assert result.all_completed is True
        assert len(result.statuses) == 2
        assert all(status == ExecutionStatus.SUCCESS for status in result.statuses.values())
        assert all(snapshot.ok for snapshot in result.snapshots.values())
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_run_process_collects_stdout_and_stderr() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    facade = ExecutionFacade(runtime=runtime)

    spec = ProcessSpec(
        name="stdout-stderr-job",
        args=[
            sys.executable,
            "-c",
            "import sys; print('out-line'); print('err-line', file=sys.stderr)",
        ],
        timeout_seconds=5.0,
    )

    try:
        result = await facade.run_process(spec, collect_output=True)
        assert result.status == ExecutionStatus.SUCCESS
        assert any("out-line" in line for line in result.stdout_lines)
        assert any("err-line" in line for line in result.stderr_lines)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wait_many_marks_timeout_ids() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    facade = ExecutionFacade(runtime=runtime)
    unblock = asyncio.Event()

    async def slow_job() -> str:
        await unblock.wait()
        return "done"

    handle = facade.submit_async_task(AsyncTaskSpec(name="slow-job", coroutine_factory=slow_job, timeout_seconds=5.0))

    try:
        wait_result = await facade.wait_many([handle], timeout_per_item=0.1)
        assert handle.execution_id in wait_result.timed_out_execution_ids
    finally:
        unblock.set()
        await facade.cancel_many([handle])
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_many_cancels_pending_async_task() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    facade = ExecutionFacade(runtime=runtime)
    unblock = asyncio.Event()

    async def cancellable() -> str:
        await unblock.wait()
        return "never"

    handle = facade.submit_async_task(
        AsyncTaskSpec(name="cancel-target", coroutine_factory=cancellable, timeout_seconds=5.0)
    )

    try:
        cancel_result = await facade.cancel_many([handle])
        assert handle.execution_id in cancel_result.cancelled_execution_ids
        status = await handle.wait(timeout=1.0)
        assert status == ExecutionStatus.CANCELLED
    finally:
        unblock.set()
        await runtime.close()
