"""PM (Project Manager) Cell Service.

This service encapsulates business logic for PM operations,
separating it from the delivery layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings, find_workspace_root, get_settings
from polaris.cells.runtime.execution_broker.public.contracts import (
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

    async def run_once(self) -> dict:
        """Run PM once."""
        async with self._lifecycle_lock:
            if self._handle.is_running:
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
                return {"ok": True, "pid": handle.pid}
            except (RuntimeError, ValueError) as exc:
                raise ProcessError("Failed to start PM process", process_name="pm", cause=exc) from exc

    async def start_loop(self, resume: bool = False) -> dict:
        """Start PM in loop mode."""
        async with self._lifecycle_lock:
            if self._handle.is_running:
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
                return {
                    "ok": True,
                    "pid": handle.pid,
                    "mode": self._handle.mode,
                    "resume": resume,
                }
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

            if not self._handle.is_running:
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

    def get_status(self) -> dict:
        if self._handle.process and not self._handle.is_running:
            self._handle.terminate()

        log_path = self._handle.log_path
        if not log_path:
            log_path = self._resolve_log_path()

        return {
            "running": self._handle.is_running,
            "pid": self._handle.pid,
            "mode": self._handle.mode,
            "started_at": self._handle.started_at,
            "log_path": log_path,
        }

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
            settings.pm.model or settings.llm.model,
            "--timeout",
            str(0),
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
            if settings.director.model:
                cmd.extend(["--director-model", settings.director.model])

        return cmd

    async def _spawn_process(self, cmd: list[str], log_path: str) -> ProcessHandle:
        """Spawn PM process through runtime.execution_broker cell."""
        workspace = self._resolve_effective_workspace()
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("KERNELONE_LOOP_MODULE_DIR", str(self._settings.loop_module_dir))
        env["KERNELONE_WORKSPACE"] = str(workspace)

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
