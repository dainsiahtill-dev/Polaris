import asyncio
import logging
import os
import sys
import threading
import time
import typing
from typing import Any

from polaris.bootstrap.config import Settings, get_settings
from polaris.cells.runtime.execution_broker.public.contracts import (
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import (
    get_execution_broker_service,
)
from polaris.cells.runtime.state_owner.public.service import ProcessHandle
from polaris.infrastructure.realtime.process_local.signal_hub import REALTIME_SIGNAL_HUB
from polaris.kernelone.fs.encoding import build_utf8_env
from polaris.kernelone.process import (
    clear_director_stop_flag as _kernel_clear_director_stop_flag,
    clear_stop_flag as _kernel_clear_stop_flag,
    director_stop_flag_path as _kernel_director_stop_flag_path,
    list_external_loop_pm_pids as _kernel_list_external_loop_pm_pids,
    terminate_external_loop_pm_processes as _kernel_terminate_external_loop_pm_processes,
    terminate_pid as _kernel_terminate_pid,
)
from polaris.kernelone.runtime.defaults import DEFAULT_MODEL, DEFAULT_PM_LOG, DEFAULT_WORKSPACE
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds
from polaris.kernelone.storage.io_paths import normalize_artifact_rel_path

logger = logging.getLogger(__name__)


def pm_command(settings: Settings, loop_mode: bool, resume: bool = False) -> list[str]:
    backend = (settings.pm_backend or "auto").strip().lower()
    if backend not in ("auto", "codex", "ollama"):
        backend = "auto"
    # PM backend is always role-mapping-driven at runtime.
    backend = "auto"
    json_log_path = normalize_artifact_rel_path(settings.json_log_path or DEFAULT_PM_LOG) or DEFAULT_PM_LOG
    cmd = [
        sys.executable,
        str(settings.pm_script_path),
        "--workspace",
        str(settings.workspace or DEFAULT_WORKSPACE),
        "--pm-backend",
        backend,
        "--model",
        settings.pm_model or settings.model or DEFAULT_MODEL,
        "--timeout",
        str(settings.timeout or 0),
        "--json-log",
        json_log_path,
    ]
    if settings.pm_show_output:
        cmd.append("--pm-show-output")
    approval_mode = str(getattr(settings, "pm_agents_approval_mode", "auto_accept") or "auto_accept")
    approval_timeout = normalize_timeout_seconds(
        getattr(settings, "pm_agents_approval_timeout", None),
        default=90,
    )
    cmd.extend(
        [
            "--agents-approval-mode",
            approval_mode,
            "--agents-approval-timeout",
            str(max(approval_timeout, 0)),
        ]
    )
    prompt_profile = str(os.environ.get("KERNELONE_PROMPT_PROFILE", "")).strip()
    if prompt_profile:
        cmd.extend(["--prompt-profile", prompt_profile])
    if settings.ramdisk_root:
        cmd.extend(["--ramdisk-root", settings.ramdisk_root])
    cmd.extend(
        [
            "--max-failures",
            str(settings.pm_max_failures or 5),
            "--max-blocked",
            str(settings.pm_max_blocked or 5),
            "--max-same-task",
            str(settings.pm_max_same or 3),
        ]
    )
    if loop_mode:
        loop_interval = int(os.environ.get("KERNELONE_PM_LOOP_INTERVAL", "20") or 20)
        cmd.extend(["--loop", "--interval", str(max(loop_interval, 1))])
        if resume:
            cmd.append("--resume")
    if settings.pm_runs_director:
        cmd.append("--run-director")
        if settings.pm_director_show_output:
            cmd.append("--director-show-output")
        try:
            director_result_timeout = normalize_timeout_seconds(
                settings.pm_director_timeout if settings.pm_director_timeout is not None else 600,
                default=600,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "normalize_timeout_seconds failed for pm_director_timeout=%r: %s, using fallback 600",
                settings.pm_director_timeout,
                exc,
            )
            director_result_timeout = 600
        cmd.extend(["--director-result-timeout", str(director_result_timeout)])
        cmd.extend(["--director-iterations", str(settings.pm_director_iterations or 1)])
        if settings.pm_director_match_mode:
            cmd.extend(["--director-match-mode", settings.pm_director_match_mode])
        if settings.director_model:
            cmd.extend(["--director-model", settings.director_model])
    return cmd


def director_command(settings: Settings) -> list[str]:
    iterations = settings.director_iterations or 1
    cmd = [
        sys.executable,
        str(settings.director_script_path),
        "--workspace",
        str(settings.workspace or DEFAULT_WORKSPACE),
    ]
    if settings.director_model or settings.model:
        cmd.extend(["--model", settings.director_model or settings.model])
    prompt_profile = str(os.environ.get("KERNELONE_PROMPT_PROFILE", "")).strip()
    if prompt_profile:
        cmd.extend(["--prompt-profile", prompt_profile])
    if settings.ramdisk_root:
        cmd.extend(["--ramdisk-root", settings.ramdisk_root])
    if settings.director_forever:
        cmd.append("--forever")
    else:
        cmd.extend(["--iterations", str(max(iterations, 1))])
    if settings.director_show_output:
        cmd.append("--show-output")
    if bool(getattr(settings, "slm_enabled", False)):
        cmd.append("--slm-enabled")
    return cmd


_INVARIANT_IO_FSYNC_MODES = {"strict", "relaxed"}
_INVARIANT_MEMORY_REFS_MODES = {"strict", "soft", "off"}


def _normalize_invariant_mode(value: str | None, allowed: set, default: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in allowed:
        return raw
    return default


def build_invariants_env(settings: Settings) -> dict[str, str]:
    del settings
    io_mode = _normalize_invariant_mode(
        os.environ.get("KERNELONE_IO_FSYNC_MODE", "strict"),
        _INVARIANT_IO_FSYNC_MODES,
        "strict",
    )
    mem_mode = _normalize_invariant_mode(
        os.environ.get("KERNELONE_MEMORY_REFS_MODE", "soft"),
        _INVARIANT_MEMORY_REFS_MODES,
        "soft",
    )
    return {
        "KERNELONE_IO_FSYNC_MODE": io_mode,
        "KERNELONE_MEMORY_REFS_MODE": mem_mode,
    }


_DEFAULT_BROKER_PROCESS_TIMEOUT_SECONDS = 86400
_BROKER_LOOP: asyncio.AbstractEventLoop | None = None
_BROKER_LOOP_THREAD: threading.Thread | None = None
_BROKER_LOOP_LOCK = threading.Lock()


def _start_broker_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _ensure_broker_loop() -> asyncio.AbstractEventLoop:
    global _BROKER_LOOP, _BROKER_LOOP_THREAD
    with _BROKER_LOOP_LOCK:
        if _BROKER_LOOP is not None and _BROKER_LOOP.is_running():
            return _BROKER_LOOP
        loop = asyncio.new_event_loop()
        worker = threading.Thread(
            target=_start_broker_loop,
            args=(loop,),
            name="roles-runtime-execution-broker-loop",
            daemon=True,
        )
        worker.start()
        _BROKER_LOOP = loop
        _BROKER_LOOP_THREAD = worker
        return loop


def _run_coroutine_sync(coro: typing.Coroutine[Any, Any, Any]) -> Any:
    """Run async broker call from sync context safely."""
    loop = _ensure_broker_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def spawn_process(
    cmd: list[str],
    cwd: str,
    log_path: str,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = 0,
) -> ProcessHandle:
    """Spawn a subprocess and return a handle.

    Args:
        cmd: Command and arguments as a list.
        cwd: Working directory.
        log_path: Path to the log file (appended).
        extra_env: Additional environment variables.
        timeout_seconds: Optional timeout hint for the subprocess.
            NOTE: actual timeout enforcement is the caller's responsibility.
    """
    env = build_utf8_env(extra_env)
    loop_module_dir = str(get_settings().loop_module_dir)
    if loop_module_dir:
        env.setdefault("KERNELONE_LOOP_MODULE_DIR", loop_module_dir)
    timeout_value = timeout_seconds if timeout_seconds > 0 else _DEFAULT_BROKER_PROCESS_TIMEOUT_SECONDS

    async def _launch() -> tuple[Any | None, str]:
        broker = get_execution_broker_service()
        command = LaunchExecutionProcessCommandV1(
            name="roles-runtime-process",
            args=tuple(cmd),
            workspace=str(cwd),
            timeout_seconds=float(timeout_value),
            env=env,
            log_path=log_path,
            metadata={
                "service": "roles.runtime.internal.process_service",
                "cwd": str(cwd),
            },
        )
        launch_result = await broker.launch_process(command)
        if not launch_result.success or launch_result.handle is None:
            raise RuntimeError(launch_result.error_message or "execution broker launch failed")
        runtime_process = broker.resolve_runtime_process(launch_result.handle)
        return runtime_process, launch_result.handle.execution_id

    try:
        process, execution_id = _run_coroutine_sync(_launch())
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to spawn process %s in %s via execution broker: %s", cmd, cwd, exc)
        raise

    if process is not None:
        _register_process_exit_notification(process, log_path)
    else:
        _register_execution_exit_notification(execution_id, log_path)
    handle = ProcessHandle(
        process=process,
        log_handle=None,
        log_path=log_path,
        started_at=time.time(),
        execution_id=execution_id,
    )
    return handle


def _register_process_exit_notification(process: Any, log_path: str) -> None:
    """Emit a realtime signal when a spawned subprocess exits."""

    def _watch() -> None:
        try:
            process.wait()
        except (RuntimeError, ValueError) as exc:
            logger.warning("Process wait watcher failed for %s: %s", log_path, exc)
            return
        REALTIME_SIGNAL_HUB.notify_from_thread(
            source="process_exit",
            path=str(log_path or ""),
        )

    watcher = threading.Thread(
        target=_watch,
        name=f"polaris-process-exit-{getattr(process, 'pid', 'unknown')}",
        daemon=True,
    )
    watcher.start()


def _register_execution_exit_notification(execution_id: str, log_path: str) -> None:
    """Fallback notifier when raw subprocess handle is unavailable."""

    def _watch() -> None:
        try:

            async def _wait_for_completion() -> None:
                broker = get_execution_broker_service()
                await broker.wait_process(execution_id)

            _run_coroutine_sync(_wait_for_completion())
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Execution wait watcher failed for execution_id=%s log_path=%s: %s",
                execution_id,
                log_path,
                exc,
            )
            return
        REALTIME_SIGNAL_HUB.notify_from_thread(
            source="process_exit",
            path=str(log_path or ""),
        )

    watcher = threading.Thread(
        target=_watch,
        name=f"polaris-exec-exit-{execution_id}",
        daemon=True,
    )
    watcher.start()


def terminate_process(handle: ProcessHandle) -> None:
    process = handle.process
    if process is None:
        execution_id = getattr(handle, "execution_id", None)
        if execution_id:
            try:

                async def _terminate() -> None:
                    broker = get_execution_broker_service()
                    await broker.terminate_process(str(execution_id))

                _run_coroutine_sync(_terminate())
            except (RuntimeError, ValueError) as exc:
                logger.warning("execution broker terminate failed for execution_id=%s: %s", execution_id, exc)
        return
    pid = None
    try:
        pid = process.pid
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to read process pid: %s", exc)
        pid = None
    if os.name == "nt" and pid:
        try:
            _kernel_terminate_pid(pid)
        except (RuntimeError, ValueError) as exc:
            logger.warning("terminate_pid failed for pid=%s: %s", pid, exc)
    try:
        process.terminate()
    except (RuntimeError, ValueError) as exc:
        logger.warning("process.terminate failed for pid=%s: %s", pid, exc)
    try:
        process.wait(timeout=3)
    except (RuntimeError, ValueError) as exc:
        logger.warning("process.wait(timeout=3) failed for pid=%s: %s", pid, exc)
        try:
            process.kill()
        except (RuntimeError, ValueError) as kill_exc:
            logger.warning("process.kill failed for pid=%s: %s", pid, kill_exc)
        try:
            process.wait(timeout=3)
        except (RuntimeError, ValueError) as wait_exc:
            logger.warning("process.wait after kill failed for pid=%s: %s", pid, wait_exc)
    handle.process = None
    handle.mode = ""
    handle.started_at = None
    if hasattr(handle, "execution_id"):
        handle.execution_id = None
    if handle.log_handle is not None:
        try:
            handle.log_handle.close()
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to close process log handle for pid=%s: %s", pid, exc)
        handle.log_handle = None


def terminate_pid(pid: int) -> bool:
    return _kernel_terminate_pid(pid)


def list_external_loop_pm_pids(workspace: str, exclude_pid: int | None = None) -> list[int]:
    return _kernel_list_external_loop_pm_pids(workspace, exclude_pid=exclude_pid)


def terminate_external_loop_pm_processes(workspace: str, exclude_pid: int | None = None) -> list[int]:
    return _kernel_terminate_external_loop_pm_processes(workspace, exclude_pid=exclude_pid)


def clear_stop_flag(workspace: str, cache_root: str) -> None:
    _kernel_clear_stop_flag(workspace, cache_root=cache_root)


def director_stop_flag_path(workspace: str, cache_root: str) -> str:
    return _kernel_director_stop_flag_path(workspace, cache_root=cache_root)


def clear_director_stop_flag(workspace: str, cache_root: str) -> None:
    _kernel_clear_director_stop_flag(workspace, cache_root=cache_root)
