"""A delivery drain is not evidence that an owned stop signal was accepted."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process import process_tree
from polaris.kernelone.process.async_contracts import PopenAsyncHandle, ProcessStatus
from polaris.kernelone.process.tests.test_popen_group_stream import _script
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import _release


async def _exit2_child(tmp_path: Path) -> tuple:
    child = await _script(tmp_path, 'import os\nos.write(2,b"READY\\n")\nos.read(0,1)\nraise SystemExit(2)\n')
    assert await asyncio.to_thread(child[0].process.stderr.readline) == "READY\n"
    return child


def _raw_stdin(handle: PopenAsyncHandle) -> None:
    """Controlled fixture input, not qualification of public write_stdin."""
    handle.process.stdin.write("x")
    handle.process.stdin.flush()


async def _stop(handle: PopenAsyncHandle, operation: str) -> None:
    if operation == "terminate":
        await handle.terminate(timeout=0.02)
    else:
        await handle.kill()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["terminate", "kill"])
async def test_denied_stop_does_not_relabel_later_autonomous_exit2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    child = await _exit2_child(tmp_path)
    handle = child[0]
    group = handle._require_group_identity().group_id
    original = process_tree._signal_owned_group
    trace: list[dict[str, object]] = []

    def deny(group_id: int, *, force: bool):
        if group_id == group:
            raise PermissionError("controlled signal denied before dispatch")
        return original(group_id, force=force)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(process_tree, "_signal_owned_group", deny)
            with pytest.raises(PermissionError, match="before dispatch"):
                await _stop(handle, operation)
        trace.append({"after_denied_stop": handle.status.value, "returncode": handle.process.poll()})
        assert handle.process.poll() is None
        await handle.write_stdin("x")
        assert await handle.wait(timeout=5.0) is ProcessStatus.FAILED
        result = await handle.result()
        assert result.status is ProcessStatus.FAILED and result.exit_code == 2
        assert result.timed_out is False
    finally:
        get_default_adapter().write_text(
            str(tmp_path / "stop-cause-trace.json"), json.dumps(trace) + "\n", encoding="utf-8"
        )
        await _release(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["terminate", "kill"])
async def test_pending_denied_signal_cannot_commit_cancelled_before_its_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    child = await _exit2_child(tmp_path)
    handle = child[0]
    group = handle._require_group_identity().group_id
    entered = threading.Event()
    decide = threading.Event()
    observed = threading.Event()
    trace: list[dict[str, object]] = []
    original_signal = process_tree._signal_owned_group
    original_observe = PopenAsyncHandle._owned_drain_confirmed
    stopper: asyncio.Task[None] | None = None
    waiter: asyncio.Task[ProcessStatus] | None = None

    def pending_denial(group_id: int, *, force: bool):
        if group_id != group:
            return original_signal(group_id, force=force)
        entered.set()
        if not decide.wait(5.0):
            raise TimeoutError("controlled signal-decision barrier expired")
        raise PermissionError("controlled pending signal rejected without dispatch")

    def observe(self: PopenAsyncHandle) -> bool:
        physical = original_observe(self)
        if self is handle and entered.is_set() and physical and not decide.is_set() and not observed.is_set():
            trace.append(
                {
                    "event": "physical_exit_while_signal_pending",
                    "status": self.status.value,
                    "exit_code": self.process.poll(),
                }
            )
            observed.set()
        return physical

    try:
        monkeypatch.setattr(process_tree, "_signal_owned_group", pending_denial)
        monkeypatch.setattr(PopenAsyncHandle, "_owned_drain_confirmed", observe)
        stopper = asyncio.create_task(_stop(handle, operation))
        assert await asyncio.to_thread(entered.wait, 5.0)
        await handle.write_stdin("x")
        assert await asyncio.to_thread(handle.process.wait, 5.0) == 2
        waiter = asyncio.create_task(handle.wait(timeout=5.0))
        assert await asyncio.to_thread(observed.wait, 5.0)
        assert trace[0]["status"] == ProcessStatus.RUNNING.value
        decide.set()
        with pytest.raises(PermissionError, match="without dispatch"):
            await stopper
        assert await waiter is ProcessStatus.FAILED
        result = await handle.result()
        assert result.exit_code == 2 and result.status is ProcessStatus.FAILED
    finally:
        decide.set()
        if stopper is not None:
            with pytest.raises(PermissionError, match="without dispatch"):
                await stopper
        if waiter is not None:
            await waiter
        get_default_adapter().write_text(
            str(tmp_path / "stop-cause-trace.json"), json.dumps(trace) + "\n", encoding="utf-8"
        )
        await _release(child)


@pytest.mark.asyncio
async def test_group_without_signal_recipient_does_not_supply_a_stop_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _exit2_child(tmp_path)
    handle = child[0]
    group = handle._require_group_identity().group_id
    original_signal = process_tree._signal_owned_group
    original_killpg = process_tree.os.killpg

    def absent_recipient(group_id: int, signum: int) -> None:
        if group_id == group:
            raise ProcessLookupError("controlled no signal recipient")
        original_killpg(group_id, signum)

    def exit_before_send(group_id: int, *, force: bool):
        if group_id == group:
            handle.process.stdin.write("x")
            handle.process.stdin.flush()
            assert handle.process.wait(timeout=5.0) == 2
        return original_signal(group_id, force=force)

    try:
        monkeypatch.setattr(process_tree.os, "killpg", absent_recipient)
        monkeypatch.setattr(process_tree, "_signal_owned_group", exit_before_send)
        await handle.terminate(timeout=0.02)
        assert await handle.wait(timeout=5.0) is ProcessStatus.FAILED
        assert (await handle.result()).exit_code == 2
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_public_write_stdin_delivers_input_to_the_real_owned_child(tmp_path: Path) -> None:
    child = await _exit2_child(tmp_path)
    try:
        await child[0].write_stdin("x")
        assert await child[0].wait(timeout=5.0) is ProcessStatus.FAILED
        assert (await child[0].result()).exit_code == 2
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_public_write_stdin_rejects_an_actual_closed_pipe(tmp_path: Path) -> None:
    child = await _exit2_child(tmp_path)
    try:
        await asyncio.to_thread(child[0].process.stdin.close)
        assert await child[0].wait(timeout=5.0) is ProcessStatus.FAILED
        with pytest.raises(BrokenPipeError, match="closed"):
            await child[0].write_stdin("x")
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_pending_signal_does_not_block_wait_deadline_or_erase_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _exit2_child(tmp_path)
    handle = child[0]
    group = handle._require_group_identity().group_id
    entered = threading.Event()
    decide = threading.Event()
    original = process_tree._signal_owned_group
    stopper: asyncio.Task[None] | None = None

    def pending(group_id: int, *, force: bool):
        if group_id != group:
            return original(group_id, force=force)
        entered.set()
        if not decide.wait(5.0):
            raise TimeoutError("controlled signal-decision barrier expired")
        raise PermissionError("controlled signal rejected after wait deadline")

    try:
        monkeypatch.setattr(process_tree, "_signal_owned_group", pending)
        stopper = asyncio.create_task(_stop(handle, "terminate"))
        assert await asyncio.to_thread(entered.wait, 5.0)
        assert await asyncio.wait_for(handle.wait(timeout=0.05), 1.0) is ProcessStatus.TIMED_OUT
        assert not stopper.done() and handle.process.poll() is None
        decide.set()
        with pytest.raises(PermissionError, match="after wait deadline"):
            await stopper
        await handle.write_stdin("x")
        assert await handle.wait(timeout=5.0) is ProcessStatus.TIMED_OUT
        result = await handle.result()
        assert result.exit_code == 2 and result.timed_out is True
    finally:
        decide.set()
        if stopper is not None:
            with pytest.raises(PermissionError, match="after wait deadline"):
                await stopper
        await _release(child)
