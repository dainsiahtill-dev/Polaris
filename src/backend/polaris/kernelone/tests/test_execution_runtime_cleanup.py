"""Unknown physical cleanup remains observable and retains its existing owner."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process import async_contracts, process_tree
from polaris.kernelone.process.async_contracts import PopenAsyncHandle
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import (
    _birth,
    _release,
    _spawn,
)
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
    original = process_tree._signal_owned_group
    owned_group = child[0]._require_group_identity().group_id

    def observed_kill(group_id: int, *, force: bool) -> bool:
        if group_id == owned_group and force:
            kills.append(True)
        return original(group_id, force=force)

    monkeypatch.setattr(process_tree, "_signal_owned_group", observed_kill)
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

            original_signal = process_tree._signal_owned_group
            owned_group = child[0]._require_group_identity().group_id

            def denied_group(group_id: int, *, force: bool) -> bool:
                if group_id == owned_group:
                    denied_kill() if force else denied_terminate()
                return original_signal(group_id, force=force)

            patch.setattr(process_tree, "_signal_owned_group", denied_group)
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
        name="cancelled-cleanup",
        lane=ExecutionLane.SUBPROCESS,
        timeout_seconds=1.0,
        metadata={},
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


@pytest.mark.asyncio
async def test_accepted_stop_precedes_a_waiter_observing_its_signal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real group drains while the stop caller has not resumed yet."""
    child = await _spawn(tmp_path, ignore_term=True)
    runtime = ExecutionRuntime(process_concurrency=1)
    state = await _state(runtime, child, timeout=5.0)
    drained = threading.Event()
    return_to_stopper = threading.Event()
    original = async_contracts._stop_and_drain_owned_group
    trace: list[dict[str, object]] = []
    stop_task: asyncio.Task[bool] | None = None

    def stop_then_pause(*args, **kwargs):
        result = original(*args, **kwargs)
        trace.append(
            {
                "event": "real_group_drained",
                "handle_status": child[0].status.value,
                "returncode": child[0].process.poll(),
            }
        )
        drained.set()
        if not return_to_stopper.wait(5.0):
            raise TimeoutError("controlled stop-return barrier expired")
        return result

    monkeypatch.setattr(async_contracts, "_stop_and_drain_owned_group", stop_then_pause)
    try:
        state.task = asyncio.create_task(runtime._await_process_completion(state))
        stop_task = asyncio.create_task(runtime.terminate(state.execution_id, timeout=0.02))
        assert await asyncio.to_thread(drained.wait, 5.0)
        await asyncio.wait_for(state.completed.wait(), 5.0)
        trace.append(
            {
                "event": "waiter_committed_before_stop_return",
                "runtime_status": state.status.value,
                "handle_status": child[0].status.value,
                "cleanup": state.metadata["process_cleanup_status"],
            }
        )
        assert state.status is ExecutionStatus.CANCELLED
        assert state.metadata["process_cleanup_status"] == "confirmed"
        assert state.result.exit_code == child[0].process.poll() == -9
        return_to_stopper.set()
        assert await stop_task is False  # Real force escalation, not graceful success.
        assert state.status is ExecutionStatus.CANCELLED
    finally:
        return_to_stopper.set()
        if stop_task is not None:
            await stop_task
        get_default_adapter().write_text(
            str(tmp_path / "stop-wait-order.json"),
            json.dumps(trace, indent=2) + "\n",
            encoding="utf-8",
        )
        await _finish(runtime, state, child)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,expected", [(0, ExecutionStatus.SUCCESS), (2, ExecutionStatus.FAILED)])
async def test_autonomous_exit_without_stop_intent_keeps_actual_status(
    tmp_path: Path, exit_code: int, expected: ExecutionStatus
) -> None:
    runtime = ExecutionRuntime(process_concurrency=1)
    script = tmp_path / "autonomous_child.py"
    get_default_adapter().write_text(str(script), f"raise SystemExit({exit_code})\n", encoding="utf-8")
    argv = [sys.executable, "-I", "-S", "-B", str(script)]
    home = str(tmp_path / "private-kernel-home")
    public = await runtime.submit_process(
        name="autonomous-exit",
        args=argv,
        cwd=tmp_path,
        env={"KERNELONE_HOME": home, "PYTHONUTF8": "1", "LANG": "C.UTF-8"},
        timeout_seconds=5.0,
    )
    state = runtime._require_state(public.execution_id)
    child = (state.process_handle, _birth(public.pid), argv, home)
    try:
        assert await public.wait(timeout=5.0) is expected
        snapshot = public.snapshot()
        assert snapshot.result.exit_code == exit_code
        assert snapshot.metadata["process_cleanup_status"] == "confirmed"
        assert snapshot.result.timed_out is False
    finally:
        await _finish(runtime, state, child)
