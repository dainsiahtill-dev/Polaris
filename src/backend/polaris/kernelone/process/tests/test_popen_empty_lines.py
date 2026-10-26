"""A genuine blank line must never become process-stream EOF."""

from __future__ import annotations

import asyncio
import io
import sys
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process.async_contracts import (
    PopenAsyncHandle,
    ProcessStatus,
    ProcessStreamSource,
    SubprocessPopenRunner,
)


def _birth(pid: int) -> str:
    raw = get_default_adapter().read_bytes(f"/proc/{pid}/stat").decode("utf-8")
    return raw[raw.rfind(")") + 2 :].split()[19]


def _pumps(handle: PopenAsyncHandle) -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name in {f"popen-stdout-{handle.pid}", f"popen-stderr-{handle.pid}"}
    ]


async def _pump_before_consumer(handle: PopenAsyncHandle) -> None:
    # A scheduling barrier makes the EOF-queued-before-consumer interleaving
    # deterministic. All reads/normalization/queue writes use the real pumps.
    handle._start_pumps()
    handle._stream_done = True
    owned = _pumps(handle)
    for thread in owned:
        await asyncio.to_thread(thread.join, 5.0)
    assert not any(thread.is_alive() for thread in owned)


async def _release(handle: PopenAsyncHandle, birth: str) -> None:
    if handle.process.poll() is None:
        assert _birth(handle.pid) == birth
        await handle.terminate(timeout=1.0)
    owned = _pumps(handle)
    for thread in owned:
        await asyncio.to_thread(thread.join, 5.0)
    assert not any(thread.is_alive() for thread in owned)
    await handle.__aexit__(None, None, None)
    assert handle.process.poll() is not None
    assert all(
        pipe is None or pipe.closed for pipe in (handle.process.stdin, handle.process.stdout, handle.process.stderr)
    )


async def _spawn(
    tmp_path: Path, writes: list[tuple[int, bytes]], *, block_on_stdin: bool = False
) -> tuple[PopenAsyncHandle, str]:
    source = "import os\n" + "".join(f"os.write({fd}, {data!r})\n" for fd, data in writes)
    if block_on_stdin:
        source += "os.read(0, 1)\n"
    script = tmp_path / "emit.py"
    adapter = get_default_adapter()
    adapter.write_text(str(script), source, encoding="utf-8")
    adapter.write_text(str(tmp_path / "tmp/marker.txt"), "private fixture\n", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "KERNELONE_HOME": str(tmp_path / "kernel-home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "KERNELONE_STATE_TO_RAMDISK": "0",
        "KERNELONE_RUNTIME_ROOT": "",
        "KERNELONE_RUNTIME_CACHE_ROOT": "",
    }
    handle = await SubprocessPopenRunner().spawn(
        [sys.executable, "-I", "-S", "-B", str(script)], cwd=tmp_path, env=env, timeout=5
    )
    return handle, _birth(handle.pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [False, True])
async def test_concrete_pumps_keep_tail_after_a_blank_line_and_other_stream_eof(blank: bool) -> None:
    text = "Ran 1 test\n\nOK\n" if blank else "Ran 1 test\nOK\n"
    # No OS process is represented by this fixture; StringIO exercises the
    # concrete production pumps and stream, including their real EOF enqueue.
    proc = SimpleNamespace(pid=0, stdout=io.StringIO(""), stderr=io.StringIO(text))
    handle = PopenAsyncHandle(proc, 5, None, datetime.now(timezone.utc))
    await _pump_before_consumer(handle)
    chunks = [chunk async for chunk in handle.stream()]
    expected = ["Ran 1 test", "", "OK", ""] if blank else ["Ran 1 test", "OK", ""]
    assert [chunk.line for chunk in chunks if chunk.source is ProcessStreamSource.STDERR] == expected
    assert list(handle._stderr_queue.queue) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "writes,want_stdout,want_stderr",
    [
        ([(2, b"Ran 1 test\n\nOK\n")], [], ["Ran 1 test", "", "OK"]),
        ([(1, b"head\n\nstdout-tail\n")], ["head", "", "stdout-tail"], []),
        (
            [(1, b"out-first\n"), (2, b"err-first\n"), (1, b"\n"), (2, b"\n"), (1, b"out-last"), (2, b"err-last")],
            ["out-first", "", "out-last"],
            ["err-first", "", "err-last"],
        ),
        ([(2, b"Ran 1 test\nOK\n")], [], ["Ran 1 test", "OK"]),
    ],
    ids=[
        "stderr-blank-stdout-first-eof",
        "stdout-blank-stderr-first-eof",
        "interleaved-both-blank-final-no-newline",
        "no-blank-control",
    ],
)
async def test_real_popen_keeps_empty_lines_and_final_lines(
    tmp_path: Path, writes: list[tuple[int, bytes]], want_stdout: list[str], want_stderr: list[str]
) -> None:
    handle, token = await _spawn(tmp_path, writes)
    try:
        await _pump_before_consumer(handle)
        chunks = [chunk async for chunk in handle.stream()]
        assert await handle.wait(timeout=5.0) is ProcessStatus.SUCCESS
        result = await handle.result()
        assert result.exit_code == 0 and result.timed_out is False
        assert list(result.stdout_lines) == want_stdout
        assert list(result.stderr_lines) == want_stderr
        # Keep the existing public empty EOF chunk for each channel; interior
        # empty strings are data and must not suppress later captured lines.
        assert [chunk.line for chunk in chunks if chunk.source is ProcessStreamSource.STDOUT] == [*want_stdout, ""]
        assert [chunk.line for chunk in chunks if chunk.source is ProcessStreamSource.STDERR] == [*want_stderr, ""]
        assert handle._stdout_queue.empty() and handle._stderr_queue.empty()
    finally:
        await _release(handle, token)


@pytest.mark.asyncio
async def test_consumer_cancel_releases_only_its_birth_bound_process_and_pumps(tmp_path: Path) -> None:
    handle, token = await _spawn(tmp_path, [(2, b"BEFORE\n\n")], block_on_stdin=True)
    ready = asyncio.Event()
    observed: list[str] = []
    stream = handle.stream()

    async def consume() -> None:
        async for chunk in stream:
            observed.append(chunk.line)
            if chunk.line == "BEFORE":
                ready.set()

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
        assert consumer.cancelled() and "BEFORE" in observed
        assert _birth(handle.pid) == token
        assert await handle.terminate(timeout=1.0) is True
        assert handle.status is ProcessStatus.CANCELLED
        result = await handle.result()
        assert result.exit_code != 0 and result.timed_out is False
    finally:
        if not consumer.done():
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
        await stream.aclose()
        await _release(handle, token)
