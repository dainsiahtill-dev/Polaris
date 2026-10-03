"""Subprocess process-group isolation, termination and physical pipe drain.

Group membership alone cannot contain descendants that create new sessions.
Whole-tree containment requires an enclosing owner-managed PID namespace or
equivalent platform job boundary. Factory verification uses such a namespace;
these return values must not grant cleanup for arbitrary detached descendants.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ProcessTreeCancelledError(RuntimeError):
    """The spawn owner stopped and drained its cancelled process group.

    This is not proof of containment for descendants escaping the group.
    """


class ProcessTreeDrainError(RuntimeError):
    """Physical tree drain could not be proved; cleanup must remain blocked."""


class ProcessTreeRunControl:
    """Single-use cancellation request, never caller-provided process evidence.

    Only ``cancel`` is public. The runner owns process/group binding and drain;
    cancellation requests cannot supply a PID or mark an invocation drained.
    """

    __slots__ = ("__cancel_requested", "__claim_lock", "__claimed")

    def __init__(self) -> None:
        self.__cancel_requested = threading.Event()
        self.__claim_lock = threading.Lock()
        self.__claimed = False

    def cancel(self) -> None:
        """Irreversibly request cancellation; repeated requests are harmless."""
        self.__cancel_requested.set()

    def _claim(self) -> None:
        with self.__claim_lock:
            if self.__claimed:
                raise RuntimeError("process tree control already used")
            self.__claimed = True

    def _cancelled(self) -> bool:
        return self.__cancel_requested.is_set()

    def _wait(self, seconds: float) -> None:
        self.__cancel_requested.wait(seconds)


_PROCESS_TREE_STOP_GRACE_SECONDS = 0.5
_PROCESS_TREE_DRAIN_TIMEOUT_SECONDS = 2.0
_PROCESS_TREE_WAIT_SLICE_SECONDS = 0.05


def isolated_process_group_kwargs() -> dict[str, Any]:
    """Return ``Popen`` kwargs that isolate descendants into one killable tree."""

    if os.name == "nt":
        return {"creationflags": int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))}
    return {"start_new_session": True}


def signal_process_tree(pid: int, *, force: bool) -> bool:
    """Signal one isolated subprocess tree by its group leader PID."""

    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return completed.returncode == 0
        os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
        return True
    except ProcessLookupError:
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _signal_owned_group(group_id: int, *, force: bool) -> None:
    """Signal the spawn-bound group, even after its leader has been reaped."""
    try:
        os.killpg(group_id, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        # No signal recipient. This is not drain proof; the owner separately
        # observes group absence, leader reap and completed pipe draining.
        return
    except OSError as exc:
        raise ProcessTreeDrainError(f"process_tree_signal_failed:{type(exc).__name__}") from exc


def _owned_group_running(group_id: int) -> bool:
    """Conservatively observe live members; zombies cannot remain writers."""
    if Path("/proc/self/stat").is_file():
        try:
            with os.scandir("/proc") as entries:
                for entry in entries:
                    if not entry.name.isdecimal():
                        continue
                    try:
                        fields = Path(entry.path, "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if len(fields) < 3:
                        raise ProcessTreeDrainError("process_tree_group_probe_invalid")
                    if int(fields[2]) == group_id and fields[0] not in {"Z", "X", "x"}:
                        return True
            return False
        except (OSError, ValueError, IndexError) as exc:
            raise ProcessTreeDrainError(f"process_tree_group_probe_failed:{type(exc).__name__}") from exc
    # On other POSIX hosts, only kernel-confirmed group absence proves drain.
    try:
        os.killpg(group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise ProcessTreeDrainError(f"process_tree_group_probe_failed:{type(exc).__name__}") from exc


def _stop_and_drain_owned_group(
    process: subprocess.Popen[str], group_id: int, *, force: bool = False
) -> tuple[str, str]:
    """Only the spawn owner can prove group death, leader reap and pipe drain."""
    _signal_owned_group(group_id, force=force)
    started = time.monotonic()
    deadline = started + _PROCESS_TREE_DRAIN_TIMEOUT_SECONDS
    force_sent = force
    pipes_drained = False
    last_pipe_timeout: subprocess.TimeoutExpired | None = None
    stdout = stderr = ""
    while True:
        if not pipes_drained:
            try:
                stdout, stderr = process.communicate(timeout=_PROCESS_TREE_WAIT_SLICE_SECONDS)
                pipes_drained = True
                last_pipe_timeout = None
            except subprocess.TimeoutExpired as exc:
                # Preserve actual unconfirmed communicate evidence without
                # fallible diagnostic I/O interrupting physical owner drain.
                last_pipe_timeout = exc
        if pipes_drained and process.poll() is not None and not _owned_group_running(group_id):
            return stdout, stderr
        now = time.monotonic()
        if not force_sent and now - started >= _PROCESS_TREE_STOP_GRACE_SECONDS:
            _signal_owned_group(group_id, force=True)
            force_sent = True
        if now >= deadline:
            raise ProcessTreeDrainError("process_tree_drain_deadline_expired") from last_pipe_timeout
        if pipes_drained:
            time.sleep(min(_PROCESS_TREE_WAIT_SLICE_SECONDS, deadline - now))


def run_process_tree_safe(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float,
    encoding: str = "utf-8",
    errors: str = "replace",
    cancel_control: ProcessTreeRunControl | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run and drain an isolated group; optionally consume one cancellation control.

    Async callers must shield/await the physical worker before disposing its
    workspace. A ``ProcessTreeDrainError`` is not permission to dispose it.
    The caller must independently enforce a namespace/job boundary if detached
    descendants could otherwise retain access to that workspace.
    """

    command = [str(part) for part in args]
    if not command:
        raise ValueError("args must not be empty")
    if float(timeout) <= 0:
        raise ValueError("timeout must be > 0")
    if cancel_control is not None:
        if type(cancel_control) is not ProcessTreeRunControl:
            raise TypeError("cancel_control must be ProcessTreeRunControl")
        cancel_control._claim()
        if cancel_control._cancelled():
            raise ProcessTreeCancelledError("process_tree_cancelled_before_spawn")
        if os.name != "posix":
            raise ProcessTreeDrainError("process_tree_control_unsupported_platform")
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        **isolated_process_group_kwargs(),
    )
    if os.name != "posix":
        # Legacy non-POSIX callers retain their existing timeout contract;
        # the new physical-cancellation control fails before launch above.
        try:
            stdout, stderr = process.communicate(timeout=float(timeout))
        except subprocess.TimeoutExpired as exc:
            signal_process_tree(process.pid, force=False)
            try:
                stdout, stderr = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                signal_process_tree(process.pid, force=True)
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=float(timeout),
                output=stdout or exc.output,
                stderr=stderr or exc.stderr,
            ) from None
        except (KeyboardInterrupt, SystemExit):
            signal_process_tree(process.pid, force=True)
            process.wait()
            raise
        return subprocess.CompletedProcess(command, int(process.returncode or 0), stdout, stderr)

    # start_new_session binds PGID to the spawned PID. Never look the group up
    # through a possibly exited/reused leader when cancellation arrives later.
    group_id = process.pid
    deadline = time.monotonic() + float(timeout)
    stdout = stderr = ""
    pipes_drained = False
    try:
        while True:
            if cancel_control is not None and cancel_control._cancelled():
                _stop_and_drain_owned_group(process, group_id)
                raise ProcessTreeCancelledError("process_tree_cancelled_and_drained")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stdout, stderr = _stop_and_drain_owned_group(process, group_id)
                raise subprocess.TimeoutExpired(command, float(timeout), output=stdout, stderr=stderr)
            if not pipes_drained:
                try:
                    stdout, stderr = process.communicate(timeout=min(remaining, _PROCESS_TREE_WAIT_SLICE_SECONDS))
                    pipes_drained = True
                except subprocess.TimeoutExpired:
                    continue
            if process.poll() is not None and not _owned_group_running(group_id):
                return subprocess.CompletedProcess(command, int(process.returncode or 0), stdout, stderr)
            if cancel_control is None:
                time.sleep(min(remaining, _PROCESS_TREE_WAIT_SLICE_SECONDS))
            else:
                cancel_control._wait(min(remaining, _PROCESS_TREE_WAIT_SLICE_SECONDS))
    except (KeyboardInterrupt, SystemExit):
        _stop_and_drain_owned_group(process, group_id, force=True)
        raise


__all__ = [
    "ProcessTreeCancelledError",
    "ProcessTreeDrainError",
    "ProcessTreeRunControl",
    "isolated_process_group_kwargs",
    "run_process_tree_safe",
    "signal_process_tree",
]
