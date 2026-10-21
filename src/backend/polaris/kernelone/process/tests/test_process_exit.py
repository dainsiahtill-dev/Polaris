"""PID exit proof reaps only exact owned children and never sends a signal."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone import process as public_process
from polaris.kernelone.process import process_tree


def _wait(pid: int, deadline: float) -> bool:
    return public_process.wait_for_process_exit(pid, deadline=deadline)


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="requires POSIX non-reaping child observation")
def test_pid_exit_wait_reaps_actual_exited_child() -> None:
    child = subprocess.Popen(
        ["rtk", "proxy", sys.executable, "-c", "raise SystemExit(7)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert _wait(child.pid, time.monotonic() + 0.5) is True
        with pytest.raises(ProcessLookupError):
            os.kill(child.pid, 0)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        child.wait(timeout=3)


def test_pid_exit_wait_leaves_actual_live_child_running(tmp_path: Path) -> None:
    release = tmp_path / "keep-running"
    release.write_text("alive", encoding="utf-8")
    child = subprocess.Popen(
        [
            "rtk",
            "proxy",
            sys.executable,
            "-c",
            "import sys,time; from pathlib import Path; "
            "flag=Path(sys.argv[1]); "
            "exec('while flag.exists(): time.sleep(0.01)')",
            str(release),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        assert _wait(child.pid, time.monotonic() + 0.03) is False
        os.kill(child.pid, 0)
        assert child.returncode is None
    finally:
        release.unlink(missing_ok=True)
        child.wait(timeout=3)


def test_nonchild_wait_error_alone_does_not_prove_exit(monkeypatch: Any) -> None:
    def nonchild(_pid: int, _options: int) -> tuple[int, int]:
        raise ChildProcessError("not caller's child")

    probes: list[tuple[int, int]] = []
    monkeypatch.setattr(process_tree.os, "waitpid", nonchild)
    monkeypatch.setattr(process_tree.os, "kill", lambda pid, sig: probes.append((pid, sig)))
    assert _wait(998811, time.monotonic()) is False
    assert probes == [(998811, 0)]


def test_nonchild_requires_kernel_confirmed_pid_absence(monkeypatch: Any) -> None:
    def nonchild(_pid: int, _options: int) -> tuple[int, int]:
        raise ChildProcessError("not caller's child")

    def absent(_pid: int, sig: int) -> None:
        assert sig == 0
        raise ProcessLookupError("PID absent")

    monkeypatch.setattr(process_tree.os, "waitpid", nonchild)
    monkeypatch.setattr(process_tree.os, "kill", absent)
    assert _wait(998811, time.monotonic()) is True


@pytest.mark.parametrize("wait_error", [False, True])
def test_permission_unknown_never_proves_pid_exit(monkeypatch: Any, wait_error: bool) -> None:
    def unknown_wait(_pid: int, _options: int) -> tuple[int, int]:
        if wait_error:
            raise PermissionError("cannot inspect child")
        raise ChildProcessError("not caller's child")

    def unknown_probe(_pid: int, sig: int) -> None:
        assert sig == 0
        raise PermissionError("cannot inspect PID")

    monkeypatch.setattr(process_tree.os, "waitpid", unknown_wait)
    monkeypatch.setattr(process_tree.os, "kill", unknown_probe)
    assert _wait(998811, time.monotonic()) is False


def test_exit_wait_uses_one_existing_deadline_without_signals(monkeypatch: Any) -> None:
    clock = [10.0]
    waits: list[float] = []
    probes: list[tuple[int, int]] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(process_tree.os, "waitpid", lambda pid, _options: (0, 0))
    monkeypatch.setattr(process_tree.os, "kill", lambda pid, sig: probes.append((pid, sig)))
    monkeypatch.setattr(process_tree.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(process_tree.time, "sleep", sleep)
    assert _wait(998811, 10.15) is False
    assert sum(waits) == pytest.approx(0.15)
    assert clock[0] == pytest.approx(10.15)
    assert probes and all(pid == 998811 and sig == 0 for pid, sig in probes)


@pytest.mark.parametrize("pid", [0, -1])
def test_exit_wait_rejects_process_group_or_any_child_pid(monkeypatch: Any, pid: int) -> None:
    def forbidden_wait(_pid: int, _options: int) -> tuple[int, int]:
        raise AssertionError("invalid PID must not select other children")

    monkeypatch.setattr(process_tree.os, "waitpid", forbidden_wait)
    with pytest.raises(ValueError, match="positive"):
        _wait(pid, time.monotonic())


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="requires POSIX non-reaping child observation")
def test_exit_wait_never_reaps_a_different_exited_child(tmp_path: Path) -> None:
    release = tmp_path / "keep-target-running"
    release.write_text("alive", encoding="utf-8")
    target = subprocess.Popen(
        [
            "rtk",
            "proxy",
            sys.executable,
            "-c",
            "import sys,time; from pathlib import Path; "
            "flag=Path(sys.argv[1]); exec('while flag.exists(): time.sleep(0.01)')",
            str(release),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    other = subprocess.Popen(
        ["rtk", "proxy", sys.executable, "-c", "raise SystemExit(9)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        os.waitid(os.P_PID, other.pid, os.WEXITED | os.WNOWAIT)
        assert _wait(target.pid, time.monotonic() + 0.03) is False
        reaped, _status = os.waitpid(other.pid, os.WNOHANG)
        assert reaped == other.pid
        os.kill(target.pid, 0)
    finally:
        release.unlink(missing_ok=True)
        target.wait(timeout=3)
        other.wait(timeout=3)


@pytest.mark.parametrize("deadline", [float("inf"), float("nan")])
def test_exit_wait_rejects_nonfinite_deadline_before_process_observation(monkeypatch: Any, deadline: float) -> None:
    def forbidden_wait(_pid: int, _options: int) -> tuple[int, int]:
        raise AssertionError("invalid deadline must not start an unbounded process wait")

    monkeypatch.setattr(process_tree.os, "waitpid", forbidden_wait)
    with pytest.raises(ValueError, match="finite"):
        _wait(998811, deadline)
