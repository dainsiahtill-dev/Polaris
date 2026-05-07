"""Service implementation for ``runtime.execution_broker``."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionBrokerError,
    ExecutionErrorCode,
    ExecutionProcessHandleV1,
    ExecutionProcessLaunchResultV1,
    ExecutionProcessStatusV1,
    ExecutionProcessWaitResultV1,
    GetExecutionProcessStatusQueryV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.kernelone.fs.encoding import build_utf8_env
from polaris.kernelone.fs.text_ops import open_text_log_append
from polaris.kernelone.runtime import (
    AsyncTaskSpec,
    BlockingIoSpec,
    BoundedCache,
    ExecutionFacade,
    ExecutionHandle,
    ExecutionLane,
    ExecutionSnapshot,
    ExecutionStatus,
    ProcessSpec,
    get_shared_execution_facade,
    reset_shared_execution_facade,
)
from polaris.kernelone.trace import create_task_with_context, get_logger
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from collections.abc import Iterable

_logger = get_logger(__name__)

_STATUS_MAP: dict[ExecutionStatus, ExecutionProcessStatusV1] = {
    ExecutionStatus.QUEUED: ExecutionProcessStatusV1.QUEUED,
    ExecutionStatus.RUNNING: ExecutionProcessStatusV1.RUNNING,
    ExecutionStatus.SUCCESS: ExecutionProcessStatusV1.SUCCESS,
    ExecutionStatus.FAILED: ExecutionProcessStatusV1.FAILED,
    ExecutionStatus.TIMED_OUT: ExecutionProcessStatusV1.TIMED_OUT,
    ExecutionStatus.CANCELLED: ExecutionProcessStatusV1.CANCELLED,
}
_LOG_DRAIN_MAX_SECONDS_ENV = "KERNELONE_EXECUTION_BROKER_LOG_DRAIN_MAX_SECONDS"
_LOG_DRAIN_TERMINAL_IDLE_SECONDS = 5.0
_SENSITIVE_ARG_NAMES = {
    "--api-key",
    "--apikey",
    "--auth-token",
    "--password",
    "--secret",
    "--token",
}


def _resolve_log_drain_max_seconds() -> float | None:
    raw = str(os.environ.get(_LOG_DRAIN_MAX_SECONDS_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(1.0, seconds)


def _to_process_status(status: ExecutionStatus) -> ExecutionProcessStatusV1:
    return _STATUS_MAP.get(status, ExecutionProcessStatusV1.UNKNOWN)


def _extract_exit_code(snapshot: ExecutionSnapshot) -> int | None:
    result = snapshot.result
    if result is None:
        return None
    if isinstance(result, dict):
        value = result.get("exit_code")
        return int(value) if isinstance(value, int) else None
    value = getattr(result, "exit_code", None)
    return int(value) if isinstance(value, int) else None


def _format_args_for_log(args: Iterable[str]) -> str:
    sanitized: list[str] = []
    redact_next = False
    for raw in args:
        value = str(raw)
        lowered = value.strip().lower()
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        if lowered in _SENSITIVE_ARG_NAMES:
            sanitized.append(value)
            redact_next = True
            continue
        if any(lowered.startswith(f"{name}=") for name in _SENSITIVE_ARG_NAMES):
            key = value.split("=", 1)[0]
            sanitized.append(f"{key}=[REDACTED]")
            continue
        sanitized.append(value)
    return shlex.join(sanitized)


async def _wait_for_terminal_snapshot(handle: ExecutionHandle, *, timeout_seconds: float = 10.0) -> ExecutionSnapshot:
    with contextlib.suppress(TimeoutError):
        await handle.wait(timeout=max(timeout_seconds, 0.0))
    return handle.snapshot()


class ExecutionBrokerService:
    """Cell-layer broker over KernelOne execution runtime facade."""

    def __init__(self, *, facade: ExecutionFacade | None = None) -> None:
        self._facade = facade or get_shared_execution_facade()
        self._process_handles: BoundedCache[str, ExecutionProcessHandleV1] = BoundedCache(max_size=500)
        self._handles_lock = asyncio.Lock()
        self._process_log_tasks: BoundedCache[str, asyncio.Task[None]] = BoundedCache(max_size=200)
        self._log_tasks_lock = asyncio.Lock()

    @property
    def facade(self) -> ExecutionFacade:
        return self._facade

    def submit_async_task(self, spec: AsyncTaskSpec) -> ExecutionHandle:
        return self._facade.submit_async_task(spec)

    def submit_blocking_io(self, spec: BlockingIoSpec) -> ExecutionHandle:
        return self._facade.submit_blocking_io(spec)

    async def submit_process_spec(self, spec: ProcessSpec) -> ExecutionHandle:
        return await self._facade.submit_process(spec)

    async def launch_process(
        self,
        command: LaunchExecutionProcessCommandV1,
    ) -> ExecutionProcessLaunchResultV1:
        _logger.info(
            "execution_broker.process.launching",
            execution_id=command.name,
            workspace=command.workspace,
            timeout_seconds=command.timeout_seconds,
        )
        try:
            env = build_utf8_env(dict(command.env))
            stdin_lines = self._normalize_stdin(command.stdin_input)
            handle = await self._facade.submit_process(
                ProcessSpec(
                    name=command.name,
                    args=list(command.args),
                    cwd=Path(command.workspace),
                    env=env,
                    stdin_lines=stdin_lines,
                    timeout_seconds=command.timeout_seconds,
                    metadata={
                        # Internal fields FIRST to prevent user metadata override
                        "workspace": command.workspace,
                        "log_path": command.log_path or "",
                        "execution_broker": "runtime.execution_broker",
                        "args": list(command.args),
                        # User metadata stored separately - cannot override internal fields
                        "_user_metadata": dict(command.metadata),
                    },
                )
            )
            process_handle = ExecutionProcessHandleV1(
                execution_id=handle.execution_id,
                pid=handle.pid,
                name=command.name,
                workspace=command.workspace,
                log_path=command.log_path,
                metadata={
                    # User metadata stored under _user_metadata key
                    "_user_metadata": dict(command.metadata),
                },
            )
            async with self._handles_lock:
                self._process_handles.set(process_handle.execution_id, process_handle)
            if command.log_path:
                await self._register_log_drain(handle, command.log_path)

            _logger.info(
                "execution_broker.process.launched",
                execution_id=handle.execution_id,
                pid=handle.pid,
                workspace=command.workspace,
            )

            return ExecutionProcessLaunchResultV1(
                success=True,
                handle=process_handle,
                launched_at=_utc_now(),
            )
        except FileNotFoundError as exc:
            _logger.error(
                "execution_broker.process.launch_failed",
                error=str(exc),
                error_type="FileNotFoundError",
                execution_id=command.name,
            )
            return ExecutionProcessLaunchResultV1(
                success=False,
                error_message=str(exc),
                error_code=ExecutionErrorCode.LAUNCH_FAILED,
                launched_at=_utc_now(),
            )
        except PermissionError as exc:
            _logger.error(
                "execution_broker.process.launch_failed",
                error=str(exc),
                error_type="PermissionError",
                execution_id=command.name,
            )
            return ExecutionProcessLaunchResultV1(
                success=False,
                error_message=str(exc),
                error_code=ExecutionErrorCode.LAUNCH_FAILED,
                launched_at=_utc_now(),
            )
        except TimeoutError as exc:
            _logger.error(
                "execution_broker.process.launch_failed",
                error=str(exc),
                error_type="TimeoutError",
                execution_id=command.name,
            )
            return ExecutionProcessLaunchResultV1(
                success=False,
                error_message=str(exc),
                error_code=ExecutionErrorCode.TIMEOUT_EXCEEDED,
                launched_at=_utc_now(),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except (RuntimeError, ValueError) as exc:
            _logger.error(
                "execution_broker.process.launch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                execution_id=command.name,
            )
            return ExecutionProcessLaunchResultV1(
                success=False,
                error_message=str(exc),
                error_code=ExecutionErrorCode.UNKNOWN_ERROR,
                launched_at=_utc_now(),
            )

    async def wait_process(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionProcessWaitResultV1:
        process_handle = await self.resolve_process_handle(handle_or_id)

        _logger.info(
            "execution_broker.process.waiting",
            execution_id=process_handle.execution_id,
            timeout_seconds=timeout_seconds,
        )

        runtime_handle = self._facade.resolve_handle(process_handle.execution_id)
        start_time = _utc_now()
        try:
            await runtime_handle.wait(timeout=timeout_seconds)
        except TimeoutError as exc:
            _logger.warning(
                "execution_broker.process.timeout",
                execution_id=process_handle.execution_id,
                timeout_seconds=timeout_seconds,
            )
            snapshot = runtime_handle.snapshot()
            return ExecutionProcessWaitResultV1(
                handle=process_handle,
                status=_to_process_status(snapshot.status),
                success=False,
                exit_code=_extract_exit_code(snapshot),
                timed_out=True,
                error_message=str(exc),
                error_code=ExecutionErrorCode.TIMEOUT_EXCEEDED,
                completed_at=_utc_now(),
            )

        await self._await_log_drain(process_handle.execution_id)
        snapshot = runtime_handle.snapshot()
        duration_delta = (snapshot.finished_at or _utc_now()) - start_time
        duration_ms: float = duration_delta.total_seconds() * 1000 if hasattr(duration_delta, "total_seconds") else 0

        _logger.info(
            "execution_broker.process.completed",
            execution_id=process_handle.execution_id,
            status=snapshot.status.value,
            exit_code=_extract_exit_code(snapshot),
            duration_ms=duration_ms,
        )

        return ExecutionProcessWaitResultV1(
            handle=process_handle,
            status=_to_process_status(snapshot.status),
            success=snapshot.ok,
            exit_code=_extract_exit_code(snapshot),
            timed_out=snapshot.status == ExecutionStatus.TIMED_OUT,
            error_message=snapshot.error or None,
            completed_at=_utc_now(),
        )

    async def terminate_process(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
        *,
        timeout_seconds: float = 5.0,
    ) -> bool:
        process_handle = await self.resolve_process_handle(handle_or_id)

        _logger.warning(
            "execution_broker.process.terminating",
            execution_id=process_handle.execution_id,
            timeout_seconds=timeout_seconds,
        )

        runtime_handle = self._facade.resolve_handle(process_handle.execution_id)
        terminated = await runtime_handle.terminate(timeout=timeout_seconds)
        await self._await_log_drain(process_handle.execution_id)

        _logger.info(
            "execution_broker.process.terminated",
            execution_id=process_handle.execution_id,
            success=terminated,
        )

        return terminated

    async def cancel_execution(self, execution_id: str) -> bool:
        return await self._facade.resolve_handle(execution_id).cancel()

    def get_process_status(
        self,
        query: GetExecutionProcessStatusQueryV1,
    ) -> ExecutionProcessStatusV1:
        snapshot = self._facade.snapshot(query.execution_id)
        return _to_process_status(snapshot.status)

    def get_process_snapshot(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
    ) -> ExecutionSnapshot:
        process_handle = self.resolve_process_handle_sync(handle_or_id)
        return self._facade.snapshot(process_handle.execution_id)

    async def resolve_process_handle(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
    ) -> ExecutionProcessHandleV1:
        """Resolve a process handle from ID or handle object (async, thread-safe)."""
        if isinstance(handle_or_id, ExecutionProcessHandleV1):
            async with self._handles_lock:
                self._process_handles.set(handle_or_id.execution_id, handle_or_id)
            return handle_or_id
        execution_id = str(handle_or_id)
        async with self._handles_lock:
            known = self._process_handles.get(execution_id)
        if known is not None:
            return known

        snapshot = self._facade.snapshot(execution_id)
        if snapshot.lane != ExecutionLane.SUBPROCESS:
            raise ExecutionBrokerError(
                "Execution handle is not a subprocess lane",
                code=ExecutionErrorCode.EXECUTION_NOT_SUBPROCESS.value,
                details={"execution_id": execution_id, "lane": snapshot.lane.value},
            )
        # 保持与 launch_process 一致的 metadata 隔离设计
        discovered = ExecutionProcessHandleV1(
            execution_id=execution_id,
            pid=snapshot.pid,
            name=snapshot.name,
            workspace=str(snapshot.metadata.get("workspace") or "."),
            log_path=str(snapshot.metadata.get("log_path") or "") or None,
            metadata={
                # 用户元数据隔离存储，不暴露内部字段
                "_user_metadata": dict(snapshot.metadata.get("_user_metadata", {})),
            },
        )
        async with self._handles_lock:
            self._process_handles.set(execution_id, discovered)
        return discovered

    def resolve_process_handle_sync(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
    ) -> ExecutionProcessHandleV1:
        """Synchronous version for non-async contexts (e.g., get_process_snapshot)."""
        if isinstance(handle_or_id, ExecutionProcessHandleV1):
            return handle_or_id
        execution_id = str(handle_or_id)
        known = self._process_handles.get(execution_id)
        if known is not None:
            return known
        raise ExecutionBrokerError(
            "Execution handle not found in local registry",
            code=ExecutionErrorCode.PROCESS_NOT_FOUND.value,
            details={"execution_id": execution_id},
        )

    def resolve_runtime_process(
        self,
        handle_or_id: ExecutionProcessHandleV1 | str,
    ) -> Any | None:
        process_handle = self.resolve_process_handle_sync(handle_or_id)
        runtime_handle = self._facade.resolve_handle(process_handle.execution_id)
        return runtime_handle.process

    async def list_active_processes(self) -> list[ExecutionProcessHandleV1]:
        """List active processes."""
        active: list[ExecutionProcessHandleV1] = []
        async with self._handles_lock:
            handle_ids = self._process_handles.keys()
        for execution_id in handle_ids:
            with contextlib.suppress(KeyError):
                snapshot = self._facade.snapshot(execution_id)
                if snapshot.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
                    handle = self._process_handles.get(execution_id)
                    if handle is not None:
                        active.append(handle)
        return active

    async def wait_many_processes(
        self,
        handles_or_ids: Iterable[ExecutionProcessHandleV1 | str],
        *,
        timeout_per_item: float | None = None,
        overall_timeout: float | None = None,
    ) -> list[ExecutionProcessWaitResultV1]:
        results: list[ExecutionProcessWaitResultV1] = []
        handles = [await self.resolve_process_handle(item) for item in handles_or_ids]
        batch = await self._facade.wait_many(
            [handle.execution_id for handle in handles],
            timeout_per_item=timeout_per_item,
            overall_timeout=overall_timeout,
        )
        for handle in handles:
            snapshot = batch.snapshots[handle.execution_id]
            results.append(
                ExecutionProcessWaitResultV1(
                    handle=handle,
                    status=_to_process_status(snapshot.status),
                    success=snapshot.ok,
                    exit_code=_extract_exit_code(snapshot),
                    timed_out=handle.execution_id in batch.timed_out_execution_ids,
                    error_message=snapshot.error or None,
                    completed_at=_utc_now(),
                )
            )
        return results

    async def close(self, *, cancel_running: bool = True) -> None:
        async with self._log_tasks_lock:
            for task in list(self._process_log_tasks.values()):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._process_log_tasks.clear()
        async with self._handles_lock:
            self._process_handles.clear()
        await self._facade.close(cancel_running=cancel_running)

    async def _register_log_drain(self, handle: ExecutionHandle, log_path: str) -> None:
        """Register a background task to drain process output to log file (async, thread-safe)."""
        execution_id = handle.execution_id
        async with self._log_tasks_lock:
            if execution_id in self._process_log_tasks:
                return
            task = create_task_with_context(
                self._drain_stream_to_log(handle, log_path),
                name=f"execution-broker-log-drain-{execution_id}",
            )
            self._process_log_tasks.set(execution_id, task)

    async def _drain_stream_to_log(self, handle: ExecutionHandle, log_path: str) -> None:
        """Drain process output to log file until the process stream closes.

        Uses per-chunk timeout (max 1 second between chunks) to prevent permanent blocking.
        An optional env-configured wall-clock cap is available for diagnostics tests,
        but production drains for the subprocess lifetime.
        """
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        log_file = open_text_log_append(log_path)
        loop = asyncio.get_running_loop()
        max_seconds = _resolve_log_drain_max_seconds()
        deadline = loop.time() + max_seconds if max_seconds is not None else None
        terminal_idle_started_at: float | None = None
        execution_id = handle.execution_id

        try:
            start_snapshot = handle.snapshot()
            log_file.write(
                "[execution_broker] launched "
                f"execution_id={execution_id} "
                f"pid={start_snapshot.pid} "
                f"status={start_snapshot.status.value}\n"
            )
            args = start_snapshot.metadata.get("args")
            if isinstance(args, list):
                log_file.write(f"[execution_broker] command={_format_args_for_log(str(item) for item in args)}\n")
            log_file.flush()
            # Use iterator approach with per-chunk timeout
            # This ensures we don't block forever on stream()
            stream_iter = handle.stream().__aiter__()
            while True:
                timeout = 1.0
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        _logger.warning(
                            "execution_broker.log_drain.timeout",
                            execution_id=execution_id,
                        )
                        break
                    timeout = min(remaining, 1.0)

                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=timeout,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    snapshot = handle.snapshot()
                    if snapshot.status.terminal:
                        if terminal_idle_started_at is None:
                            terminal_idle_started_at = loop.time()
                        elif loop.time() - terminal_idle_started_at >= _LOG_DRAIN_TERMINAL_IDLE_SECONDS:
                            _logger.warning(
                                "execution_broker.log_drain.terminal_idle_timeout",
                                execution_id=execution_id,
                                status=snapshot.status.value,
                            )
                            break
                    else:
                        terminal_idle_started_at = None
                    continue

                if not chunk.line:
                    continue
                terminal_idle_started_at = None
                log_file.write(chunk.line + "\n")
            log_file.flush()
        except asyncio.CancelledError:
            _logger.warning(
                "execution_broker.log_drain.cancelled",
                execution_id=execution_id,
            )
            raise
        except (RuntimeError, ValueError) as exc:
            _logger.error(
                "execution_broker.log_drain.error",
                execution_id=execution_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        finally:
            with contextlib.suppress(Exception):
                snapshot = await _wait_for_terminal_snapshot(handle)
                log_file.write(
                    "[execution_broker] terminal "
                    f"execution_id={execution_id} "
                    f"pid={snapshot.pid} "
                    f"status={snapshot.status.value} "
                    f"exit_code={_extract_exit_code(snapshot)} "
                    f"error={snapshot.error or ''}\n"
                )
                log_file.flush()
            with contextlib.suppress(Exception):
                log_file.close()
            # Always remove task from registry to prevent memory leak
            async with self._log_tasks_lock:
                self._process_log_tasks.remove(execution_id)

    async def _await_log_drain(self, execution_id: str) -> None:
        """Await and remove a log drain task (async, thread-safe)."""
        task = None
        async with self._log_tasks_lock:
            task = self._process_log_tasks.get(execution_id)
            if task is not None:
                self._process_log_tasks.remove(execution_id)
        if task is None:
            return
        # 任务已从注册表中移除，在锁外等待是安全的
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @staticmethod
    def _normalize_stdin(stdin_input: str | None) -> list[str] | None:
        if stdin_input is None:
            return None
        lines = str(stdin_input).splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return lines


_SHARED_EXECUTION_BROKER_SERVICES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    ExecutionBrokerService,
] = weakref.WeakKeyDictionary()


def get_execution_broker_service(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ExecutionBrokerService:
    target_loop = loop or asyncio.get_running_loop()
    service = _SHARED_EXECUTION_BROKER_SERVICES.get(target_loop)
    if service is None:
        facade = get_shared_execution_facade(loop=target_loop)
        service = ExecutionBrokerService(facade=facade)
        _SHARED_EXECUTION_BROKER_SERVICES[target_loop] = service
    return service


async def reset_execution_broker_service(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    target_loop = loop or asyncio.get_running_loop()
    service = _SHARED_EXECUTION_BROKER_SERVICES.pop(target_loop, None)
    if service is not None:
        await service.close(cancel_running=True)
        return
    await reset_shared_execution_facade(loop=target_loop)


__all__ = [
    "ExecutionBrokerService",
    "get_execution_broker_service",
    "reset_execution_broker_service",
]
