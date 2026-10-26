from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from polaris.kernelone.runtime.execution_facade import (
    AsyncTaskSpec,
    BlockingIoSpec,
    ExecutionFacade,
    ProcessSpec,
)
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionSnapshot,
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


class _SyntheticCleanupRuntime(ExecutionRuntime):
    """Synthetic public runtime port; no subprocess or physical cleanup exists."""

    def __init__(self) -> None:
        super().__init__()
        self.cleanup_allowed = False
        self.cancelled: list[str] = []
        self.snapshots = {
            name: ExecutionSnapshot(
                execution_id=name,
                name=name,
                lane=ExecutionLane.SUBPROCESS,
                status=status,
                submitted_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
                timeout_seconds=None,
                metadata={"process_cleanup_status": cleanup, "synthetic_runtime_port": True},
            )
            for name, status, cleanup in (
                ("unknown", ExecutionStatus.FAILED, "unknown"),
                ("finished", ExecutionStatus.SUCCESS, "confirmed"),
            )
        }

    async def submit_process(self, **kwargs: Any) -> ExecutionHandle:
        return ExecutionHandle(self, str(kwargs["name"]))

    def get_snapshot(self, execution_id: str) -> ExecutionSnapshot:
        return self.snapshots[execution_id]

    def list_snapshots(
        self, *, lane: ExecutionLane | None = None, status: ExecutionStatus | None = None
    ) -> list[ExecutionSnapshot]:
        return [
            snapshot
            for snapshot in self.snapshots.values()
            if (lane is None or snapshot.lane == lane) and (status is None or snapshot.status == status)
        ]

    async def cancel(self, execution_id: str) -> bool:
        self.cancelled.append(execution_id)
        if not self.cleanup_allowed:
            return False
        snapshot = self.snapshots[execution_id]
        self.snapshots[execution_id] = replace(
            snapshot, metadata={**snapshot.metadata, "process_cleanup_status": "confirmed"}
        )
        return True

    async def close(self, *, cancel_running: bool = True) -> None:
        await super().close(cancel_running=False)


@pytest.mark.asyncio
async def test_close_retains_unknown_process_lookup_and_same_owner_cleanup() -> None:
    runtime = _SyntheticCleanupRuntime()
    facade = ExecutionFacade(runtime=runtime)
    unknown = await facade.submit_process(ProcessSpec(name="unknown", args=["synthetic-no-process"]))
    finished = await facade.submit_process(ProcessSpec(name="finished", args=["synthetic-no-process"]))

    try:
        await facade.close()
        assert runtime.get_snapshot(unknown.execution_id).metadata["process_cleanup_status"] == "unknown"
        assert facade.snapshot(unknown.execution_id).metadata["process_cleanup_status"] == "unknown"
        assert facade.resolve_handle(unknown.execution_id) is unknown
        assert facade.list_known_handles() == [unknown]
        with pytest.raises(KeyError):
            facade.resolve_handle(finished.execution_id)

        denied = await facade.cancel_many([unknown.execution_id])
        assert denied.skipped_execution_ids == (unknown.execution_id,)
        assert facade.snapshot(unknown.execution_id).status == ExecutionStatus.FAILED
        assert facade.snapshot(unknown.execution_id).ok is False
        runtime.cleanup_allowed = True
        retried = await facade.cancel_many([unknown.execution_id])
        assert retried.cancelled_execution_ids == (unknown.execution_id,)
        assert runtime.cancelled == [unknown.execution_id, unknown.execution_id]
        assert facade.resolve_handle(unknown.execution_id) is unknown
        assert facade.snapshot(unknown.execution_id).metadata["process_cleanup_status"] == "confirmed"
        assert facade.snapshot(unknown.execution_id).status == ExecutionStatus.FAILED
        await facade.close()
        assert facade.list_known_handles() == []
        with pytest.raises(KeyError):
            facade.snapshot(unknown.execution_id)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_without_cancellation_keeps_active_lookup_but_releases_completed_handle() -> None:
    runtime = ExecutionRuntime(async_concurrency=2)
    facade = ExecutionFacade(runtime=runtime)
    entered = asyncio.Event()
    unblock = asyncio.Event()

    async def running() -> None:
        entered.set()
        await unblock.wait()

    async def completed() -> str:
        return "done"

    active = facade.submit_async_task(AsyncTaskSpec(name="active", coroutine_factory=running))
    finished = facade.submit_async_task(AsyncTaskSpec(name="finished", coroutine_factory=completed))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await facade.wait_one(finished.execution_id, timeout=1) == ExecutionStatus.SUCCESS
        await facade.close(cancel_running=False)
        assert runtime.get_snapshot(active.execution_id).status == ExecutionStatus.RUNNING
        assert facade.snapshot(active.execution_id).status == ExecutionStatus.RUNNING
        assert facade.resolve_handle(active.execution_id) is active
        assert facade.list_known_handles() == [active]
        with pytest.raises(KeyError):
            facade.resolve_handle(finished.execution_id)
        cancelled = await facade.cancel_many([active.execution_id])
        assert cancelled.cancelled_execution_ids == (active.execution_id,)
        assert await facade.wait_one(active.execution_id, timeout=1) == ExecutionStatus.CANCELLED
        await facade.close(cancel_running=False)
        assert facade.list_known_handles() == []
    finally:
        unblock.set()
        await active.cancel()
        await runtime.close()
