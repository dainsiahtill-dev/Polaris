"""Runtime must preserve the actual Popen cause after a rejected stop."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process import process_tree
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import _birth, _release
from polaris.kernelone.runtime.execution_facade import ExecutionFacade, ProcessSpec
from polaris.kernelone.runtime.execution_runtime import ExecutionRuntime, ExecutionStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["terminate", "cancel"])
async def test_public_runtime_rejected_stop_retains_autonomous_exit2_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    fs = get_default_adapter()
    script = tmp_path / "runtime_exit2.py"
    fs.write_text(str(script), "import os\nos.read(0,1)\nraise SystemExit(2)\n", encoding="utf-8")
    argv = [sys.executable, "-I", "-S", "-B", str(script)]
    home = str(tmp_path / "private-kernel-home")
    runtime = ExecutionRuntime(process_concurrency=1)
    public = await runtime.submit_process(
        name="rejected-stop-autonomous-exit2",
        args=argv,
        cwd=tmp_path,
        env={"KERNELONE_HOME": home, "PYTHONUTF8": "1", "LANG": "C.UTF-8"},
        timeout_seconds=5.0,
    )
    state = runtime._require_state(public.execution_id)
    handle = state.process_handle
    child = (handle, _birth(handle.pid), argv, home)
    group = handle._require_group_identity().group_id
    original = process_tree._signal_owned_group

    def denied(group_id: int, *, force: bool):
        if group_id == group:
            raise PermissionError("controlled Runtime signal denied before dispatch")
        return original(group_id, force=force)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(process_tree, "_signal_owned_group", denied)
            stopped = await public.terminate(timeout=0.02) if action == "terminate" else await public.cancel()
            assert stopped is False
            assert public.snapshot().metadata["process_cleanup_status"] == "unknown"
            assert handle.process.poll() is None
        await handle.write_stdin("x")
        assert await public.wait(timeout=5.0) is ExecutionStatus.FAILED
        snapshot = public.snapshot()
        assert snapshot.status is ExecutionStatus.FAILED and snapshot.ok is False
        assert snapshot.result.exit_code == 2
        assert snapshot.metadata["process_cleanup_status"] == "confirmed"
    finally:
        if state.task is not None and not state.task.done():
            state.task.cancel()
            with suppress(asyncio.CancelledError):
                await state.task
        await _release(child)
        await runtime.close()


@pytest.mark.asyncio
async def test_real_facade_close_retains_unknown_id_until_same_owner_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = get_default_adapter()
    script = tmp_path / "facade_child.py"
    fs.write_text(str(script), "import os\nos.read(0,1)\n", encoding="utf-8")
    argv = [sys.executable, "-I", "-S", "-B", str(script)]
    home = str(tmp_path / "private-kernel-home")
    runtime = ExecutionRuntime(process_concurrency=1)
    facade = ExecutionFacade(runtime)
    public = await facade.submit_process(
        ProcessSpec(
            name="real-facade-unknown-owner",
            args=argv,
            cwd=tmp_path,
            env={"KERNELONE_HOME": home, "PYTHONUTF8": "1", "LANG": "C.UTF-8"},
            timeout_seconds=30.0,
        )
    )
    state = runtime._require_state(public.execution_id)
    handle = state.process_handle
    child = (handle, _birth(handle.pid), argv, home)
    group = handle._require_group_identity().group_id
    original = process_tree._signal_owned_group

    def denied(group_id: int, *, force: bool):
        if group_id == group:
            raise PermissionError("controlled real-facade signal denied")
        return original(group_id, force=force)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(process_tree, "_signal_owned_group", denied)
            await facade.close()
            snapshot = facade.snapshot(public.execution_id)
            assert snapshot.metadata["process_cleanup_status"] == "unknown"
            assert snapshot.ok is False and handle.process.poll() is None
            assert facade.resolve_handle(public.execution_id) is public
        canceled = await facade.cancel_many([public.execution_id])
        assert canceled.cancelled_execution_ids == (public.execution_id,)
        snapshot = facade.snapshot(public.execution_id)
        assert snapshot.status is ExecutionStatus.CANCELLED and snapshot.ok is False
        assert snapshot.metadata["process_cleanup_status"] == "confirmed"
        assert snapshot.result.exit_code == handle.process.poll() == -15
        assert facade.resolve_handle(public.execution_id) is public
        await facade.close()
        assert facade.list_known_handles() == []
        with pytest.raises(KeyError, match="Unknown execution handle"):
            facade.snapshot(public.execution_id)
        assert public.snapshot().metadata["process_cleanup_status"] == "confirmed"
    finally:
        if state.task is not None and not state.task.done():
            state.task.cancel()
            with suppress(asyncio.CancelledError):
                await state.task
        await _release(child)
        await runtime.close()
