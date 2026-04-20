from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Any

from polaris.kernelone.process.command_executor import CommandExecutionService

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_TAIL_LIMIT = 12000


def _normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _resolve_preexec_fn() -> Callable[[], None] | None:
    """Return a safe preexec function for Unix-like systems only."""
    if os.name == "nt":
        return None
    setsid = getattr(os, "setsid", None)
    if callable(setsid):
        return setsid
    return None


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill a process tree with enhanced Windows support and multiple fallback methods."""
    if process.poll() is not None:
        return

    pid = process.pid

    if os.name == "nt":
        # Windows-specific process tree termination with multiple fallbacks
        killed = False

        # Method 1: taskkill (most reliable on Windows)
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
            if result.returncode in {0, 128}:  # 128 means process was already dead
                killed = True
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass

        # Method 2: wmic if taskkill failed
        if not killed:
            try:
                subprocess.run(
                    ["wmic", "process", "where", f"ParentProcessId={pid}", "delete"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    timeout=5,
                )
                killed = True
            except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                pass

        # Method 3: PowerShell if wmic failed
        if not killed:
            try:
                subprocess.run(
                    ["powershell", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    timeout=5,
                )
                killed = True
            except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                pass

        # Method 4: Direct kill as last resort
        if not killed:
            try:
                process.kill()
                killed = True
            except (OSError, PermissionError):
                pass
    else:
        # Unix-like systems: use process group
        try:
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            if callable(killpg) and callable(getpgid):
                pgid = int(getpgid(pid))
                killpg(pgid, signal.SIGTERM)
                time.sleep(0.1)  # Give it a moment to terminate
                if process.poll() is None:
                    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                    killpg(pgid, sigkill)
            else:
                process.terminate()
                time.sleep(0.1)
                if process.poll() is None:
                    process.kill()
        except (OSError, PermissionError):
            # Fallback to direct kill
            with contextlib.suppress(OSError, PermissionError):
                process.kill()


def _read_pipe_stream(
    pipe: Any,
    stream_name: str,
    chunks: list[str],
    output_callback: Callable[[str, str], None] | None,
    activity_ref: list[float] | None = None,
) -> None:
    if pipe is None:
        return
    try:
        while True:
            line = pipe.readline()
            if line == "":
                break
            text = _normalize_output(line)
            if not text:
                continue
            chunks.append(text)
            if activity_ref is not None:
                activity_ref[0] = time.perf_counter()
            if output_callback is not None:
                try:
                    output_callback(stream_name, text)
                except (RuntimeError, ValueError) as e:
                    logger.debug(f"Output callback failed: {e}")
    except (RuntimeError, ValueError):
        logger.warning("Stream read failed for command output callback")
        return
    finally:
        try:
            pipe.close()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Stream read failed: {e}")


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: int,
    output_callback: Callable[[str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    stall_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run a command via CommandExecutionService (KernelOne CommandExecutorPort contract).

    Security invariants:
    - All commands are validated through CommandExecutionService allowlist and
      workspace-boundary enforcement (cwd must be within workspace root).
    - shell=True is permanently rejected.
    - Forbidden shell operators (;, &&, ||, |, `, $(, <, >) are rejected before parsing.
    - Timeout is enforced by CommandExecutionService.

    Note: streaming output_callback is not supported by CommandExecutionService;
    output is captured and returned in full. This is a deliberate security tradeoff
    to route all command execution through the kernelone contract.
    """
    started = time.perf_counter()
    try:
        raw_command = str(command or "").strip()
        if not raw_command:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": "empty command",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "timed_out": False,
                "cancelled": False,
                "stalled": False,
                "cancel_reason": "",
            }

        # Route through CommandExecutionService (KernelOne contract).
        # Shell-operator validation happens inside parse_command().
        cmd_svc = CommandExecutionService(str(cwd))
        req = cmd_svc.parse_command(
            raw_command,
            cwd=str(cwd),
            timeout_seconds=max(1, int(timeout_seconds)),
        )
        result = cmd_svc.run(req)
        elapsed = time.perf_counter() - started

        return {
            "command": raw_command,
            "exit_code": result.get("returncode", -1),
            "duration_seconds": round(elapsed, 3),
            "stdout": result.get("stdout", "")[-OUTPUT_TAIL_LIMIT:],
            "stderr": result.get("stderr", "")[-OUTPUT_TAIL_LIMIT:],
            "timed_out": bool(result.get("timed_out", False)),
            "cancelled": False,
            "stalled": False,
            "cancel_reason": "",
        }

    except ValueError as e:
        # Command rejected by CommandExecutionService validation (e.g., not in allowlist,
        # forbidden shell operator, or workspace boundary violation).
        elapsed = time.perf_counter() - started
        return {
            "command": str(command),
            "exit_code": 1,
            "duration_seconds": round(elapsed, 3),
            "stdout": "",
            "stderr": f"command validation failed: {e}",
            "timed_out": False,
            "cancelled": False,
            "stalled": False,
            "cancel_reason": "",
        }
    except (OSError, RuntimeError, TypeError, subprocess.SubprocessError) as exc:
        elapsed = time.perf_counter() - started
        return {
            "command": str(command),
            "exit_code": 1,
            "duration_seconds": round(elapsed, 3),
            "stdout": "",
            "stderr": f"agent-accel process error: {exc}",
            "timed_out": False,
            "cancelled": False,
            "stalled": False,
            "cancel_reason": "",
        }
