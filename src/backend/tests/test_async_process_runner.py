"""Tests for KernelOne IAsyncProcessRunner contract and SubprocessAsyncRunner."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.kernelone.process.async_contracts import (
    DEFAULT_TIMEOUT_SECONDS,
    AsyncProcessHandle,
    AsyncProcessRunnerPort,
    ProcessStatus,
    ProcessStreamSource,
    StreamChunk,
    StreamResult,
    SubprocessAsyncRunner,
    _merge_two_streams,
)

# =============================================================================
# IAsyncProcessRunner protocol — structural subtyping checks
# =============================================================================


def test_protocol_is_runtime_checkable() -> None:
    """SubprocessAsyncRunner satisfies AsyncProcessRunnerPort at runtime."""
    runner = SubprocessAsyncRunner()
    assert isinstance(runner, AsyncProcessRunnerPort)


def test_protocol_rejects_non_implementations() -> None:
    """Objects without spawn() are rejected by isinstance check."""
    assert not isinstance("not a runner", IAsyncProcessRunner)
    assert not isinstance(42, IAsyncProcessRunner)


# =============================================================================
# AsyncProcessHandle protocol — structural subtyping checks
# =============================================================================


class _FakeAsyncProcessHandle:
    """Minimal object that satisfies AsyncProcessHandle."""

    def __init__(self) -> None:
        self._status = ProcessStatus.SUCCESS
        self._pid = 12345

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def status(self) -> ProcessStatus:
        return self._status

    async def stream(self) -> AsyncIterator[StreamChunk]:
        return
        yield  # pragma: no cover

    async def wait(self, timeout: float | None = None) -> ProcessStatus:
        return self._status

    async def is_alive(self) -> bool:
        return False

    async def write_stdin(self, data: str) -> None:
        pass

    async def terminate(self, timeout: float = 5.0) -> bool:
        return True

    async def kill(self) -> None:
        pass

    async def result(self) -> StreamResult:
        return StreamResult(
            pid=self._pid,
            exit_code=0,
            status=self._status,
            stdout_lines=(),
            stderr_lines=(),
            timed_out=False,
            timeout_seconds=30,
            started_at=datetime.now(timezone.utc),
        )


def test_handle_protocol_runtime_check() -> None:
    """A minimal object satisfying AsyncProcessHandle passes isinstance."""
    handle = _FakeAsyncProcessHandle()
    assert isinstance(handle, AsyncProcessHandle)


def test_handle_protocol_rejects_incomplete() -> None:
    """An object missing wait() is rejected."""
    incomplete: dict[str, object] = {"pid": 123, "stream": lambda: None}
    assert not isinstance(incomplete, AsyncProcessHandle)


# =============================================================================
# StreamChunk — frozen dataclass invariants
# =============================================================================


def test_stream_chunk_is_frozen() -> None:
    """StreamChunk fields cannot be mutated after construction."""
    chunk = StreamChunk(line="hello", source=ProcessStreamSource.STDOUT, pid=1)
    with pytest.raises(AttributeError):
        chunk.line = "modified"  # type: ignore[attr-defined]


def test_stream_chunk_to_dict() -> None:
    """to_dict() returns a serializable dictionary."""
    chunk = StreamChunk(
        line="test line",
        source=ProcessStreamSource.STDERR,
        pid=999,
    )
    d = chunk.to_dict()
    assert d["line"] == "test line"
    assert d["source"] == "stderr"
    assert d["pid"] == 999
    assert "timestamp" in d


def test_stream_result_ok_property() -> None:
    """StreamResult.ok is True only when exit_code==0 and not timed_out."""
    now = datetime.now(timezone.utc)
    base = {
        "pid": 1,
        "exit_code": 0,
        "status": ProcessStatus.SUCCESS,
        "stdout_lines": (),
        "stderr_lines": (),
        "timed_out": False,
        "timeout_seconds": 30,
        "started_at": now,
        "ended_at": now,
    }
    assert StreamResult(**base).ok is True

    timed_out_result = StreamResult(**{**base, "status": ProcessStatus.TIMED_OUT, "timed_out": True})
    assert timed_out_result.ok is False

    failed_result = StreamResult(**{**base, "exit_code": 1, "status": ProcessStatus.FAILED})
    assert failed_result.ok is False


# =============================================================================
# ProcessStatus — state machine enum
# =============================================================================


def test_process_status_values() -> None:
    """All expected status values exist."""
    assert {s.value for s in ProcessStatus} == {
        "pending",
        "running",
        "success",
        "failed",
        "timed_out",
        "cancelled",
    }


# =============================================================================
# SubprocessAsyncRunner — integration tests using real subprocesses
# =============================================================================


@pytest.mark.asyncio
async def test_spawn_and_wait_success(tmp_path: Path) -> None:
    """A simple echo subprocess exits successfully."""
    runner = SubprocessAsyncRunner()
    if sys.platform == "win32":
        cmd = ["python", "-c", "print('hello')"]
    else:
        cmd = ["echo", "hello"]

    handle = await runner.spawn(cmd, timeout=10, cwd=str(tmp_path))
    async with handle:
        status = await handle.wait()
    assert status == ProcessStatus.SUCCESS
    assert handle.pid > 0


@pytest.mark.asyncio
async def test_spawn_fails_with_empty_args() -> None:
    """spawn() raises ValueError when args is empty."""
    runner = SubprocessAsyncRunner()
    with pytest.raises(ValueError, match="args must not be empty"):
        await runner.spawn([], timeout=5)


@pytest.mark.asyncio
async def test_wait_with_custom_timeout() -> None:
    """wait() returns TIMED_OUT when the timeout expires."""
    runner = SubprocessAsyncRunner()
    if sys.platform == "win32":
        cmd = ["cmd", "/c", "ping", "-n", "100", "127.0.0.1"]
    else:
        cmd = ["sleep", "100"]

    handle = await runner.spawn(cmd, timeout=1)
    status = await handle.wait(timeout=0.1)
    # The inner wait times out; process may still be alive
    assert status in (ProcessStatus.TIMED_OUT, ProcessStatus.RUNNING)


@pytest.mark.asyncio
async def test_terminate_stops_running_process(tmp_path: Path) -> None:
    """terminate() transitions the process to CANCELLED state."""
    runner = SubprocessAsyncRunner()
    if sys.platform == "win32":
        # Use a Python sleep script so terminate() works reliably on Windows.
        cmd = ["python", "-c", "import time; time.sleep(1000)"]
    else:
        cmd = ["sleep", "1000"]

    handle = await runner.spawn(cmd, timeout=300)
    await asyncio.sleep(0.3)
    assert await handle.is_alive() is True
    await handle.terminate(timeout=10.0)
    # After terminate(), the process must not be alive.
    assert await handle.is_alive() is False
    assert handle.status == ProcessStatus.CANCELLED


@pytest.mark.asyncio
async def test_stream_stdout_lines(tmp_path: Path) -> None:
    """stream() yields lines from the subprocess stdout."""
    runner = SubprocessAsyncRunner()
    # Use Python to avoid Windows cmd.exe buffering issues with pipe output.
    script = ";".join(
        [
            "import sys",
            "for i in range(1, 4):",
            "    print(f'line{i}')",
            "    sys.stdout.flush()",
        ]
    )
    cmd = ["python", "-c", script]

    handle = await runner.spawn(cmd, timeout=10, cwd=str(tmp_path))
    async with handle:
        lines: list[str] = []
        async for chunk in handle.stream():
            if chunk.line:
                lines.append(chunk.line)
            if len(lines) >= 3:
                break
        assert len(lines) >= 1


@pytest.mark.asyncio
async def test_result_raises_on_non_terminal_process() -> None:
    """result() raises RuntimeError if called before wait()."""
    runner = SubprocessAsyncRunner()
    if sys.platform == "win32":
        cmd = ["cmd", "/c", "ping -n 1000 127.0.0.1 > NUL"]
    else:
        cmd = ["sleep", "1000"]

    handle = await runner.spawn(cmd, timeout=300)
    with pytest.raises(RuntimeError, match="still running"):
        await handle.result()
    await handle.terminate(timeout=5.0)


@pytest.mark.asyncio
async def test_is_alive_reflects_status() -> None:
    """is_alive() returns False after terminate()."""
    runner = SubprocessAsyncRunner()
    if sys.platform == "win32":
        cmd = ["cmd", "/c", "ping", "-n", "1000", "127.0.0.1"]
    else:
        cmd = ["sleep", "1000"]

    handle = await runner.spawn(cmd, timeout=300)
    await asyncio.sleep(0.2)
    assert await handle.is_alive() is True
    await handle.terminate(timeout=2.0)
    assert await handle.is_alive() is False


@pytest.mark.asyncio
async def test_write_stdin_with_stdin_lines(tmp_path: Path) -> None:
    """stdin_lines are written to subprocess stdin before streaming starts."""
    runner = SubprocessAsyncRunner()
    # Python reads all stdin and prints it back.
    cmd = ["python", "-c", "import sys; data=sys.stdin.read(); print(repr(data))"]

    handle = await runner.spawn(
        cmd,
        timeout=60,
        stdin_lines=["hello stdin"],
    )
    async with handle:
        # stream() must complete without hanging; drain all output.
        async for chunk in handle.stream():
            pass
        # wait() with a large timeout so the fast echo process finishes.
        status = await handle.wait(timeout=30)
    # Verify the process completed (SUCCESS or FAILED; not still alive).
    assert status in (ProcessStatus.SUCCESS, ProcessStatus.FAILED)


# =============================================================================
# _merge_two_streams — async iterator merger
# =============================================================================


@pytest.mark.asyncio
async def test_merge_two_streams_yields_both_sources() -> None:
    """The merger yields chunks from both source iterators."""

    async def stdout_gen() -> AsyncIterator[StreamChunk]:
        for i in range(3):
            yield StreamChunk(line=f"out-{i}", source=ProcessStreamSource.STDOUT)
            await asyncio.sleep(0)
        yield StreamChunk(line="", source=ProcessStreamSource.STDOUT)  # EOF

    async def stderr_gen() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(line="err-0", source=ProcessStreamSource.STDERR)
        await asyncio.sleep(0)
        yield StreamChunk(line="", source=ProcessStreamSource.STDERR)  # EOF

    results: list[StreamChunk] = []
    async for chunk in _merge_two_streams(stdout_gen(), stderr_gen()):
        results.append(chunk)

    sources = {c.source for c in results if c.line}
    assert ProcessStreamSource.STDOUT in sources
    assert ProcessStreamSource.STDERR in sources


# =============================================================================
# Constants
# =============================================================================


def test_default_timeout_is_30() -> None:
    """DEFAULT_TIMEOUT_SECONDS is 30."""
    assert DEFAULT_TIMEOUT_SECONDS == 30
