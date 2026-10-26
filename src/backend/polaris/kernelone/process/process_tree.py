"""Subprocess process-group isolation, termination and physical pipe drain.

Group membership alone cannot contain descendants that create new sessions.
Whole-tree containment requires an enclosing owner-managed PID namespace or
equivalent platform job boundary. Factory verification uses such a namespace;
these return values must not grant cleanup for arbitrary detached descendants.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import subprocess
import threading
import time
from _thread import LockType
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class ProcessTreeCancelledError(RuntimeError):
    """The spawn owner stopped and drained its cancelled process group.

    This is not proof of containment for descendants escaping the group.
    """


class ProcessTreeDrainError(RuntimeError):
    """Physical tree drain could not be proved; cleanup must remain blocked."""

    owned_resource: object | None = None
    _owned_resource_producer: object | None = None
    _owned_resource_identity: object | None = None


_GROUP_IDENTITY_SEAL = object()


@dataclass(frozen=True, slots=True)
class _OwnedProcessGroupIdentity:
    process: subprocess.Popen[str]
    leader_pid: int
    group_id: int
    session_id: int
    leader_start_token: str
    seal: object


def _process_start_token(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, ValueError, IndexError) as exc:
        raise ProcessTreeDrainError("process_group_birth_unavailable") from exc


def _require_group_binding_support() -> None:
    if os.name != "posix" or not Path("/proc/self/stat").is_file():
        raise ProcessTreeDrainError("process_group_binding_unsupported_platform")


def _bind_spawned_process_group(process: subprocess.Popen[str]) -> _OwnedProcessGroupIdentity:
    _require_group_binding_support()
    pid = process.pid
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ProcessTreeDrainError("process_group_identity_invalid")
    try:
        group_id = os.getpgid(pid)
        session_id = os.getsid(pid)
    except OSError as exc:
        raise ProcessTreeDrainError("process_group_identity_unavailable") from exc
    if group_id != pid or session_id != pid:
        raise ProcessTreeDrainError("process_group_not_isolated")
    return _OwnedProcessGroupIdentity(
        process, pid, group_id, session_id, _process_start_token(pid), _GROUP_IDENTITY_SEAL
    )


def _validate_owned_group(process: subprocess.Popen[str], identity: _OwnedProcessGroupIdentity) -> None:
    if (
        type(identity) is not _OwnedProcessGroupIdentity
        or identity.seal is not _GROUP_IDENTITY_SEAL
        or identity.process is not process
        or process.pid != identity.leader_pid
        or identity.group_id != identity.leader_pid
        or identity.session_id != identity.leader_pid
    ):
        raise ProcessTreeDrainError("process_group_not_bound")
    try:
        token = _process_start_token(identity.leader_pid)
    except ProcessTreeDrainError as error:
        if not isinstance(error.__cause__, (FileNotFoundError, ProcessLookupError)):
            raise
        if process.poll() is None:
            raise ProcessTreeDrainError("process_group_leader_identity_unavailable") from error
    else:
        if token != identity.leader_start_token:
            raise ProcessTreeDrainError("process_group_leader_identity_changed")


def _owned_bound_group_running(process: subprocess.Popen[str], identity: _OwnedProcessGroupIdentity) -> bool:
    _validate_owned_group(process, identity)
    return _owned_group_running(identity.group_id)


class ProcessTreeRunControl:
    """Single-use cancellation request, never caller-provided process evidence.

    Callers request cancellation; a physical execution owner claims the control
    once and observes its immutable request state. No API supplies a PID or
    marks an invocation drained.
    """

    __slots__ = ("__cancel_requested", "__claim_lock", "__claimed")

    def __init__(self) -> None:
        self.__cancel_requested = threading.Event()
        self.__claim_lock = threading.Lock()
        self.__claimed = False

    def cancel(self) -> None:
        """Irreversibly request cancellation; repeated requests are harmless."""
        self.__cancel_requested.set()

    def claim_for_execution(self) -> None:
        """Consume this request control once; never claim physical completion."""
        with self.__claim_lock:
            if self.__claimed:
                raise RuntimeError("process tree control already used")
            self.__claimed = True

    @property
    def cancellation_requested(self) -> bool:
        """Read the irreversible stop request, without process evidence."""
        return self.__cancel_requested.is_set()

    def _claim(self) -> None:
        self.claim_for_execution()

    def _cancelled(self) -> bool:
        return self.cancellation_requested

    def _wait(self, seconds: float) -> None:
        self.__cancel_requested.wait(seconds)


_PROCESS_TREE_STOP_GRACE_SECONDS = 0.5
_PROCESS_TREE_DRAIN_TIMEOUT_SECONDS = 2.0
_PROCESS_TREE_WAIT_SLICE_SECONDS = 0.05


def wait_for_process_exit(pid: int, *, deadline: float) -> bool:
    """Confirm one PID is absent, reaping an exited child owned by this caller.

    ``deadline`` is an existing finite ``time.monotonic()`` deadline. This
    function never signals a process or extends that deadline. POSIX ``ECHILD``
    only means the caller cannot reap this PID; it is not proof of exit. A live
    or inaccessible PID remains unconfirmed. This proves leader exit only,
    not descendant drain, port release, or any execution authority.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if not math.isfinite(deadline):
        raise ValueError("deadline must be finite")
    while True:
        if os.name == "posix":
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, InterruptedError) as exc:
                _logger.debug("PID %s wait status unavailable; checking kernel presence: %s", pid, type(exc).__name__)
            except OSError:
                return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))


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


def _signal_owned_group(group_id: int, *, force: bool) -> bool:
    """Signal the spawn-bound group, even after its leader has been reaped."""
    try:
        os.killpg(group_id, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except ProcessLookupError:
        # No signal recipient. This is not drain proof; the owner separately
        # observes group absence, leader reap and completed pipe draining.
        return False
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
    process: subprocess.Popen[str],
    group_id: int,
    *,
    force: bool = False,
    identity: _OwnedProcessGroupIdentity | None = None,
    drain_pipes: Callable[[float], tuple[str, str]] | None = None,
    stop_grace_seconds: float | None = None,
    drain_timeout_seconds: float | None = None,
    on_force: Callable[[], None] | None = None,
    signal_lock: LockType | None = None,
    on_signal_accepted: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """The same spawn owner coordinates signals, leader/group death and pipe EOF.

    Synchronous owners retain communicate. Stream owners supply their sole
    reader's drain observation; communicate must never race those readers.
    """

    def signal_owned(*, force_signal: bool) -> None:
        if identity is not None:
            if group_id != identity.group_id:
                raise ProcessTreeDrainError("process_group_not_bound")
            _validate_owned_group(process, identity)

        def send_and_record() -> None:
            accepted = _signal_owned_group(group_id, force=force_signal)
            if accepted is True:
                if on_signal_accepted is not None:
                    on_signal_accepted()
                if force_signal and on_force is not None:
                    on_force()

        if signal_lock is None:
            send_and_record()
        else:
            # Observers may inspect physical drain while a signal is pending,
            # but logical exit attribution commits after this decision.
            with signal_lock:
                send_and_record()

    signal_owned(force_signal=force)
    started = time.monotonic()
    grace = _PROCESS_TREE_STOP_GRACE_SECONDS if stop_grace_seconds is None else stop_grace_seconds
    timeout = _PROCESS_TREE_DRAIN_TIMEOUT_SECONDS if drain_timeout_seconds is None else drain_timeout_seconds
    deadline = started + timeout
    force_sent = force
    pipes_drained = False
    last_pipe_timeout: subprocess.TimeoutExpired | None = None
    stdout = stderr = ""
    while True:
        if not pipes_drained:
            now = time.monotonic()
            slice_seconds = min(_PROCESS_TREE_WAIT_SLICE_SECONDS, max(0.0, deadline - now))
            if not force_sent:
                slice_seconds = min(slice_seconds, max(0.0, started + grace - now))
            try:
                if drain_pipes is None:
                    stdout, stderr = process.communicate(timeout=slice_seconds)
                else:
                    stdout, stderr = drain_pipes(slice_seconds)
                pipes_drained = True
                last_pipe_timeout = None
            except subprocess.TimeoutExpired as exc:
                last_pipe_timeout = exc
        if identity is not None:
            running = _owned_bound_group_running(process, identity)
        else:
            running = _owned_group_running(group_id)
        if pipes_drained and process.poll() is not None and not running:
            return stdout, stderr
        now = time.monotonic()
        if not force_sent and now - started >= grace:
            signal_owned(force_signal=True)
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
    "wait_for_process_exit",
]
