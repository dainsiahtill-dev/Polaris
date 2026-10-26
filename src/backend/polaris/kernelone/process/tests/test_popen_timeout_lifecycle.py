"""Deadline observation is not physical process termination."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process.async_contracts import PopenAsyncHandle, ProcessStatus, SubprocessPopenRunner


def _birth(pid: int) -> str:
    raw = get_default_adapter().read_bytes(f"/proc/{pid}/stat").decode("utf-8")
    return raw[raw.rfind(")") + 2 :].split()[19]


async def _spawn(tmp_path: Path, *, ignore_term: bool = False) -> tuple[PopenAsyncHandle, str, list[str], str]:
    source = (
        "import os, signal\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
        + 'os.write(2, b"READY\\n")\nos.read(0, 1)\n'
    )
    script = tmp_path / "owned_child.py"
    fs = get_default_adapter()
    fs.write_text(str(script), source, encoding="utf-8")
    fs.write_text(str(tmp_path / "tmp/marker"), "private fixture\n", encoding="utf-8")
    home = str(tmp_path / "kernel-home")
    argv = [sys.executable, "-I", "-S", "-B", str(script)]
    handle = await SubprocessPopenRunner().spawn(
        argv,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "KERNELONE_HOME": home,
            "TMPDIR": str(tmp_path / "tmp"),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        timeout=5,
    )
    token = _birth(handle.pid)
    ready = await asyncio.wait_for(asyncio.to_thread(handle.process.stderr.readline), 5.0)
    assert ready == "READY\n"
    return handle, token, argv, home


async def _release(child: tuple[PopenAsyncHandle, str, list[str], str]) -> None:
    handle, token, argv, home = child
    proc = handle.process
    fs = get_default_adapter()
    harness_kill = proc.poll() is None
    if harness_kill:
        assert _birth(handle.pid) == token
        assert [v.decode("utf-8") for v in fs.read_bytes(f"/proc/{handle.pid}/cmdline").split(b"\0") if v] == argv
        assert ("KERNELONE_HOME=" + home).encode("utf-8") in fs.read_bytes(f"/proc/{handle.pid}/environ").split(b"\0")
        proc.kill()  # Test-only fallback after exact owned argv/home/birth checks.
    await asyncio.to_thread(proc.wait, 5.0)
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        if pipe is not None:
            pipe.close()
    assert proc.poll() is not None
    fs.write_text(
        str(Path(argv[-1]).parent / "owned-child-cleanup.json"),
        json.dumps(
            {
                "pid": handle.pid,
                "start_token": token,
                "argv": argv,
                "private_home": home,
                "harness_fallback_kill": harness_kill,
                "returncode": proc.returncode,
                "pipes_closed": all(pipe is None or pipe.closed for pipe in (proc.stdout, proc.stderr, proc.stdin)),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["kill", "terminate"])
async def test_wait_timeout_keeps_physical_liveness_and_allows_cleanup(tmp_path: Path, cleanup: str) -> None:
    child = await _spawn(tmp_path, ignore_term=True)
    handle = child[0]
    try:
        assert await handle.wait(timeout=0.02) is ProcessStatus.TIMED_OUT
        assert handle._proc_alive is True and await handle.is_alive() is True
        if cleanup == "kill":
            await handle.kill()
        else:
            assert await handle.terminate(timeout=0.02) is False
        assert handle.process.poll() is not None and handle._proc_alive is False
        result = await handle.result()
        assert result.status is ProcessStatus.TIMED_OUT
        assert result.timed_out is True and result.exit_code == handle.process.returncode == -9
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_direct_terminate_escalates_after_real_timeout_and_keeps_exit_code(tmp_path: Path) -> None:
    child = await _spawn(tmp_path, ignore_term=True)
    handle = child[0]
    try:
        assert await handle.terminate(timeout=0.02) is False
        assert handle.process.poll() == -9 and handle._proc_alive is False
        result = await handle.result()
        assert result.status is ProcessStatus.CANCELLED and result.exit_code == -9 and result.timed_out is False
    finally:
        await _release(child)


@pytest.mark.asyncio
async def test_context_exit_reclaims_a_logically_timed_out_live_child(tmp_path: Path) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    try:
        assert await handle.wait(timeout=0.02) is ProcessStatus.TIMED_OUT
        await handle.__aexit__(None, None, None)
        assert handle.process.poll() is not None and handle._proc_alive is False
        result = await handle.result()
        assert result.status is ProcessStatus.TIMED_OUT and result.exit_code == handle.process.returncode
    finally:
        await _release(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["terminate", "kill"])
@pytest.mark.parametrize("error", [PermissionError, Exception])
async def test_signal_failure_does_not_fabricate_physical_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, error: type[Exception]
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    try:

        def denied() -> None:
            raise error("controlled signal unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(handle.process, operation, denied)
            with pytest.raises(error, match="controlled signal unavailable"):
                if operation == "terminate":
                    await handle.terminate(timeout=0.02)
                else:
                    await handle.kill()
        assert handle.process.poll() is None and handle._proc_alive is True
        assert await handle.is_alive() is True
    finally:
        await _release(child)
