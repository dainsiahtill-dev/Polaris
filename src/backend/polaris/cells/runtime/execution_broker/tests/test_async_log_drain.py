"""Tests for off-loop log draining in ``runtime.execution_broker``.

These cover :class:`_AsyncLineLog` and the ``_drain_stream_to_log`` hot path,
verifying that directory creation, the initial ``open`` and every ``write``/
``flush`` are offloaded to a worker thread (never run inline on the event loop)
while preserving append ordering and UTF-8 content.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.execution_broker.internal import service as broker_service_module
from polaris.cells.runtime.execution_broker.internal.service import _AsyncLineLog
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import ExecutionBrokerService
from polaris.kernelone.runtime import ExecutionFacade, ExecutionRuntime


@pytest.mark.asyncio
async def test_async_line_log_preserves_order_and_utf8(tmp_path: Path) -> None:
    """_AsyncLineLog must append buffered lines in order with UTF-8 encoding."""
    # Arrange
    log_path = tmp_path / "nested" / "ordered.log"
    log = await _AsyncLineLog.open(str(log_path))

    # Act
    for index in range(5):
        log.write_line(f"line-{index}-数据\n")
    await log.flush()
    log.write_line("tail-行\n")
    await log.close()

    # Assert
    content = log_path.read_text(encoding="utf-8")
    assert content == "line-0-数据\nline-1-数据\nline-2-数据\nline-3-数据\nline-4-数据\ntail-行\n"


@pytest.mark.asyncio
async def test_async_line_log_open_creates_parent_off_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening the log must create the parent directory via a worker thread, not inline."""
    # Arrange
    log_path = tmp_path / "deep" / "auto" / "created.log"
    to_thread_calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        to_thread_calls.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(broker_service_module.asyncio, "to_thread", _tracking_to_thread)

    # Act
    log = await _AsyncLineLog.open(str(log_path))
    await log.close()

    # Assert
    assert log_path.parent.is_dir()
    # open_text_log_append (which performs makedirs + open) was offloaded.
    assert "open_text_log_append" in to_thread_calls


@pytest.mark.asyncio
async def test_async_line_log_flush_is_offloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Buffered writes must be persisted through asyncio.to_thread, never inline."""
    # Arrange
    log_path = tmp_path / "flush.log"
    log = await _AsyncLineLog.open(str(log_path))
    offloaded_payloads: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        if getattr(func, "__name__", "") == "_write_and_flush" and args:
            offloaded_payloads.append(str(args[0]))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(broker_service_module.asyncio, "to_thread", _tracking_to_thread)

    # Act
    log.write_line("alpha\n")
    log.write_line("beta\n")
    assert log.pending_lines == 2  # buffered, no I/O yet
    await log.flush()
    await log.close()

    # Assert
    assert offloaded_payloads == ["alpha\nbeta\n"]
    assert log.pending_lines == 0
    assert log_path.read_text(encoding="utf-8") == "alpha\nbeta\n"


@pytest.mark.asyncio
async def test_async_line_log_flush_noop_when_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Flushing an empty buffer must not offload any work."""
    # Arrange
    log_path = tmp_path / "empty.log"
    log = await _AsyncLineLog.open(str(log_path))
    write_calls = 0
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        nonlocal write_calls
        if getattr(func, "__name__", "") == "_write_and_flush":
            write_calls += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(broker_service_module.asyncio, "to_thread", _tracking_to_thread)

    # Act
    await log.flush()
    await log.close()

    # Assert
    assert write_calls == 0


@pytest.mark.asyncio
async def test_drain_stream_offloads_all_file_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end drain must keep file I/O off the loop while capturing output in order."""
    # Arrange
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "drain.log"

    inline_io_calls = 0
    real_to_thread = asyncio.to_thread

    async def _tracking_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(broker_service_module.asyncio, "to_thread", _tracking_to_thread)

    # Forbid inline writes/flushes on the captured text handle: any direct call
    # would indicate I/O leaking onto the event loop instead of the worker thread.
    real_init = _AsyncLineLog.__init__

    def _guarded_init(self: _AsyncLineLog, handle: Any) -> None:
        nonlocal inline_io_calls
        real_write = handle.write
        real_flush = handle.flush

        def _guard(target: Any) -> Any:
            def _wrapped(*a: Any, **k: Any) -> Any:
                nonlocal inline_io_calls
                import threading

                if threading.current_thread() is threading.main_thread():
                    inline_io_calls += 1
                return target(*a, **k)

            return _wrapped

        handle.write = _guard(real_write)
        handle.flush = _guard(real_flush)
        real_init(self, handle)

    monkeypatch.setattr(_AsyncLineLog, "__init__", _guarded_init)

    command = LaunchExecutionProcessCommandV1(
        name="drain-offload-test",
        args=(
            sys.executable,
            "-c",
            "import sys; [print(f'数据-{i}') for i in range(50)]; sys.stdout.flush()",
        ),
        workspace=str(tmp_path),
        timeout_seconds=10.0,
        log_path=str(log_path),
        metadata={"test_case": "drain_offloads_all_file_io"},
    )

    try:
        # Act
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None
        wait_result = await broker.wait_process(launch.handle, timeout_seconds=10.0)

        # Assert
        assert wait_result.success is True
        assert wait_result.status == ExecutionProcessStatusV1.SUCCESS
        assert inline_io_calls == 0, "file write/flush ran on the event loop thread"

        content = log_path.read_text(encoding="utf-8")
        assert "[execution_broker] launched" in content
        assert "[execution_broker] terminal" in content
        # All emitted process lines are present and in order.
        emitted = [line for line in content.splitlines() if line.startswith("数据-")]
        assert emitted == [f"数据-{i}" for i in range(50)]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_drain_open_failure_unregisters_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the log file cannot be opened, the drain task must still clear its registry slot."""
    # Arrange
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    async def _failing_open(_log_path: str) -> _AsyncLineLog:
        raise OSError("disk full")

    monkeypatch.setattr(broker_service_module._AsyncLineLog, "open", _failing_open)

    command = LaunchExecutionProcessCommandV1(
        name="drain-open-failure-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=str(tmp_path),
        timeout_seconds=10.0,
        log_path=str(tmp_path / "unwritable.log"),
        metadata={"test_case": "drain_open_failure_unregisters_task"},
    )

    try:
        # Act
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None
        execution_id = launch.handle.execution_id
        # Allow the drain task to run and hit the open failure.
        await broker._await_log_drain(execution_id)

        # Assert: registry slot was released despite the open failure.
        assert execution_id not in broker._process_log_tasks

        # The process itself still completes normally.
        wait_result = await broker.wait_process(launch.handle, timeout_seconds=10.0)
        assert wait_result.status == ExecutionProcessStatusV1.SUCCESS
    finally:
        await runtime.close()
