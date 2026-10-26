"""Context exit retains live process resources when cleanup is unknown."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import TextIO

import pytest
from polaris.kernelone.process import ProcessTreeDrainError, process_tree
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import _birth, _release, _spawn


class LockedReader:
    """Real pipe reader with an explicit observable pump-held read lock.

    The added lock/barrier makes a valid blocked-reader schedule deterministic;
    it does not claim a historical OS scheduler trace.
    """

    def __init__(self, original: TextIO) -> None:
        self.original = original
        self.lock = threading.Lock()
        self.reading = threading.Event()
        self.done = threading.Event()
        self.close_entered = threading.Event()

    def __iter__(self) -> Iterator[str]:
        try:
            with self.lock:
                self.reading.set()
                yield from self.original
        finally:
            self.done.set()

    def close(self) -> None:
        self.close_entered.set()
        with self.lock:
            self.original.close()

    @property
    def closed(self) -> bool:
        return self.original.closed


async def _cleanup(child: tuple) -> None:
    proc = child[0].process
    # Pre-fix context exit closes its own stdin, unblocking this bounded probe.
    # Waiting on our Popen object reaps it without signalling an uncertain PID.
    if all(pipe.closed for pipe in (proc.stdout, proc.stderr, proc.stdin)):
        await asyncio.to_thread(proc.wait, 5.0)
    await _release(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_operation", ["terminate", "poll"])
async def test_unknown_exit_does_not_close_a_pump_locked_pipe_and_same_handle_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_operation: str
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    proc = handle.process
    stdout, stderr = LockedReader(proc.stdout), LockedReader(proc.stderr)
    proc.stdout, proc.stderr = stdout, stderr
    handle._start_pumps()
    handle._stream_done = True
    assert await asyncio.to_thread(stdout.reading.wait, 2.0)
    assert await asyncio.to_thread(stderr.reading.wait, 2.0)
    exiting = None
    try:
        with monkeypatch.context() as patch:

            def unavailable() -> None:
                raise PermissionError("controlled context cleanup denied")

            if failed_operation == "poll":
                patch.setattr(proc, "poll", unavailable)
            else:
                original_signal = process_tree._signal_owned_group
                owned_group = handle._require_group_identity().group_id

                def unavailable_group(group_id: int, *, force: bool) -> bool:
                    if group_id == owned_group:
                        unavailable()
                    return original_signal(group_id, force=force)

                patch.setattr(process_tree, "_signal_owned_group", unavailable_group)
            # An independent loop prevents the pre-fix synchronous pipe.close
            # deadlock from blocking the test's owned-child cleanup watchdog.
            exiting = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(handle.__aexit__(None, None, None))))
            done, _ = await asyncio.wait({exiting}, timeout=0.25)
            assert exiting in done, "context exit blocked on a live pump read lock"
            with pytest.raises(ProcessTreeDrainError, match="process_context_cleanup_unproven") as error:
                await exiting
            assert isinstance(error.value.__cause__, PermissionError)
            assert _birth(handle.pid) == child[1]
            assert not stdout.close_entered.is_set() and not stderr.close_entered.is_set()
            assert not proc.stdin.closed and handle._proc_alive is True
        await handle.__aexit__(None, None, None)
        assert proc.poll() is not None and handle._proc_alive is False
        assert stdout.closed and stderr.closed and proc.stdin.closed
        assert await asyncio.to_thread(stdout.done.wait, 2.0)
        assert await asyncio.to_thread(stderr.done.wait, 2.0)
    finally:
        await _cleanup(child)
        if exiting is not None:
            with suppress(PermissionError, ProcessTreeDrainError):
                await exiting  # Expected cleanup error already checked above.


@pytest.mark.asyncio
async def test_already_exited_context_closes_pipes_normally(tmp_path: Path) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    try:
        await handle.terminate(timeout=0.02)
        assert handle.process.poll() is not None
        await handle.__aexit__(None, None, None)
        assert all(pipe.closed for pipe in (handle.process.stdout, handle.process.stderr, handle.process.stdin))
        assert handle._proc_alive is False
    finally:
        await _cleanup(child)


@pytest.mark.asyncio
async def test_original_body_exception_remains_in_unknown_cleanup_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    original = ValueError("original context body")
    try:
        with monkeypatch.context() as patch:

            def denied() -> None:
                raise PermissionError("controlled context cleanup denied")

            original_signal = process_tree._signal_owned_group
            owned_group = handle._require_group_identity().group_id

            def denied_group(group_id: int, *, force: bool) -> bool:
                if group_id == owned_group:
                    denied()
                return original_signal(group_id, force=force)

            patch.setattr(process_tree, "_signal_owned_group", denied_group)
            with pytest.raises(ProcessTreeDrainError, match="process_context_cleanup_unproven") as error:
                async with handle:
                    raise original
            assert error.value.__cause__ is original
            assert any("PermissionError" in note for note in error.value.__notes__)
            assert not handle.process.stdin.closed
    finally:
        await _cleanup(child)


@pytest.mark.asyncio
async def test_original_cancellation_is_not_replaced_by_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    original = asyncio.CancelledError("original cancellation")
    try:
        with monkeypatch.context() as patch:

            def denied() -> None:
                raise PermissionError("controlled context cleanup denied")

            original_signal = process_tree._signal_owned_group
            owned_group = handle._require_group_identity().group_id

            def denied_group(group_id: int, *, force: bool) -> bool:
                if group_id == owned_group:
                    denied()
                return original_signal(group_id, force=force)

            patch.setattr(process_tree, "_signal_owned_group", denied_group)
            with pytest.raises(asyncio.CancelledError) as error:
                async with handle:
                    raise original
            assert error.value is original
            assert isinstance(original.__cause__, ProcessTreeDrainError)
            assert any("process_context_cleanup_unproven" in note for note in original.__notes__)
            assert not handle.process.stdin.closed
    finally:
        await _cleanup(child)


@pytest.mark.asyncio
async def test_new_cleanup_cancellation_propagates_with_unknown_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    cancelled = asyncio.CancelledError("cleanup interrupted")
    try:
        with monkeypatch.context() as patch:

            def interrupt() -> None:
                raise cancelled

            original_signal = process_tree._signal_owned_group
            owned_group = handle._require_group_identity().group_id

            def interrupted_group(group_id: int, *, force: bool) -> bool:
                if group_id == owned_group:
                    interrupt()
                return original_signal(group_id, force=force)

            patch.setattr(process_tree, "_signal_owned_group", interrupted_group)
            with pytest.raises(asyncio.CancelledError) as error:
                await handle.__aexit__(None, None, None)
            assert error.value is cancelled
            assert any("process_context_cleanup_unproven" in note for note in cancelled.__notes__)
            assert not handle.process.stdin.closed
    finally:
        await _cleanup(child)
