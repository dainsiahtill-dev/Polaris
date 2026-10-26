"""Created process groups and sole-reader streams must converge together."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import signal
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process import ProcessTreeDrainError, async_contracts as process_owner
from polaris.kernelone.process.async_contracts import (
    PopenAsyncHandle,
    ProcessStatus,
    SubprocessPopenRunner,
)
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import (
    _birth,
    _release,
    _spawn,
)


async def _script(tmp_path: Path, text: str, extra: tuple[str, ...] = ()):
    fs = get_default_adapter()
    path = tmp_path / "owned_group.py"
    fs.write_text(str(path), text, encoding="utf-8")
    fs.write_text(str(tmp_path / "tmp/marker"), "private fixture\n", encoding="utf-8")
    home = str(tmp_path / "kernel-home")
    argv = [sys.executable, "-I", "-S", "-B", str(path), *extra]
    handle = await SubprocessPopenRunner().spawn(
        argv,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "KERNELONE_HOME": home,
            "TMPDIR": str(tmp_path / "tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        timeout=5,
    )
    return handle, _birth(handle.pid), argv, home


async def _full(handle: PopenAsyncHandle) -> None:
    deadline = time.monotonic() + 2.0
    while max(handle._stdout_queue.qsize(), handle._stderr_queue.qsize()) < 500:
        if time.monotonic() >= deadline:
            raise AssertionError("controlled active stream did not reach its unchanged500 bound")
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_official_producer_isolates_the_created_group(tmp_path: Path) -> None:
    child = await _spawn(tmp_path)
    try:
        assert os.getpgid(child[0].pid) == os.getsid(child[0].pid) == child[0].pid
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_legacy_unbound_handle_refuses_cleanup_without_group_signal(
    tmp_path: Path,
) -> None:
    child = await _spawn(tmp_path)
    legacy = PopenAsyncHandle(child[0].process, 5, None, datetime.now(timezone.utc))
    try:
        with pytest.raises(ProcessTreeDrainError, match=r"group.*bound"):
            await legacy.kill()
        assert child[0].process.poll() is None
    finally:
        await _release(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("holds_pipe", [False, True])
async def test_parent_exit_does_not_finish_its_owned_group(tmp_path: Path, holds_pipe: bool) -> None:
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    fs = get_default_adapter()
    grand = tmp_path / "grand.py"
    fs.write_text(str(grand), "import signal\nsignal.pause()\n", encoding="utf-8")
    text = (
        'import json, os, subprocess, sys\nchild=subprocess.Popen([sys.executable,"-I","-S","-B",sys.argv[1]],stdin=subprocess.DEVNULL'
        + ("" if holds_pipe else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL")
        + ')\nos.write(2,(json.dumps({"pid":child.pid})+"\\n").encode("utf-8"))\nos.read(0,1)\nos._exit(0)\n'
    )
    child = await _script(tmp_path, text, (str(grand),))
    handle, _token, _argv, home = child
    grand_pid = None
    grand_birth = None
    try:
        line = await asyncio.wait_for(asyncio.to_thread(handle.process.stderr.readline), 5.0)
        grand_pid = json.loads(line)["pid"]
        grand_birth = _birth(grand_pid)
        handle.process.stdin.write("x")
        handle.process.stdin.flush()
        assert await handle.wait(timeout=0.05) is ProcessStatus.TIMED_OUT
        assert await handle.is_alive() is True
        await handle.kill()
        assert await handle.is_alive() is False
        assert (await handle.result()).timed_out is True
    finally:
        if grand_pid is not None and grand_birth is not None:
            try:
                actual = [v.decode("utf-8") for v in fs.read_bytes(f"/proc/{grand_pid}/cmdline").split(b"\0") if v]
                if actual:  # A zombie is already dead, but still needs this subreaper's waitpid.
                    assert _birth(grand_pid) == grand_birth
                    assert actual == [sys.executable, "-I", "-S", "-B", str(grand)]
                    assert ("KERNELONE_HOME=" + home).encode("utf-8") in fs.read_bytes(
                        f"/proc/{grand_pid}/environ"
                    ).split(b"\0")
                    os.kill(grand_pid, signal.SIGKILL)
                waited_pid, _wait_status = await asyncio.to_thread(os.waitpid, grand_pid, 0)
                assert waited_pid == grand_pid
                reap = "waitpid"
            except (FileNotFoundError, ProcessLookupError, ChildProcessError) as error:
                reap = type(error).__name__
            try:
                remaining_birth = _birth(grand_pid)
            except (FileNotFoundError, ProcessLookupError):
                remaining_birth = None
            assert remaining_birth != grand_birth, "owned descendant remains unreaped"
            fs.write_text(
                str(tmp_path / "owned-grandchild-cleanup.json"),
                json.dumps(
                    {
                        "pid": grand_pid,
                        "start_token": grand_birth,
                        "argv": [sys.executable, "-I", "-S", "-B", str(grand)],
                        "private_home": home,
                        "reap": reap,
                        "remaining_birth": remaining_birth,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        await _release(child)


_FLOW = 'import os\nfor n in range(601):\n    os.write(1,("out-%04d\\n"%n).encode("utf-8"))\n    os.write(2,("err-%04d\\n"%n).encode("utf-8"))\n'


@pytest.mark.asyncio
async def test_full_bounded_stream_delivers_all601_lines_on_both_sources(
    tmp_path: Path,
) -> None:
    child = await _script(tmp_path, _FLOW)
    handle = child[0]
    stream = handle.stream()
    chunks = []
    try:
        chunks.append(await stream.__anext__())
        await _full(handle)

        async def consume() -> None:
            async for chunk in stream:
                chunks.append(chunk)

        await asyncio.wait_for(consume(), 2.0)
        assert await handle.wait(timeout=2.0) is ProcessStatus.SUCCESS
        expected_out = [f"out-{n:04d}" for n in range(601)]
        expected_err = [f"err-{n:04d}" for n in range(601)]
        assert [c.line for c in chunks if c.source.value == "stdout" and c.line] == expected_out
        assert [c.line for c in chunks if c.source.value == "stderr" and c.line] == expected_err
        result = await handle.result()
        assert list(result.stdout_lines) == expected_out and list(result.stderr_lines) == expected_err
        assert max(handle._stdout_queue.maxsize, handle._stderr_queue.maxsize) == 500
    finally:
        await stream.aclose()
        await _release(child)


@pytest.mark.asyncio
async def test_cancelled_full_subscription_resumes_only_undelivered_output(
    tmp_path: Path,
) -> None:
    child = await _script(tmp_path, _FLOW)
    handle = child[0]
    stream = handle.stream()
    first = await stream.__anext__()
    try:
        await _full(handle)
        await stream.aclose()
        assert await handle.wait(timeout=2.0) is ProcessStatus.SUCCESS

        async def resume():
            return [c async for c in handle.stream()]

        remaining = await asyncio.wait_for(resume(), 2.0)
        all_chunks = [first, *remaining]
        assert [c.line for c in all_chunks if c.source.value == "stdout" and c.line] == [
            f"out-{n:04d}" for n in range(601)
        ]
        assert [c.line for c in all_chunks if c.source.value == "stderr" and c.line] == [
            f"err-{n:04d}" for n in range(601)
        ]
        assert [c async for c in handle.stream()] == []
    finally:
        await stream.aclose()
        await _release(child)


@pytest.mark.asyncio
async def test_concurrent_subscription_is_rejected_instead_of_splitting_output(
    tmp_path: Path,
) -> None:
    child = await _script(tmp_path, _FLOW)
    first, second = child[0].stream(), child[0].stream()
    try:
        await first.__anext__()
        with pytest.raises(RuntimeError, match="consumer"):
            await second.__anext__()
    finally:
        await first.aclose()
        await second.aclose()
        await _release(child)


@pytest.mark.asyncio
async def test_wait_without_subscriber_retains_output_for_one_late_consumer(
    tmp_path: Path,
) -> None:
    child = await _script(tmp_path, _FLOW)
    handle = child[0]
    try:
        assert await handle.wait(timeout=2.0) is ProcessStatus.SUCCESS
        chunks = [c async for c in handle.stream()]
        assert [c.line for c in chunks if c.source.value == "stdout" and c.line] == [f"out-{n:04d}" for n in range(601)]
        assert [c.line for c in chunks if c.source.value == "stderr" and c.line] == [f"err-{n:04d}" for n in range(601)]
        assert [c async for c in handle.stream()] == []
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_reader_failure_is_not_natural_eof_or_confirmed_drain(
    tmp_path: Path,
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    original = handle.process.stdout

    class FailedReader:
        def __iter__(self) -> Iterator[str]:
            raise OSError("controlled reader failure")
            yield "unreachable"

        def close(self):
            original.close()

        @property
        def closed(self):
            return original.closed

    handle.process.stdout = FailedReader()
    try:
        with pytest.raises(ProcessTreeDrainError, match="reader_failed"):
            _ = [c async for c in handle.stream()]
        assert handle._stdout_eof.is_set() is False
        with pytest.raises(ProcessTreeDrainError, match="reader_failed"):
            await handle.kill()
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_unavailable_group_proof_refuses_before_any_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def unsupported():
        raise ProcessTreeDrainError("group binding unsupported")

    def forbidden(*args, **kwargs):
        seen.append(True)
        raise AssertionError("unproven platform reached spawn")

    monkeypatch.setattr(process_owner, "_require_group_binding_support", unsupported)
    monkeypatch.setattr(process_owner.subprocess, "Popen", forbidden)
    with pytest.raises(ProcessTreeDrainError, match="unsupported"):
        await SubprocessPopenRunner().spawn(["does-not-run"], timeout=5)
    assert seen == []
