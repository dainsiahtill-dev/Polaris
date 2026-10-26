"""Unknown physical cleanup remains observable and retains its existing owner."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.kernelone.process.async_contracts import PopenAsyncHandle
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import _release, _spawn
from polaris.kernelone.runtime import execution_runtime as owner
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionStatus,
)


async def _state(runtime: ExecutionRuntime, child: tuple, timeout: float = 0.02):
    await runtime._process_semaphore.acquire()
    state = runtime._register_state(
        name="owned-cleanup-probe",
        lane=ExecutionLane.SUBPROCESS,
        timeout_seconds=timeout,
        metadata={"process_cleanup_status": "confirmed"},
    )
    state.status = ExecutionStatus.RUNNING
    state.started_at = datetime.now(timezone.utc)
    state.process_handle = child[0]
    state.pid = child[0].pid
    return state


async def _finish(runtime: ExecutionRuntime, state, child: tuple) -> None:
    if state.task is not None and not state.task.done():
        state.task.cancel()
        with suppress(asyncio.CancelledError):
            await state.task
    await _release(child)
    await runtime.close()


@pytest.mark.asyncio
async def test_real_deadline_force_cleanup_preserves_timeout_and_actual_exitcode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path, ignore_term=True)
    runtime = ExecutionRuntime(process_concurrency=1)
    state = await _state(runtime, child)
    kills: list[bool] = []
    original = child[0].process.kill

    def observed_kill() -> None:
        kills.append(True)
        original()

    monkeypatch.setattr(child[0].process, "kill", observed_kill)
    try:
        state.task = asyncio.create_task(runtime._await_process_completion(state))
        await state.task
        snapshot = ExecutionHandle(runtime, state.execution_id).snapshot()
        assert snapshot.status is ExecutionStatus.TIMED_OUT and snapshot.ok is False
        assert snapshot.metadata["process_cleanup_status"] == "confirmed"
        assert child[0].process.poll() == snapshot.result.exit_code == -9
        assert snapshot.result.timed_out is True
        assert kills == [True]
    finally:
        await _finish(runtime, state, child)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,retry", [(OSError, "cancel"), (Exception, "close")])
async def test_cleanup_failure_keeps_unknown_snapshot_and_owner_for_later_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception], retry: str
) -> None:
    child = await _spawn(tmp_path)
    runtime = ExecutionRuntime(process_concurrency=1)
    state = await _state(runtime, child)
    public = ExecutionHandle(runtime, state.execution_id)
    try:
        with monkeypatch.context() as patch:

            def denied_terminate() -> None:
                raise error("controlled terminate unavailable")

            def denied_kill() -> None:
                raise error("controlled kill unavailable")

            patch.setattr(child[0].process, "terminate", denied_terminate)
            patch.setattr(child[0].process, "kill", denied_kill)
            state.task = asyncio.create_task(runtime._await_process_completion(state))
            await state.task
            assert state.completed.is_set()  # Logical processing, not physical drain.
            snapshot = public.snapshot()
            assert snapshot.status is ExecutionStatus.TIMED_OUT and snapshot.ok is False
            assert snapshot.metadata["process_cleanup_status"] == "unknown"
            assert "process_cleanup_unproven" in snapshot.error
            assert snapshot.result is None and child[0].process.poll() is None
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(runtime._process_semaphore.acquire(), 0.01)
            assert snapshot.to_dict()["metadata"]["process_cleanup_status"] == "unknown"
            patch.setattr(owner, "EXECUTION_MAX_TERMINAL_STATES", 0)
            await runtime._compact_states()
            assert public.process is child[0].process
            if retry == "close":
                await runtime.close()
                assert public.snapshot().metadata["process_cleanup_status"] == "unknown"
                assert child[0].process.poll() is None
        # Original physical methods restored. This is a separate explicit owner
        # request, not an automatic retry or new execution identity.
        if retry == "cancel":
            assert await public.cancel() is True
        else:
            assert await public.terminate(timeout=0.02) is True
        final = public.snapshot()
        assert final.status is ExecutionStatus.TIMED_OUT
        assert final.metadata["process_cleanup_status"] == "confirmed"
        assert final.result.exit_code == child[0].process.returncode
        assert child[0].process.poll() is not None
        await asyncio.wait_for(runtime._process_semaphore.acquire(), 0.1)
        runtime._process_semaphore.release()
    finally:
        await _finish(runtime, state, child)


@pytest.mark.asyncio
async def test_true_terminate_return_cannot_replace_a_physical_exit_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    runtime = ExecutionRuntime(process_concurrency=1)
    state = await _state(runtime, child)
    try:
        with monkeypatch.context() as patch:
            original_terminate = PopenAsyncHandle.terminate

            async def dishonest_return(self: PopenAsyncHandle, timeout: float = 5.0) -> bool:
                if self is child[0]:
                    return True
                return await original_terminate(self, timeout=timeout)

            patch.setattr(PopenAsyncHandle, "terminate", dishonest_return)
            state.task = asyncio.create_task(runtime._await_process_completion(state))
            await state.task
        assert state.metadata["process_cleanup_status"] == "confirmed"
        assert child[0].process.poll() == -9
    finally:
        await _finish(runtime, state, child)


@pytest.mark.asyncio
async def test_cleanup_cancel_is_not_swallowed_or_marked_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    runtime = ExecutionRuntime()
    state = runtime._register_state(
        name="cancelled-cleanup", lane=ExecutionLane.SUBPROCESS, timeout_seconds=1.0, metadata={}
    )
    state.process_handle = child[0]
    state.status = ExecutionStatus.RUNNING
    try:
        with monkeypatch.context() as patch:
            original_terminate = PopenAsyncHandle.terminate

            async def cancelled(self: PopenAsyncHandle, timeout: float = 5.0) -> bool:
                if self is child[0]:
                    raise asyncio.CancelledError
                return await original_terminate(self, timeout=timeout)

            patch.setattr(PopenAsyncHandle, "terminate", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await runtime._handle_process_timeout(state, child[0])
        assert state.metadata["process_cleanup_status"] == "unknown"
        assert child[0].process.poll() is None
    finally:
        await _release(child)
        await runtime.close()


@pytest.mark.asyncio
async def test_compaction_preserves_an_existing_unknown_live_process_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    runtime = ExecutionRuntime()
    state = runtime._register_state(
        name="retained-owner",
        lane=ExecutionLane.SUBPROCESS,
        timeout_seconds=1.0,
        metadata={"process_cleanup_status": "unknown"},
    )
    state.process_handle = child[0]
    state.pid = child[0].pid
    state.status = ExecutionStatus.TIMED_OUT
    public = ExecutionHandle(runtime, state.execution_id)
    try:
        assert await child[0].is_alive() is True
        monkeypatch.setattr(owner, "EXECUTION_MAX_TERMINAL_STATES", 0)
        await runtime._compact_states()
        assert public.process is child[0].process
        assert public.snapshot().metadata["process_cleanup_status"] == "unknown"
    finally:
        await _release(child)
        await runtime.close()
