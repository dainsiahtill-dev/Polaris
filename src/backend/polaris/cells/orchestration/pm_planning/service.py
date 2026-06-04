"""PM (Project Manager) Cell Service.

This service encapsulates business logic for PM operations,
separating it from the delivery layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from polaris.bootstrap.config import Settings, find_workspace_root, get_settings
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessStatusV1,
    GetExecutionProcessStatusQueryV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    get_execution_broker_service,
)
from polaris.domain.exceptions import (
    ProcessAlreadyRunningError,
    ProcessError,
    ServiceUnavailableError,
)
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.process import terminate_pid
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest
from polaris.kernelone.storage import StorageLayout

logger = logging.getLogger(__name__)

_ACTIVE_EXECUTION_STATUSES = {
    ExecutionProcessStatusV1.QUEUED,
    ExecutionProcessStatusV1.RUNNING,
}
_PM_PLANNING_TIMEOUT_ENV = "KERNELONE_PM_PLANNING_TIMEOUT_SECONDS"
_PM_CODEX_MIN_PLANNING_TIMEOUT_ENV = "KERNELONE_PM_CODEX_MIN_TIMEOUT_SECONDS"
_DEFAULT_PM_PLANNING_TIMEOUT_SECONDS = 60
_DEFAULT_CODEX_PM_PLANNING_TIMEOUT_SECONDS = 360
_MIN_PM_PLANNING_TIMEOUT_SECONDS = 5
_MAX_PM_PLANNING_TIMEOUT_SECONDS = 600
_CODEX_PROVIDER_IDS = {"codex", "codex_cli", "codex_sdk"}
_PM_LIFECYCLE_STATUS_FILE = "pm.lifecycle.json"
_CONTRACT_MTIME_SLOP_SECONDS = 2.0
_ACTIVE_ENGINE_PHASES = {
    "planning",
    "dispatching",
    "running",
    "in_progress",
    "analyzing",
    "executing",
    "llm_calling",
    "tool_running",
    "verification",
    "chief_engineer",
    "director",
    "qa",
}


def _parse_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _clamp_pm_planning_timeout(seconds: int) -> int:
    return max(
        _MIN_PM_PLANNING_TIMEOUT_SECONDS,
        min(_MAX_PM_PLANNING_TIMEOUT_SECONDS, int(seconds)),
    )


def _read_contract_terminal_error(contract_path: Path) -> str:
    if not contract_path.is_file():
        return ""
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Failed to read PM contract terminal error from %s: %s", contract_path, exc)
        return ""
    if not isinstance(payload, dict):
        return ""
    code = str(payload.get("terminal_error_code") or "").strip()
    if not code:
        return ""
    detail = str(payload.get("terminal_error") or payload.get("notes") or "").strip()
    return f"{code}: {detail}" if detail else code


def _read_engine_terminal_state(engine_status_path: Path) -> dict[str, Any]:
    if not engine_status_path.is_file():
        return {}
    try:
        payload = json.loads(engine_status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Failed to read PM engine status from %s: %s", engine_status_path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    phase = str(payload.get("phase") or "").strip().lower()
    if phase not in {"completed", "failed", "cancelled", "canceled", "terminated", "timed_out"}:
        return {}
    error = str(payload.get("error") or "").strip()
    updated_at = str(payload.get("updated_at") or "").strip()
    roles = payload.get("roles")
    pm_role = roles.get("PM") if isinstance(roles, dict) else None
    pm_status = str(pm_role.get("status") or "").strip().lower() if isinstance(pm_role, dict) else ""
    pm_completed = pm_status in {"completed", "success", "succeeded"}
    pm_failed = pm_status in {"failed", "blocked"}
    pm_ok = phase == "completed" or (phase == "failed" and pm_completed and not pm_failed)
    return {
        "terminal": True,
        "ok": pm_ok,
        "status": "success" if pm_ok else "failed",
        "exit_code": 0 if pm_ok else 1,
        "error": "" if pm_ok else error,
        "phase": phase,
        "pm_role_status": pm_status,
        "run_id": str(payload.get("run_id") or "").strip(),
        "updated_at": updated_at,
        "updated_at_ts": _parse_timestamp_seconds(updated_at),
    }


def _read_engine_active_state(engine_status_path: Path) -> dict[str, Any]:
    if not engine_status_path.is_file():
        return {}
    try:
        payload = json.loads(engine_status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Failed to read active PM engine status from %s: %s", engine_status_path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    phase = str(payload.get("phase") or "").strip().lower()
    running = bool(payload.get("running"))
    if not (running or phase in _ACTIVE_ENGINE_PHASES):
        return {}
    updated_at = str(payload.get("updated_at") or "").strip()
    return {
        "active": True,
        "running": running,
        "phase": phase,
        "run_id": str(payload.get("run_id") or "").strip(),
        "updated_at": updated_at,
        "updated_at_ts": _parse_timestamp_seconds(updated_at),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _engine_terminal_state_is_current(
    engine_terminal_state: dict[str, Any],
    *,
    started_at: float | None,
) -> bool:
    if not engine_terminal_state:
        return False
    updated_at_ts = _float_or_none(engine_terminal_state.get("updated_at_ts"))
    if started_at is None or updated_at_ts is None:
        return True
    return updated_at_ts + _CONTRACT_MTIME_SLOP_SECONDS >= float(started_at)


@dataclass
class ProcessHandle:
    """Handle for a managed process."""

    process: Any | None = None  # subprocess.Popen — see _spawn_process
    log_handle: object | None = None
    log_path: str | None = None
    started_at: float | None = None
    mode: str = ""
    execution_id: str | None = None

    @property
    def pid(self) -> int | None:
        """Get process ID."""
        if self.process:
            try:
                return self.process.pid
            except (OSError, ValueError, AttributeError) as e:
                logger.debug(f"Failed to get process pid: {e}")
        return None

    @property
    def is_running(self) -> bool:
        """Check if process is running."""
        if self.process is None:
            return False
        try:
            return self.process.poll() is None
        except (OSError, ValueError) as e:
            logger.debug(f"Failed to check process status: {e}")
            return False

    def terminate(self) -> None:
        """Terminate the process."""
        _terminate_process_impl(self)


@dataclass(frozen=True)
class RoleModelSelection:
    """Resolved role model binding used for subprocess launch defaults."""

    provider_id: str = ""
    model: str = ""


def _text_value(value: Any) -> str:
    if isinstance(value, Mock):
        return ""
    return str(value or "").strip()


class PMService:
    """Service for PM (Project Manager) operations.

    Responsibilities:
    - PM process lifecycle management (start, stop, status)
    - PM command building
    - Coordination with Director (via settings)
    """

    def __init__(
        self,
        settings: Settings,
        storage_layout: StorageLayout | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage_layout
        self._handle = ProcessHandle()
        # Serialize lifecycle transitions to prevent concurrent start races.
        self._lifecycle_lock = asyncio.Lock()
        self._drain_task: asyncio.Task[None] | None = None  # async stdout drain task
        self._refresh_storage_layout(force=True)

    @property
    def handle(self) -> ProcessHandle:
        """Get current process handle."""
        return self._handle

    def refresh_storage_layout(self) -> None:
        """Refresh storage binding after workspace/runtime settings updates."""
        self._refresh_storage_layout(force=True)

    def rebind_settings(self, settings: Settings) -> None:
        """Bind this long-lived service to the application settings object."""
        self._settings = settings
        self._refresh_storage_layout(force=True)

    async def run_once(self) -> dict:
        """Run PM once."""
        async with self._lifecycle_lock:
            if self._is_execution_active():
                raise ProcessAlreadyRunningError("pm", pid=self._handle.pid)

            if self._handle.process is not None:
                self._handle.terminate()

            error = await self._check_backend_available()
            if error:
                raise ServiceUnavailableError("backend", message=error)

            await self._clear_stop_flag()

            cmd = self._build_command(loop_mode=False)
            log_path = self._resolve_log_path()

            try:
                handle = await self._spawn_process(cmd, log_path)
                self._handle = handle
                self._handle.mode = "run_once"
                self._write_lifecycle_status(self._handle, mode=self._handle.mode)
                return self._build_start_response(handle, mode=self._handle.mode)
            except (RuntimeError, ValueError) as exc:
                raise ProcessError("Failed to start PM process", process_name="pm", cause=exc) from exc

    async def start_loop(self, resume: bool = False) -> dict:
        """Start PM in loop mode."""
        async with self._lifecycle_lock:
            if self._is_execution_active():
                raise ProcessAlreadyRunningError("pm", pid=self._handle.pid)

            if self._handle.process is not None:
                self._handle.terminate()

            error = await self._check_backend_available()
            if error:
                raise ServiceUnavailableError("backend", message=error)

            await self._clear_stop_flag()

            cmd = self._build_command(loop_mode=True, resume=resume)
            log_path = self._resolve_log_path()

            try:
                handle = await self._spawn_process(cmd, log_path)
                self._handle = handle
                self._handle.mode = "loop_resume" if resume else "loop"
                self._write_lifecycle_status(self._handle, mode=self._handle.mode)
                response = self._build_start_response(handle, mode=self._handle.mode)
                response["resume"] = resume
                return response
            except (RuntimeError, ValueError) as exc:
                raise ProcessError("Failed to start PM loop", process_name="pm", cause=exc) from exc

    async def stop(
        self,
        *,
        graceful: bool = True,
        graceful_timeout: float = 5.0,
        force_timeout: float = 3.0,
    ) -> dict:
        """Stop PM process."""
        async with self._lifecycle_lock:
            # Cancel the async stdout drain task if running.
            if self._drain_task is not None and not self._drain_task.done():
                self._drain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._drain_task
            self._drain_task = None

            if not self._is_execution_active():
                return {"ok": False, "error": "not running"}

            pid = self._handle.pid
            workspace = self._resolve_effective_workspace()

            if pid is None:
                return {"ok": False, "error": "no pid"}

            # pid is guaranteed non-None after check
            pid_int: int = pid

            if graceful:
                try:
                    await self._write_stop_flag(str(workspace))
                    if await self._wait_for_exit(pid_int, timeout=graceful_timeout):
                        self._handle.terminate()
                        return {
                            "ok": True,
                            "method": "graceful",
                            "pid": pid_int,
                            "waited": graceful_timeout,
                        }
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Graceful stop failed; fallback to force termination: %s", exc)

            self._handle.terminate()

            if await self._is_process_alive(pid_int):
                await self._force_kill_tree(pid_int, timeout=force_timeout)

            return {
                "ok": True,
                "method": "force" if not graceful else "graceful_timeout",
                "pid": pid,
            }

    async def _write_stop_flag(self, workspace: str) -> None:
        try:
            from polaris.kernelone.fs.control_flags import stop_flag_path

            flag_path = stop_flag_path(str(workspace))
            write_text_atomic(flag_path, f"stop requested at {time.time()}\n")
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to write PM stop flag for workspace %s: %s", workspace, exc)

    async def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not await self._is_process_alive(pid):
                return True
            await asyncio.sleep(0.1)
        return False

    async def _is_process_alive(self, pid: int) -> bool:
        if os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except (RuntimeError, ValueError):
                logger.debug("Primary process liveness check failed for pid=%s; using tasklist fallback", pid)
                try:
                    cmd_svc = CommandExecutionService(".")
                    request = CommandRequest(
                        executable="tasklist",
                        args=["/FI", f"PID eq {pid}", "/NH"],
                        timeout_seconds=2,
                    )
                    result = cmd_svc.run(request)
                    stdout = result.get("stdout", "") if result.get("ok") else ""
                    return str(pid) in stdout
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Fallback liveness check failed for pid=%s: %s", pid, exc)
                    return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    async def _force_kill_tree(self, pid: int, timeout: float) -> None:
        """Force kill a process tree.

        Uses kernelone's terminate_pid for cross-platform compatibility.
        """
        try:
            # Use kernelone's cross-platform terminate_pid
            terminate_pid(pid)
        except (RuntimeError, ValueError) as exc:
            logger.debug("terminate_pid failed for pid=%s: %s", pid, exc)
            # Fallback to os.kill for Unix
            if os.name != "nt":
                try:
                    os.kill(pid, 9)
                except (RuntimeError, ValueError) as fallback_exc:
                    logger.debug("Fallback os.kill failed for pid=%s: %s", pid, fallback_exc)

    def _resolve_execution_status(self, execution_id: str | None = None) -> ExecutionProcessStatusV1 | None:
        execution_id = execution_id or self._handle.execution_id
        if not execution_id:
            return None
        try:
            broker = get_execution_broker_service()
            return broker.get_process_status(
                GetExecutionProcessStatusQueryV1(execution_id=execution_id),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to resolve PM execution broker status for %s: %s", execution_id, exc)
            return None

    def _resolve_execution_snapshot(self, execution_id: str | None = None) -> Any | None:
        execution_id = execution_id or self._handle.execution_id
        if not execution_id:
            return None
        try:
            broker = get_execution_broker_service()
            return broker.get_process_snapshot(execution_id)
        except (AttributeError, KeyError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to resolve PM execution broker snapshot for %s: %s", execution_id, exc)
            return None

    def _is_execution_active(self) -> bool:
        execution_status = self._resolve_execution_status()
        if execution_status is not None:
            return execution_status in _ACTIVE_EXECUTION_STATUSES
        return self._handle.is_running

    def get_status(self) -> dict:
        lifecycle = self._read_lifecycle_status()
        execution_id = self._handle.execution_id or _text_value(lifecycle.get("execution_id"))
        handle_pid = self._handle.pid
        handle_mode = self._handle.mode
        handle_started_at = self._handle.started_at
        handle_log_path = self._handle.log_path
        execution_status = self._resolve_execution_status(execution_id)
        running = (
            execution_status in _ACTIVE_EXECUTION_STATUSES if execution_status is not None else self._handle.is_running
        )
        lifecycle_pid = _parse_positive_int(lifecycle.get("pid"))
        if not running and not self._handle.process and lifecycle_pid and self._is_pid_alive_sync(lifecycle_pid):
            running = True
        if self._handle.process and not running:
            self._handle.terminate()

        log_path = handle_log_path or _text_value(lifecycle.get("log_path"))
        if not log_path:
            log_path = self._resolve_log_path()

        snapshot = self._resolve_execution_snapshot(execution_id)
        exit_code: int | None = None
        execution_error = ""
        terminal = False
        ok: bool | None = None
        if snapshot is not None:
            result = getattr(snapshot, "result", None)
            result_exit_code = getattr(result, "exit_code", None)
            if isinstance(result, dict):
                result_exit_code = result.get("exit_code")
            if isinstance(result_exit_code, int):
                exit_code = result_exit_code
            execution_error = str(getattr(snapshot, "error", "") or "")
            status_obj = getattr(snapshot, "status", None)
            terminal = bool(getattr(status_obj, "terminal", False))
            ok = bool(getattr(snapshot, "ok", False))

        contract_path = self._resolve_contract_path()
        contract_exists = contract_path.exists()
        contract_size = contract_path.stat().st_size if contract_exists else 0
        started_at = handle_started_at or _float_or_none(lifecycle.get("started_at"))
        engine_status_path = self._resolve_engine_status_path()
        engine_terminal_state = _read_engine_terminal_state(engine_status_path)
        engine_terminal_current = _engine_terminal_state_is_current(
            engine_terminal_state,
            started_at=started_at,
        )
        engine_active_state = _read_engine_active_state(engine_status_path)
        engine_active_current = _engine_terminal_state_is_current(
            engine_active_state,
            started_at=started_at,
        )
        if engine_terminal_current:
            running = False
            terminal = True
            ok = bool(engine_terminal_state.get("ok"))
            exit_code = int(engine_terminal_state.get("exit_code", 1))
            execution_error = str(engine_terminal_state.get("error") or "").strip()
            self._write_lifecycle_terminal_status(
                lifecycle,
                status=str(engine_terminal_state.get("status") or ("success" if ok else "failed")),
                ok=ok,
                exit_code=exit_code,
                error=execution_error,
            )
        contract_terminal_error = _read_contract_terminal_error(contract_path)
        lifecycle_status = str(lifecycle.get("status") or "").strip().lower()
        lifecycle_was_running = lifecycle_status in {"queued", "running"} and lifecycle.get("terminal") is not True
        lifecycle_pid_alive = bool(lifecycle_pid and self._is_pid_alive_sync(lifecycle_pid))
        orphaned_running_lifecycle = (
            lifecycle_was_running
            and lifecycle_pid > 0
            and not lifecycle_pid_alive
            and not running
            and not engine_terminal_current
        )
        contract_current_for_run = (
            contract_exists
            and not running
            and exit_code is None
            and not execution_error
            and self._contract_is_current_for_run(contract_path, started_at)
        )
        if contract_terminal_error and not running and exit_code is None and not execution_error:
            terminal = True
            ok = False
            exit_code = 1
            execution_error = contract_terminal_error
            self._write_lifecycle_terminal_status(
                lifecycle,
                status="failed",
                ok=ok,
                exit_code=exit_code,
                error=execution_error,
            )
        elif engine_active_current and not running and not engine_terminal_current:
            terminal = True
            ok = False
            exit_code = 1
            phase_detail = str(engine_active_state.get("phase") or "active").strip() or "active"
            execution_error = f"PM process exited before terminal engine update (phase={phase_detail})"
            self._write_lifecycle_terminal_status(
                lifecycle,
                status="failed",
                ok=ok,
                exit_code=exit_code,
                error=execution_error,
            )
        elif contract_current_for_run:
            terminal = True
            ok = True
            exit_code = 0
            self._write_lifecycle_terminal_status(
                lifecycle,
                status="success",
                ok=ok,
                exit_code=exit_code,
                error="",
            )
        elif orphaned_running_lifecycle and exit_code is None and not execution_error:
            terminal = True
            ok = False
            exit_code = 1
            execution_error = "PM process exited before terminal lifecycle update"
            self._write_lifecycle_terminal_status(
                lifecycle,
                status="failed",
                ok=ok,
                exit_code=exit_code,
                error=execution_error,
            )
        status_value = execution_status.value if execution_status is not None else None
        source = "execution_broker" if execution_status is not None else "handle"
        if execution_status is None and lifecycle:
            source = "lifecycle"
        if terminal and not running and engine_terminal_current:
            status_value = str(engine_terminal_state.get("status") or ("success" if ok else "failed"))
        if terminal and status_value is None:
            status_value = "success" if ok else "failed"

        return {
            "running": running,
            "pid": handle_pid or (lifecycle_pid or None),
            "mode": handle_mode or _text_value(lifecycle.get("mode")),
            "started_at": started_at,
            "log_path": log_path,
            "source": source,
            "status": status_value,
            "execution_id": execution_id,
            "terminal": terminal,
            "ok": ok,
            "exit_code": exit_code,
            "error": execution_error,
            "contract_path": str(contract_path),
            "contract_exists": contract_exists,
            "contract_size": contract_size,
        }

    def _build_start_response(self, handle: ProcessHandle, *, mode: str) -> dict[str, Any]:
        contract_path = self._resolve_contract_path()
        return {
            "ok": True,
            "pid": handle.pid,
            "mode": mode,
            "execution_id": handle.execution_id,
            "log_path": handle.log_path,
            "started_at": handle.started_at,
            "contract_path": str(contract_path),
            "contract_exists": contract_path.exists(),
        }

    def _resolve_lifecycle_status_path(self) -> Path:
        storage = self._refresh_storage_layout()
        return storage.get_path("status", _PM_LIFECYCLE_STATUS_FILE)

    def _resolve_engine_status_path(self) -> Path:
        storage = self._refresh_storage_layout()
        return storage.get_path("status", "engine.status.json")

    def _read_lifecycle_status(self) -> dict[str, Any]:
        path = self._resolve_lifecycle_status_path()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Failed to read PM lifecycle status from %s: %s", path, exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_lifecycle_status(self, handle: ProcessHandle, *, mode: str) -> None:
        path = self._resolve_lifecycle_status_path()
        payload = {
            "schema_version": "pm.lifecycle.v1",
            "status": "running",
            "mode": mode,
            "execution_id": handle.execution_id,
            "pid": handle.pid,
            "log_path": handle.log_path,
            "started_at": handle.started_at,
            "workspace": str(self._resolve_effective_workspace()),
            "contract_path": str(self._resolve_contract_path()),
            "updated_at": time.time(),
        }
        try:
            write_text_atomic(
                str(path),
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Failed to write PM lifecycle status to %s: %s", path, exc)

    def _write_lifecycle_terminal_status(
        self,
        lifecycle: dict[str, Any],
        *,
        status: str,
        ok: bool | None,
        exit_code: int | None,
        error: str,
    ) -> None:
        path = self._resolve_lifecycle_status_path()
        existing_status = str(lifecycle.get("status") or "").strip().lower()
        if existing_status == status and lifecycle.get("terminal") is True:
            return
        payload = dict(lifecycle) if lifecycle else {}
        payload.update(
            {
                "schema_version": "pm.lifecycle.v1",
                "status": status,
                "terminal": True,
                "ok": ok,
                "exit_code": exit_code,
                "error": error,
                "finished_at": time.time(),
                "updated_at": time.time(),
            }
        )
        try:
            write_text_atomic(
                str(path),
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Failed to write PM terminal lifecycle status to %s: %s", path, exc)

    def _contract_is_current_for_run(self, contract_path: Path, started_at: float | None) -> bool:
        if started_at is None:
            return True
        try:
            return contract_path.stat().st_mtime + _CONTRACT_MTIME_SLOP_SECONDS >= float(started_at)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to compare PM contract mtime for %s: %s", contract_path, exc)
            return False

    def _is_pid_alive_sync(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, int(pid))
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except (AttributeError, OSError, RuntimeError, ValueError):
                return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    async def _check_backend_available(self) -> str | None:
        from polaris.bootstrap.runtime_health import check_backend_available

        return check_backend_available(self._settings)

    async def _clear_stop_flag(self) -> None:
        storage = self._refresh_storage_layout()
        flag_path = storage.get_path("control", "pm.stop.flag")
        if flag_path.exists():
            try:
                flag_path.unlink()
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to clear PM stop flag at %s: %s", flag_path, exc)

    def _resolve_log_path(self) -> str:
        storage = self._refresh_storage_layout()
        return str(storage.get_path("logs", "pm.process.log"))

    def _resolve_contract_path(self) -> Path:
        storage = self._refresh_storage_layout()
        return storage.get_path("contracts", "pm_tasks.contract.json")

    def _resolve_effective_workspace(self) -> Path:
        configured_raw = str(getattr(self._settings, "workspace", "") or "").strip()
        configured_path: Path | None = None
        if configured_raw:
            try:
                candidate = Path(configured_raw).expanduser().resolve()
                if candidate.is_dir():
                    configured_path = candidate
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to resolve configured workspace path from %r: %s", configured_raw, exc)
                configured_path = None

        persisted_path: Path | None = None
        try:
            from polaris.cells.storage.layout.public.service import load_persisted_settings

            persisted_payload = load_persisted_settings(configured_raw)
            persisted_raw = str(
                persisted_payload.get("workspace") if isinstance(persisted_payload, dict) else ""
            ).strip()
            if persisted_raw:
                candidate = Path(persisted_raw).expanduser().resolve()
                if candidate.is_dir():
                    persisted_path = candidate
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to load persisted workspace settings: %s", exc)
            persisted_path = None

        selected = configured_path
        if persisted_path is not None and configured_path is not None:
            should_use_persisted = False
            try:
                default_workspace = Path(find_workspace_root(os.getcwd())).resolve()
                should_use_persisted = configured_path == default_workspace
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to determine if persisted path should be used, defaulting to False: %s", exc)
                should_use_persisted = False
            if persisted_path != configured_path and should_use_persisted:
                selected = persisted_path
        elif persisted_path is not None:
            selected = persisted_path
        elif selected is None:
            selected = Path.cwd().resolve()

        # Ensure selected is never None (fallback to cwd)
        if selected is None:
            selected = Path.cwd().resolve()

        if str(getattr(self._settings, "workspace", "")) != str(selected):
            self._settings.workspace = selected
        return selected

    def _refresh_storage_layout(self, force: bool = False) -> StorageLayout:
        workspace = self._resolve_effective_workspace()
        runtime_base = Path(str(self._settings.runtime_base)).expanduser().resolve()
        candidate = StorageLayout(workspace, runtime_base)
        if force or self._storage is None:
            self._storage = candidate
            return self._storage
        if self._storage.workspace != candidate.workspace or self._storage.runtime_root != candidate.runtime_root:
            self._storage = candidate
        return self._storage

    def _build_command(self, loop_mode: bool, resume: bool = False) -> list[str]:
        settings = self._settings
        workspace = self._resolve_effective_workspace()
        backend = "auto"
        planning_timeout_seconds = self._resolve_planning_timeout_seconds()
        pm_model = self._resolve_pm_model_arg()

        raw_json_log = str(settings.json_log_path or "runtime/events/pm.events.jsonl").strip()
        if not raw_json_log:
            raw_json_log = "runtime/events/pm.events.jsonl"
        if os.path.isabs(raw_json_log):
            json_log_arg = raw_json_log
        else:
            normalized_json_log = raw_json_log.replace("\\", "/").lstrip("/")
            if not normalized_json_log.startswith(("runtime/", "workspace/", "config/")):
                normalized_json_log = f"runtime/{normalized_json_log}"
            json_log_arg = normalized_json_log

        cmd = [
            sys.executable,
            str(settings.pm_script_path),
            "--workspace",
            str(workspace),
            "--pm-backend",
            backend,
            "--model",
            pm_model,
            "--timeout",
            str(planning_timeout_seconds),
            "--json-log",
            json_log_arg,
        ]

        cmd.extend(
            [
                "--agents-approval-mode",
                settings.pm.agents_approval_mode,
                "--agents-approval-timeout",
                str(max(settings.pm.agents_approval_timeout, 0)),
                "--orchestration-runtime",
                "workflow",
            ]
        )

        prompt_profile = str(os.environ.get("KERNELONE_PROMPT_PROFILE", "")).strip()
        if prompt_profile:
            cmd.extend(["--prompt-profile", prompt_profile])
        if settings.runtime.ramdisk_root:
            cmd.extend(["--ramdisk-root", str(settings.runtime.ramdisk_root)])

        cmd.extend(
            [
                "--max-failures",
                str(settings.pm.max_failures),
                "--max-blocked",
                str(settings.pm.max_blocked),
                "--max-same-task",
                str(settings.pm.max_same),
            ]
        )

        cmd.extend(
            [
                "--blocked-strategy",
                str(settings.pm.blocked_strategy),
                "--blocked-degrade-max-retries",
                str(settings.pm.blocked_degrade_max_retries),
            ]
        )

        if settings.pm.show_output:
            cmd.append("--pm-show-output")

        if loop_mode:
            loop_interval = int(os.environ.get("KERNELONE_PM_LOOP_INTERVAL", "20") or 20)
            cmd.extend(["--loop", "--interval", str(max(loop_interval, 1))])
            if resume:
                cmd.append("--resume")

        if settings.pm.runs_director:
            cmd.append("--run-director")
            if settings.pm.director_show_output:
                cmd.append("--director-show-output")
            cmd.extend(
                [
                    "--director-result-timeout",
                    str(settings.pm.director_timeout),
                    "--director-iterations",
                    str(settings.pm.director_iterations),
                    "--director-workflow-execution-mode",
                    str(settings.director.execution_mode),
                    "--director-max-parallel-tasks",
                    str(settings.director.max_parallel_tasks),
                    "--director-ready-timeout-seconds",
                    str(settings.director.ready_timeout_seconds),
                    "--director-claim-timeout-seconds",
                    str(settings.director.claim_timeout_seconds),
                    "--director-phase-timeout-seconds",
                    str(settings.director.phase_timeout_seconds),
                    "--director-complete-timeout-seconds",
                    str(settings.director.complete_timeout_seconds),
                    "--director-task-timeout-seconds",
                    str(settings.director.task_timeout_seconds),
                ]
            )
            if settings.pm.director_match_mode:
                cmd.extend(["--director-match-mode", settings.pm.director_match_mode])
            director_model = self._resolve_role_model_arg("director", fallback=_text_value(settings.director.model))
            if director_model:
                cmd.extend(["--director-model", director_model])

        return cmd

    def _resolve_role_selection(self, role_id: str) -> RoleModelSelection:
        try:
            from polaris.kernelone.llm.runtime_config import load_role_config

            role_config = load_role_config(role_id)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to resolve %s role model binding: %s", role_id, exc)
            return RoleModelSelection()
        if role_config is None:
            return RoleModelSelection()
        return RoleModelSelection(
            provider_id=_text_value(getattr(role_config, "provider_id", "")),
            model=_text_value(getattr(role_config, "model", "")),
        )

    def _resolve_role_model_arg(self, role_id: str, *, fallback: str = "") -> str:
        fallback_model = _text_value(fallback)
        if fallback_model:
            return fallback_model
        selection = self._resolve_role_selection(role_id)
        return selection.model

    def _resolve_pm_model_arg(self) -> str:
        explicit_pm_model = _text_value(getattr(self._settings.pm, "model", ""))
        if explicit_pm_model:
            return explicit_pm_model

        role_model = self._resolve_role_model_arg("pm")
        if role_model:
            return role_model

        global_model = _text_value(getattr(self._settings.llm, "model", ""))
        if global_model:
            return global_model

        return "gpt-4"

    def _resolve_planning_timeout_seconds(self) -> int:
        env_timeout = _parse_positive_int(os.environ.get(_PM_PLANNING_TIMEOUT_ENV))
        if env_timeout > 0:
            return _clamp_pm_planning_timeout(env_timeout)

        settings_timeout = _parse_positive_int(getattr(self._settings, "timeout", 0))
        if settings_timeout > 0:
            return self._apply_planning_timeout_floor(_clamp_pm_planning_timeout(settings_timeout))

        llm_config = getattr(self._settings, "llm", None)
        llm_timeout = _parse_positive_int(getattr(llm_config, "timeout", 0))
        if llm_timeout > 0:
            return self._apply_planning_timeout_floor(_clamp_pm_planning_timeout(llm_timeout))

        return self._apply_planning_timeout_floor(_DEFAULT_PM_PLANNING_TIMEOUT_SECONDS)

    def _apply_planning_timeout_floor(self, seconds: int) -> int:
        if not self._pm_uses_codex_runtime():
            return seconds
        floor = _parse_positive_int(os.environ.get(_PM_CODEX_MIN_PLANNING_TIMEOUT_ENV))
        if floor <= 0:
            floor = _DEFAULT_CODEX_PM_PLANNING_TIMEOUT_SECONDS
        return max(seconds, _clamp_pm_planning_timeout(floor))

    def _pm_uses_codex_runtime(self) -> bool:
        pm_model = self._resolve_pm_model_arg().lower()
        if "codex" in pm_model:
            return True

        explicit_pm_model = _text_value(getattr(self._settings.pm, "model", ""))
        if explicit_pm_model:
            return False

        provider_id = self._resolve_role_selection("pm").provider_id.lower()
        return provider_id in _CODEX_PROVIDER_IDS

    async def _spawn_process(self, cmd: list[str], log_path: str) -> ProcessHandle:
        """Spawn PM process through runtime.execution_broker cell."""
        workspace = self._resolve_effective_workspace()
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("KERNELONE_LOOP_MODULE_DIR", str(self._settings.loop_module_dir))
        env["KERNELONE_RUNTIME_CACHE_ROOT"] = str(self._settings.runtime_base)
        if self._settings.runtime.root:
            env["KERNELONE_RUNTIME_ROOT"] = str(self._settings.runtime.root)
        else:
            env.pop("KERNELONE_RUNTIME_ROOT", None)
        env["KERNELONE_WORKSPACE"] = str(workspace)
        pm_selection = self._resolve_role_selection("pm")
        if pm_selection.provider_id:
            env["KERNELONE_PM_PROVIDER"] = pm_selection.provider_id
        env["KERNELONE_PM_MODEL"] = self._resolve_pm_model_arg()

        broker = get_execution_broker_service()
        timeout_seconds = float(
            max(
                int(os.environ.get("KERNELONE_PM_PROCESS_TIMEOUT_SECONDS", "86400") or 86400),
                1,
            )
        )
        command = LaunchExecutionProcessCommandV1(
            name="pm-service",
            args=tuple(cmd),
            workspace=str(workspace),
            timeout_seconds=timeout_seconds,
            env=env,
            log_path=log_path,
            metadata={
                "service": "pm_planning",
                "workspace": str(workspace),
            },
        )
        launch_result = await broker.launch_process(command)
        if not launch_result.success or launch_result.handle is None:
            raise RuntimeError(launch_result.error_message or "execution broker launch failed")

        runtime_process = broker.resolve_runtime_process(launch_result.handle)
        return ProcessHandle(
            process=runtime_process,
            log_handle=None,
            log_path=log_path,
            started_at=time.time(),
            execution_id=launch_result.handle.execution_id,
        )


_pm_service: PMService | None = None
_pm_lock = asyncio.Lock()


async def get_pm_service() -> PMService:
    global _pm_service
    if _pm_service is None:
        async with _pm_lock:
            if _pm_service is None:
                settings = get_settings()
                storage = StorageLayout(settings.workspace, settings.runtime_base)
                _pm_service = PMService(settings, storage)
    return _pm_service


def reset_pm_service() -> None:
    global _pm_service
    _pm_service = None


def _terminate_process_impl(handle: ProcessHandle, *, graceful: bool = False, graceful_timeout: float = 3.0) -> None:
    if handle.process is None:
        return
    process = handle.process
    pid = process.pid
    if graceful and pid and os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle_ctrl = kernel32.OpenProcess(1, False, pid)
            if handle_ctrl:
                try:
                    kernel32.FreeConsole()
                    kernel32.AttachConsole(pid)
                    kernel32.GenerateConsoleCtrlEvent(0, 0)
                    kernel32.FreeConsole()
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Failed to send CTRL event to pid=%s: %s", pid, exc)
                finally:
                    kernel32.CloseHandle(handle_ctrl)
            process.wait(timeout=graceful_timeout)
            _cleanup_handle(handle)
            return
        except (RuntimeError, ValueError) as exc:
            logger.debug("Graceful process termination failed for pid=%s: %s", pid, exc)
    if os.name == "nt" and pid:
        try:
            terminate_pid(pid)
        except (RuntimeError, ValueError) as exc:
            logger.debug("terminate_pid during cleanup failed for pid=%s: %s", pid, exc)
    try:
        process.terminate()
        process.wait(timeout=3)
    except (RuntimeError, ValueError) as exc:
        logger.debug("process.terminate failed for pid=%s: %s", pid, exc)
        try:
            process.kill()
            process.wait(timeout=3)
        except (RuntimeError, ValueError) as kill_exc:
            logger.debug("process.kill failed for pid=%s: %s", pid, kill_exc)
    _cleanup_handle(handle)


def _cleanup_handle(handle: ProcessHandle) -> None:
    handle.process = None
    handle.mode = ""
    handle.started_at = None
    handle.execution_id = None
    if handle.log_handle is not None:
        try:
            handle.log_handle.close()  # type: ignore[attr-defined]
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to close PM log handle: %s", exc)
        handle.log_handle = None
