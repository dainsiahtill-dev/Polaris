"""KernelOne runtime process/flag control utilities.

This module centralizes process termination and runtime control-flag cleanup
used by delivery-layer shutdown/reset flows.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time

from polaris.kernelone.fs.control_flags import (
    clear_director_stop_flag as _clear_director_stop_flag,
    clear_stop_flag as _clear_stop_flag,
    director_stop_flag_path as _director_stop_flag_path,
)

logger = logging.getLogger(__name__)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        logger.debug("pid existence probe failed: pid=%s error=%s", pid, exc)
        return False
    return True


def terminate_pid(pid: int) -> bool:
    """Terminate a process by PID in a cross-engine way."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "taskkill failed for pid=%s returncode=%s stderr=%s",
                    pid,
                    result.returncode,
                    str(result.stderr or "").strip(),
                )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            if not _pid_exists(pid):
                return True
            time.sleep(0.05)
        if _pid_exists(pid):
            try:
                if os.name != "nt":
                    # SIGKILL is only available on Unix-like systems
                    import signal as _signal_module

                    _sigkill = getattr(_signal_module, "SIGKILL", None)
                    if _sigkill is not None:
                        os.kill(pid, _sigkill)
                else:
                    # On Windows, use taskkill with /F for forceful termination
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True,
                        check=False,
                    )
            except (RuntimeError, ValueError) as exc:
                logger.debug("SIGKILL fallback skipped for pid=%s: %s", pid, exc)
        return not _pid_exists(pid)
    except OSError as exc:
        logger.warning("Failed to terminate pid=%s: %s", pid, exc)
        return False


def _normalize_cmdline_for_match(cmdline: str) -> str:
    normalized = str(cmdline or "").strip().lower()
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
    return normalized


def _looks_like_loop_pm_command(cmdline: str) -> bool:
    normalized = _normalize_cmdline_for_match(cmdline)
    return "loop-pm.py" in normalized and "--workspace" in normalized


def _iter_process_commandlines() -> list[tuple[int, str]]:
    if os.name == "nt":
        ps_script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, CommandLine | "
            "ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            raw = str(completed.stdout or "").strip()
            if completed.returncode != 0 or not raw:
                if completed.returncode != 0:
                    logger.debug(
                        "PowerShell process enumeration returned non-zero: returncode=%s stderr=%s",
                        completed.returncode,
                        str(completed.stderr or "").strip(),
                    )
                return []
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                return []
            rows: list[tuple[int, str]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                pid_raw = item.get("ProcessId")
                cmdline = str(item.get("CommandLine") or "")
                try:
                    pid = int(pid_raw) if pid_raw is not None else 0
                except (TypeError, ValueError):
                    continue
                rows.append((pid, cmdline))
            return rows
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            logger.warning("Failed to enumerate process command lines via powershell: %s", exc)
            return []

    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Failed to enumerate process command lines via ps: %s", exc)
        return []
    if completed.returncode != 0:
        logger.debug(
            "ps process enumeration returned non-zero: returncode=%s stderr=%s",
            completed.returncode,
            str(completed.stderr or "").strip(),
        )
        return []
    ps_rows: list[tuple[int, str]] = []
    for raw_line in str(completed.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except (TypeError, ValueError):
            continue
        cmdline = parts[1] if len(parts) > 1 else ""
        ps_rows.append((pid, cmdline))
    return ps_rows


def list_external_loop_pm_pids(workspace: str, exclude_pid: int | None = None) -> list[int]:
    """List stale external loop-pm process IDs for the given workspace."""
    workspace_text = str(workspace or "").strip()
    if not workspace_text:
        return []
    workspace_norm = _normalize_cmdline_for_match(os.path.abspath(workspace_text))
    if not workspace_norm:
        return []

    pids: list[int] = []
    for pid, cmdline in _iter_process_commandlines():
        if pid <= 0:
            continue
        if exclude_pid is not None and pid == int(exclude_pid):
            continue
        if not _looks_like_loop_pm_command(cmdline):
            continue
        normalized_cmd = _normalize_cmdline_for_match(cmdline)
        if workspace_norm not in normalized_cmd:
            continue
        pids.append(pid)
    return sorted(set(pids))


def terminate_external_loop_pm_processes(workspace: str, exclude_pid: int | None = None) -> list[int]:
    """Terminate stale external loop-pm processes for a workspace."""
    terminated: list[int] = []
    for pid in list_external_loop_pm_pids(workspace, exclude_pid=exclude_pid):
        if terminate_pid(pid):
            terminated.append(pid)
    return terminated


def clear_stop_flag(workspace: str, cache_root: str | None = None) -> None:
    """Compatibility wrapper for PM stop-flag cleanup.

    ``cache_root`` is accepted for legacy call-site compatibility.
    """
    del cache_root
    _clear_stop_flag(workspace)


def director_stop_flag_path(workspace: str, cache_root: str | None = None) -> str:
    """Compatibility wrapper for director stop-flag path lookup."""
    del cache_root
    return _director_stop_flag_path(workspace)


def clear_director_stop_flag(workspace: str, cache_root: str | None = None) -> None:
    """Compatibility wrapper for director stop-flag cleanup."""
    del cache_root
    _clear_director_stop_flag(workspace)


__all__ = [
    "clear_director_stop_flag",
    "clear_stop_flag",
    "director_stop_flag_path",
    "list_external_loop_pm_pids",
    "terminate_external_loop_pm_processes",
    "terminate_pid",
]
