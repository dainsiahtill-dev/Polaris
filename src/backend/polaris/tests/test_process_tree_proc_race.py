"""Native departed-procfs races with narrow ESRCH read-boundary injection."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.process import process_tree as owner

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux procfs race")

_LEADER = """import pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                         stdin=subprocess.PIPE)
root.joinpath('child.pid').write_text(str(child.pid), encoding='utf-8')
while not root.joinpath('exit-child').exists():
    time.sleep(0.005)
child.communicate(timeout=2)
root.joinpath('child-reaped').write_text(str(child.returncode), encoding='utf-8')
time.sleep(60)
"""


def _wait_file(path: Path) -> str:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="utf-8")
            if value:
                return value
        except FileNotFoundError:
            pass
        time.sleep(0.005)
    raise AssertionError(f"fixture did not publish {path.name}")


@contextlib.contextmanager
def _native_owned_members(root: Path) -> Iterator[tuple[subprocess.Popen[str], int]]:
    leader = subprocess.Popen(
        [sys.executable, "-c", _LEADER, str(root)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        child = int(_wait_file(root / "child.pid"))
        assert os.getpgid(child) == leader.pid
        yield leader, child
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(leader.pid, signal.SIGKILL)
        leader.communicate(timeout=3)


def _scan_live_member_first(monkeypatch: pytest.MonkeyPatch, pid: int) -> None:
    with os.scandir("/proc") as entries:
        snapshot = sorted(entries, key=lambda entry: entry.name != str(pid))

    @contextlib.contextmanager
    def scan(path: str) -> Iterator[Iterator[os.DirEntry[str]]]:
        assert path == "/proc"
        yield iter(snapshot)

    monkeypatch.setattr(owner.os, "scandir", scan)


@pytest.mark.parametrize("inject_esrch", [False, True], ids=["native-ENOENT", "injected-ESRCH"])
def test_departed_proc_entry_does_not_hide_real_live_owned_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inject_esrch: bool
) -> None:
    # Removing the per-entry departed-process catch must fail this live-member
    # assertion. Returning false for that entry also fails despite real leader.
    with _native_owned_members(tmp_path) as (leader, child):
        real_scandir = os.scandir
        real_read = Path.read_text
        with real_scandir("/proc") as entries:
            snapshot = list(entries)
        vanished_entry = next(entry for entry in snapshot if entry.name == str(child))
        live_entry = next(entry for entry in snapshot if entry.name == str(leader.pid))
        # Native procfs enumeration retained; force race before live member read.
        ordered = [vanished_entry, live_entry] + [
            entry for entry in snapshot if entry.name not in {str(child), str(leader.pid)}
        ]

        @contextlib.contextmanager
        def scan(path: str) -> Iterator[Iterator[os.DirEntry[str]]]:
            assert path == "/proc"
            yield iter(ordered)

        def read(path: Path, *args: Any, **kwargs: Any) -> str:
            if path == Path(f"/proc/{child}/stat"):
                (tmp_path / "exit-child").write_text("exit", encoding="utf-8")
                assert _wait_file(tmp_path / "child-reaped") == "0"
                # The child really exited/reaped. Native read now gives ENOENT.
                with pytest.raises(FileNotFoundError):
                    real_read(path, encoding="utf-8")
                assert leader.poll() is None
                if inject_esrch:
                    raise ProcessLookupError(errno.ESRCH, "departed procfs entry", str(path))
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(owner.os, "scandir", scan)
        monkeypatch.setattr(Path, "read_text", read)
        assert owner._owned_group_running(leader.pid) is True
        assert leader.poll() is None
        assert os.getpgid(leader.pid) == leader.pid


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        (PermissionError(errno.EACCES, "denied"), "PermissionError"),
        (OSError(errno.EIO, "I/O error"), "OSError"),
    ],
)
def test_live_proc_read_errors_remain_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError, diagnostic: str
) -> None:
    with _native_owned_members(tmp_path) as (leader, _child):
        _scan_live_member_first(monkeypatch, leader.pid)
        real_read = Path.read_text

        def read(path: Path, *args: Any, **kwargs: Any) -> str:
            if path == Path(f"/proc/{leader.pid}/stat"):
                raise failure
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
        with pytest.raises(owner.ProcessTreeDrainError, match=f"process_tree_group_probe_failed:{diagnostic}"):
            owner._owned_group_running(leader.pid)
        assert leader.poll() is None


@pytest.mark.parametrize("invalid_stat", ["malformed", "1 (fixture) S", "1 (fixture) S 1 bad-group"])
def test_malformed_live_proc_stat_remains_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_stat: str
) -> None:
    with _native_owned_members(tmp_path) as (leader, _child):
        _scan_live_member_first(monkeypatch, leader.pid)
        real_read = Path.read_text

        def read(path: Path, *args: Any, **kwargs: Any) -> str:
            if path == Path(f"/proc/{leader.pid}/stat"):
                return invalid_stat
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
        with pytest.raises(owner.ProcessTreeDrainError, match=r"process_tree_group_probe_(failed|invalid)"):
            owner._owned_group_running(leader.pid)
        assert leader.poll() is None


def test_scandir_esrch_is_not_misclassified_as_departed_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def scan(_path: str) -> Any:
        raise ProcessLookupError(errno.ESRCH, "scan unavailable")

    monkeypatch.setattr(owner.os, "scandir", scan)
    with pytest.raises(owner.ProcessTreeDrainError, match="process_tree_group_probe_failed:ProcessLookupError"):
        owner._owned_group_running(os.getpid())


def test_native_fast_exit_retains_pipe_and_exit_evidence(tmp_path: Path) -> None:
    for exit_code in (0, 7, 0, 7, 0, 7, 0, 7):
        completed = owner.run_process_tree_safe(
            [
                sys.executable,
                "-c",
                f"import sys; print('管道完成'); print('stderr', file=sys.stderr); sys.exit({exit_code})",
            ],
            cwd=tmp_path,
            timeout=3,
        )
        assert completed.returncode == exit_code
        assert completed.stdout == "管道完成\n"
        assert completed.stderr == "stderr\n"


_IGNORE_TERM_CHILD = """import pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = pathlib.Path(sys.argv[1])
root.joinpath('child-ready').write_text('ready', encoding='utf-8')
while not root.joinpath('arm-write').exists():
    time.sleep(0.005)
time.sleep(1.0)
root.joinpath('late-write').write_text('unexpected', encoding='utf-8')
"""
_IGNORE_TERM_LEADER = """import os, pathlib, signal, subprocess, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = pathlib.Path(sys.argv[1])
child = subprocess.Popen([sys.executable, '-c', sys.argv[2], str(root)])
root.joinpath('child.pid').write_text(str(child.pid), encoding='utf-8')
root.joinpath('owner.pid').write_text(str(os.getpid()), encoding='utf-8')
child.wait()
"""


def _proc_is_live(pid: int) -> bool:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return fields[0] not in {"Z", "X", "x"}


@contextlib.contextmanager
def _term_ignoring_owned_members(
    root: Path, leader_script: str = _IGNORE_TERM_LEADER
) -> Iterator[tuple[subprocess.Popen[str], int]]:
    leader = subprocess.Popen(
        [sys.executable, "-c", leader_script, str(root), _IGNORE_TERM_CHILD],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert int(_wait_file(root / "owner.pid")) == leader.pid
        child = int(_wait_file(root / "child.pid"))
        assert _wait_file(root / "child-ready") == "ready"
        assert os.getpgid(child) == leader.pid
        yield leader, child
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(leader.pid, signal.SIGKILL)
        leader.communicate(timeout=3)


class _RaisingDiagnosticSink(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("diagnostic_sink_failed")


def test_raising_diagnostic_sink_cannot_abort_native_owner_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fallible sink inside drain must fail this actual escalation/reap test.
    signals: list[int] = []
    native_timeouts: list[subprocess.TimeoutExpired] = []
    real_killpg = os.killpg
    real_communicate = subprocess.Popen.communicate
    logger = logging.getLogger(owner.__name__)
    original_level = logger.level
    handler = _RaisingDiagnosticSink()

    def signal_group(group_id: int, sig: int) -> None:
        signals.append(sig)
        real_killpg(group_id, sig)

    def communicate(process: subprocess.Popen[str], *args: Any, **kwargs: Any) -> tuple[str, str]:
        try:
            return real_communicate(process, *args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            native_timeouts.append(exc)
            if len(native_timeouts) == 1:
                assert process.poll() is None
                assert _proc_is_live(child)
                assert signal.SIGTERM in signals
                assert signal.SIGKILL not in signals
            raise

    with _term_ignoring_owned_members(tmp_path) as (leader, child):
        monkeypatch.setattr(owner.os, "killpg", signal_group)
        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            (tmp_path / "arm-write").write_text("arm", encoding="utf-8")
            started = time.monotonic()
            assert owner._stop_and_drain_owned_group(leader, leader.pid) == ("", "")
            elapsed = time.monotonic() - started
            assert 0.5 <= elapsed < 2.0
            assert signal.SIGKILL in signals
            assert leader.returncode == -signal.SIGKILL
            assert not Path(f"/proc/{leader.pid}").exists(), "spawn owner did not reap leader"
            assert not _proc_is_live(child)
            time.sleep(1.1)
            assert not (tmp_path / "late-write").exists()
            assert len(native_timeouts) >= 2
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)


def test_native_unconfirmed_drain_retains_actual_last_communicate_timeout_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dropping actual timeout evidence (or granting false cleanup) must fail.
    timeouts: list[subprocess.TimeoutExpired] = []
    real_communicate = subprocess.Popen.communicate

    def communicate(process: subprocess.Popen[str], *args: Any, **kwargs: Any) -> tuple[str, str]:
        try:
            return real_communicate(process, *args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            timeouts.append(exc)
            raise

    with _term_ignoring_owned_members(tmp_path) as (leader, child):
        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        monkeypatch.setattr(owner, "_signal_owned_group", lambda *_args, **_kwargs: None)
        started = time.monotonic()
        with pytest.raises(owner.ProcessTreeDrainError, match="process_tree_drain_deadline_expired") as caught:
            owner._stop_and_drain_owned_group(leader, leader.pid)
        elapsed = time.monotonic() - started
        assert 2.0 <= elapsed < 2.5
        assert len(timeouts) >= 2
        assert caught.value.__cause__ is timeouts[-1]
        assert timeouts[-1].timeout == 0.05
        assert leader.poll() is None
        assert _proc_is_live(child)
        assert (tmp_path / "child-ready").exists()
        assert not (tmp_path / "late-write").exists()


_CLOSE_PIPE_LEADER = """import os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = pathlib.Path(sys.argv[1])
child = subprocess.Popen([sys.executable, '-c', sys.argv[2], str(root)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
root.joinpath('child.pid').write_text(str(child.pid), encoding='utf-8')
root.joinpath('owner.pid').write_text(str(os.getpid()), encoding='utf-8')
while not root.joinpath('release-leader').exists():
    time.sleep(0.005)
"""


def test_completed_native_communicate_clears_stale_pipe_timeout_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A retained timeout after real pipe completion misattributes group failure.
    timeouts: list[subprocess.TimeoutExpired] = []
    real_communicate = subprocess.Popen.communicate

    def communicate(process: subprocess.Popen[str], *args: Any, **kwargs: Any) -> tuple[str, str]:
        try:
            return real_communicate(process, *args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            timeouts.append(exc)
            (tmp_path / "release-leader").write_text("release", encoding="utf-8")
            raise

    with _term_ignoring_owned_members(tmp_path, _CLOSE_PIPE_LEADER) as (leader, child):
        monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
        monkeypatch.setattr(owner, "_signal_owned_group", lambda *_args, **_kwargs: None)
        with pytest.raises(owner.ProcessTreeDrainError, match="process_tree_drain_deadline_expired") as caught:
            owner._stop_and_drain_owned_group(leader, leader.pid)
        assert timeouts, "fixture did not exercise initial native pipe timeout"
        assert caught.value.__cause__ is None
        assert leader.returncode == 0
        assert not Path(f"/proc/{leader.pid}").exists()
        assert _proc_is_live(child), "unknown surviving writer cannot grant cleanup"
