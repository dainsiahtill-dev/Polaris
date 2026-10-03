"""Real-process regression tests for whole-tree timeout containment."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.kernelone.process import process_tree as owner, run_process_tree_safe


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
        except OSError:
            return False
        return len(fields) > 2 and fields[2] != "Z"
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_timeout_terminates_descendant_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_process_tree_safe(
            [sys.executable, str(parent), str(pid_file)],
            cwd=tmp_path,
            timeout=0.4,
        )

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2.0
    while _pid_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _pid_is_running(child_pid) is False


_DELAYED_CHILD = """import pathlib, signal, sys, time
if sys.argv[4] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[2]).with_name('child-ready').write_text('ready', encoding='utf-8')
arm = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 5
while not arm.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
time.sleep(float(sys.argv[3]))
pathlib.Path(sys.argv[2]).write_text('late-write', encoding='utf-8')
"""
_TREE_PARENT = """import json, os, pathlib, signal, subprocess, sys
if sys.argv[6] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3], '1', sys.argv[6]],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path(sys.argv[4]).write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}), encoding='utf-8')
if sys.argv[5] != 'exit':
    child.wait()
"""


async def _wait_fixture_ready(path: Path) -> dict[str, int]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            await asyncio.sleep(0.01)
    raise AssertionError("owned fixture did not start")


def _new_control() -> Any:
    control_type = getattr(owner, "ProcessTreeRunControl", None)
    return control_type() if control_type is not None else None


def _run_with_control(command: list[str], root: Path, control: Any, timeout: float = 3) -> Any:
    # Before the new owner API exists, exercise the real legacy path so RED is
    # the delayed physical write, not an import or unsupported-keyword error.
    kwargs = {"cancel_control": control} if control is not None else {}
    return run_process_tree_safe(command, cwd=root, timeout=timeout, **kwargs)


def _cleanup_fixture_group(parent: int, child: int) -> None:
    if _pid_is_running(parent) or _pid_is_running(child):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(parent, signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned process-group regression")
@pytest.mark.parametrize("leader_exits", [False, True])
@pytest.mark.parametrize("ignore_term", [False, True])
@pytest.mark.asyncio
async def test_threaded_cancel_drains_real_grandchild_even_after_leader_exit_and_closefd(
    tmp_path: Path, leader_exits: bool, ignore_term: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, arm, ready = tmp_path / "late.txt", tmp_path / "arm", tmp_path / "ready.json"
    command = [
        sys.executable,
        "-c",
        _TREE_PARENT,
        _DELAYED_CHILD,
        str(arm),
        str(marker),
        str(ready),
        "exit" if leader_exits else "wait",
        "ignore" if ignore_term else "normal",
    ]
    control = _new_control()
    physical = asyncio.create_task(asyncio.to_thread(_run_with_control, command, tmp_path, control))
    identities = await _wait_fixture_ready(ready)
    deadline = time.monotonic() + 3
    while not (tmp_path / "child-ready").is_file() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert (tmp_path / "child-ready").is_file()
    real_killpg = os.killpg
    signals: list[int] = []

    def record_signal(group_id: int, sig: int) -> None:
        signals.append(sig)
        real_killpg(group_id, sig)

    monkeypatch.setattr(owner.os, "killpg", record_signal)
    try:
        if leader_exits:
            deadline = time.monotonic() + 1
            while _pid_is_running(identities["parent"]) and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
        if control is not None:
            control.cancel()
            control.cancel()  # Repeated requests must remain idempotent.
        arm.write_text("go", encoding="utf-8")
        failure: Exception | None = None
        try:
            await asyncio.wait_for(asyncio.shield(physical), 4)
        except RuntimeError as exc:
            failure = exc
        await asyncio.sleep(1.1)

        assert marker.exists() is False, "cancelled owner left a physical grandchild writer alive"
        assert _pid_is_running(identities["parent"]) is False
        assert _pid_is_running(identities["child"]) is False
        assert type(failure).__name__ == "ProcessTreeCancelledError"
        if ignore_term:
            assert signal.SIGKILL in signals
    finally:
        _cleanup_fixture_group(identities["parent"], identities["child"])


def test_cancel_before_spawn_never_launches_the_writer(tmp_path: Path) -> None:
    marker = tmp_path / "spawned.txt"
    control = _new_control()
    if control is not None:
        control.cancel()
    failure: Exception | None = None
    try:
        _run_with_control(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('spawned',encoding='utf-8')",
                str(marker),
            ],
            tmp_path,
            control,
        )
    except RuntimeError as exc:
        failure = exc
    assert marker.exists() is False
    assert type(failure).__name__ == "ProcessTreeCancelledError"


@pytest.mark.skipif(os.name != "posix", reason="cancellation control is not certified on this platform")
def test_control_is_single_use_and_cannot_accept_caller_pid_or_drained_fact(tmp_path: Path) -> None:
    control = _new_control()
    assert control is not None, "spawn-owned cancellation control is missing"
    with pytest.raises(TypeError):
        type(control)(pid=os.getpid(), drained=True)
    with pytest.raises(AttributeError):
        control.drained = True
    with pytest.raises(AttributeError):
        control.pid = os.getpid()
    first = _run_with_control([sys.executable, "-c", "print('中文')"], tmp_path, control)
    assert first.returncode == 0
    assert first.stdout.strip() == "中文"
    with pytest.raises(RuntimeError, match="already used"):
        _run_with_control([sys.executable, "-c", "print('must not spawn')"], tmp_path, control)


def test_ordinary_completion_and_nonzero_exit_keep_existing_contract(tmp_path: Path) -> None:
    result = run_process_tree_safe(
        [sys.executable, "-c", "print('中文'); raise SystemExit(7)"], cwd=tmp_path, timeout=2
    )
    assert result.returncode == 7
    assert result.stdout.strip() == "中文"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.asyncio
async def test_fake_signal_ack_does_not_prove_physical_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker, arm, ready = tmp_path / "late.txt", tmp_path / "arm", tmp_path / "ready.json"
    command = [sys.executable, "-c", _TREE_PARENT, _DELAYED_CHILD, str(arm), str(marker), str(ready), "wait", "normal"]
    control = _new_control()
    assert control is not None
    physical = asyncio.create_task(asyncio.to_thread(_run_with_control, command, tmp_path, control))
    identities = await _wait_fixture_ready(ready)
    try:
        monkeypatch.setattr(owner, "_signal_owned_group", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(owner, "_PROCESS_TREE_DRAIN_TIMEOUT_SECONDS", 0.15)
        monkeypatch.setattr(owner, "_PROCESS_TREE_STOP_GRACE_SECONDS", 0.05)
        control.cancel()
        with pytest.raises(RuntimeError, match="process_tree_drain_deadline_expired"):
            await asyncio.wait_for(asyncio.shield(physical), 2)
        assert _pid_is_running(identities["child"]) is True
        assert marker.exists() is False
    finally:
        _cleanup_fixture_group(identities["parent"], identities["child"])


def test_unknown_platform_control_fails_closed_before_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control = _new_control()
    assert control is not None
    marker = tmp_path / "unexpected.txt"
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="uncertified"))
    with pytest.raises(RuntimeError, match="process_tree_control_unsupported_platform"):
        _run_with_control(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('bad',encoding='utf-8')",
                str(marker),
            ],
            tmp_path,
            control,
        )
    assert marker.exists() is False


def test_forged_caller_control_cannot_supply_drain_authority(tmp_path: Path) -> None:
    marker = tmp_path / "unexpected.txt"
    with pytest.raises(TypeError, match="cancel_control must be ProcessTreeRunControl"):
        _run_with_control(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('bad',encoding='utf-8')",
                str(marker),
            ],
            tmp_path,
            SimpleNamespace(pid=os.getpid(), drained=True),
        )
    assert marker.exists() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX thread/process cancellation regression")
@pytest.mark.asyncio
async def test_async_caller_cancel_waits_for_actual_thread_owner_after_repeated_requests(tmp_path: Path) -> None:
    marker, arm, ready = tmp_path / "late.txt", tmp_path / "arm", tmp_path / "ready.json"
    command = [sys.executable, "-c", _TREE_PARENT, _DELAYED_CHILD, str(arm), str(marker), str(ready), "wait", "ignore"]
    control = _new_control()
    assert control is not None
    physical = asyncio.create_task(asyncio.to_thread(_run_with_control, command, tmp_path, control))

    async def caller() -> None:
        try:
            await asyncio.shield(physical)
        except asyncio.CancelledError:
            control.cancel()
            arm.write_text("go", encoding="utf-8")
            while True:
                try:
                    await asyncio.shield(physical)
                    break
                except asyncio.CancelledError:
                    control.cancel()
                except RuntimeError as exc:
                    assert type(exc).__name__ == "ProcessTreeCancelledError"
                    break
            raise

    logical = asyncio.create_task(caller())
    identities = await _wait_fixture_ready(ready)
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / "child-ready").is_file() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert (tmp_path / "child-ready").is_file()
        logical.cancel()
        await asyncio.sleep(0.05)
        logical.cancel()
        with pytest.raises(asyncio.CancelledError):
            await logical
        assert physical.done()
        assert _pid_is_running(identities["parent"]) is False
        assert _pid_is_running(identities["child"]) is False
        await asyncio.sleep(1.1)
        assert not marker.exists()
    finally:
        _cleanup_fixture_group(identities["parent"], identities["child"])
