"""Cross-platform subprocess isolation and whole-tree termination.

Verifier and tool commands may spawn descendants.  A timeout is not contained
unless the whole process tree is terminated; killing only the direct child can
leave compilers, test runners, or recursively spawned verifiers alive.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


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


def run_process_tree_safe(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> subprocess.CompletedProcess[str]:
    """Run a command and terminate its full descendant tree on timeout/cancel."""

    command = [str(part) for part in args]
    if not command:
        raise ValueError("args must not be empty")
    if float(timeout) <= 0:
        raise ValueError("timeout must be > 0")
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


__all__ = ["isolated_process_group_kwargs", "run_process_tree_safe", "signal_process_tree"]
