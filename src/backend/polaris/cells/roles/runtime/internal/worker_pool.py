"""
Worker Module - 执行面 (Execution Plane)

Reference: learn-claude-code s11/s12
Process isolation via independent work dirs per Worker

Worker Lifecycle:
    spawn -> WORKING -> IDLE -> WORKING -> ... -> SHUTDOWN

Worker States:
    - idle: Free, waiting for task events
    - working: Executing task
    - shutdown: Graceful shutdown pending

This module provides both sync and async implementations:
- Sync: Worker, WorkerPool (backward compatible)
- Async: AsyncWorker, AsyncWorkerPool (using execution_broker)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import queue
import shlex
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from polaris.kernelone.fs.jsonl.ops import append_jsonl_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _split_worker_command(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        return []
    tokens = shlex.split(raw, posix=(os.name != "nt"))
    if os.name == "nt":
        tokens = [_strip_wrapping_quotes(token) for token in tokens]
    return [str(token).strip() for token in tokens if str(token).strip()]


def _resolve_worker_runtime_path(workspace: Path, rel_path: str) -> Path:
    try:
        return Path(resolve_runtime_path(str(workspace), rel_path))
    except RuntimeError:
        normalized = str(rel_path or "").replace("\\", "/").strip().lstrip("./")
        if normalized == "runtime":
            suffix = Path()
        elif normalized.startswith("runtime/"):
            suffix_parts = [part for part in normalized[len("runtime/") :].split("/") if part]
            suffix = Path(*suffix_parts) if suffix_parts else Path()
        else:
            raise
        return workspace / ".polaris" / "runtime" / suffix


class _TaskRuntimeFinalizationPort(Protocol):
    def complete_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        result_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def fail_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

class TaskRuntimePort(_TaskRuntimeFinalizationPort, Protocol):
    def claim_next_execution(
        self,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        prefer_resumable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

class _LegacyTaskRuntimePort(_TaskRuntimeFinalizationPort, Protocol):
    def list_ready_task_rows(self) -> list[dict[str, Any]]: ...
    def claim_execution(
        self,
        task_id: Any,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

TaskRuntimeLike = TaskRuntimePort | _LegacyTaskRuntimePort


class WorkerState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    SHUTDOWN = "shutdown"


@dataclass
class WorkerConfig:
    """Worker configuration"""

    worker_id: str
    work_dir: Path
    max_idle_time: int = 60
    poll_interval: int = 5
    env: dict = field(default_factory=dict)


@dataclass
class WorkerTask:
    """Task executed by Worker"""

    task_id: int
    command: str
    work_dir: Path
    env: dict = field(default_factory=dict)
    timeout: int = 300
    metadata: dict = field(default_factory=dict)
    session_id: str = ""


@dataclass
class WorkerResult:
    """Task execution result"""

    task_id: int
    worker_id: str
    success: bool
    output: str = ""
    error: str = ""
    duration: float = 0
    metadata: dict = field(default_factory=dict)


def _merge_task_runtime_finalization_result(
    result: WorkerResult,
    finalization: dict[str, Any] | None,
) -> WorkerResult:
    """Apply TaskRuntime finalization failure evidence to a worker result."""

    if not isinstance(finalization, dict) or bool(finalization.get("success")):
        return result

    reason = str(finalization.get("reason") or "task_runtime_finalization_failed").strip()
    if not reason:
        reason = "task_runtime_finalization_failed"
    failure_class = str(finalization.get("failure_class") or reason).strip()
    finalization_projection: dict[str, Any] = {
        "success": False,
        "reason": reason,
    }
    if failure_class:
        finalization_projection["failure_class"] = failure_class
    for key in ("requested_reason", "state_mutation_applied", "execution_event"):
        if key in finalization:
            finalization_projection[key] = finalization[key]

    metadata = dict(result.metadata)
    metadata["task_runtime_finalization"] = finalization_projection

    finalization_error = f"TaskRuntime finalization failed: {reason}"
    if failure_class and failure_class != reason:
        finalization_error = f"{finalization_error} ({failure_class})"
    existing_error = str(result.error or "").strip()
    error = f"{existing_error}; {finalization_error}" if existing_error else finalization_error

    return WorkerResult(
        task_id=result.task_id,
        worker_id=result.worker_id,
        success=False,
        output=result.output,
        error=error,
        duration=result.duration,
        metadata=metadata,
    )


def _row_task_id(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("id") or 0)
    except (TypeError, ValueError):
        return None


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = row.get("metadata")
    return dict(raw_metadata) if isinstance(raw_metadata, dict) else {}


def _session_id_from_claim(claim_result: dict[str, Any]) -> str:
    raw_session = claim_result.get("session")
    if not isinstance(raw_session, dict):
        return ""
    return str(raw_session.get("session_id") or "").strip()


def _worker_timeout(metadata: dict[str, Any]) -> int:
    try:
        return int(metadata.get("timeout") or 300)
    except (TypeError, ValueError):
        return 300


def _worker_task_from_runtime_row(
    row: dict[str, Any],
    *,
    session_id: str,
    work_dir: Path,
) -> WorkerTask | None:
    task_id = _row_task_id(row)
    if task_id is None:
        return None
    metadata = _row_metadata(row)
    return WorkerTask(
        task_id=task_id,
        command=str(metadata.get("command") or ""),
        work_dir=work_dir,
        env=dict(metadata.get("env") or {}),
        timeout=_worker_timeout(metadata),
        metadata=metadata,
        session_id=session_id,
    )

def _claim_result_to_worker_task(
    claim_result: dict[str, Any],
    *,
    work_dir: Path,
    fallback_row: dict[str, Any] | None = None,
) -> WorkerTask | None:
    claim_row = claim_result.get("task")
    runtime_row = claim_row if isinstance(claim_row, dict) else fallback_row
    if runtime_row is None:
        return None
    return _worker_task_from_runtime_row(
        runtime_row,
        session_id=_session_id_from_claim(claim_result),
        work_dir=work_dir,
    )

def _has_legacy_ready_task(task_runtime: TaskRuntimeLike | None) -> bool:
    if task_runtime is None:
        return False
    list_ready_task_rows = getattr(task_runtime, "list_ready_task_rows", None)
    if not callable(list_ready_task_rows):
        return False
    return bool(list_ready_task_rows())

def _claim_next_runtime_task(
    task_runtime: TaskRuntimeLike,
    *,
    worker_id: str,
    role_id: str,
    selection_source: str,
    work_dir: Path,
) -> tuple[bool, WorkerTask | None]:
    claim_next_execution = getattr(task_runtime, "claim_next_execution", None)
    if not callable(claim_next_execution):
        return False, None

    claim_result = claim_next_execution(
        worker_id=worker_id,
        role_id=role_id,
        lease_ttl_seconds=120,
        selection_source=selection_source,
        prefer_resumable=True,
    )
    if not isinstance(claim_result, dict):
        logger.warning(
            "TaskRuntime claim_next_execution returned non-dict result for worker_id=%s role_id=%s",
            worker_id,
            role_id,
        )
        return True, None

    if not bool(claim_result.get("success")):
        return True, None

    task = _claim_result_to_worker_task(claim_result, work_dir=work_dir)
    if task is None:
        logger.warning(
            "TaskRuntime claim_next_execution returned success without a usable task row for worker_id=%s role_id=%s",
            worker_id,
            role_id,
        )
    return True, task

def _claim_legacy_ready_task(
    task_runtime: TaskRuntimeLike,
    *,
    worker_id: str,
    role_id: str,
    selection_source: str,
    work_dir: Path,
) -> WorkerTask | None:
    list_ready_task_rows = getattr(task_runtime, "list_ready_task_rows", None)
    claim_execution = getattr(task_runtime, "claim_execution", None)
    if not callable(list_ready_task_rows) or not callable(claim_execution):
        return None

    for row in list_ready_task_rows():
        task_id = _row_task_id(row)
        if task_id is None:
            continue
        claim_result = claim_execution(
            task_id,
            worker_id=worker_id,
            role_id=role_id,
            selection_source=selection_source,
        )
        if not bool(claim_result.get("success")):
            continue
        task = _claim_result_to_worker_task(claim_result, work_dir=work_dir, fallback_row=row)
        if task is not None:
            return task
    return None

def _claim_ready_runtime_task(
    task_runtime: TaskRuntimeLike | None,
    *,
    worker_id: str,
    role_id: str,
    selection_source: str,
    work_dir: Path,
) -> WorkerTask | None:
    if task_runtime is None:
        return None

    atomic_attempted, task = _claim_next_runtime_task(
        task_runtime,
        worker_id=worker_id,
        role_id=role_id,
        selection_source=selection_source,
        work_dir=work_dir,
    )
    if atomic_attempted:
        return task

    return _claim_legacy_ready_task(
        task_runtime,
        worker_id=worker_id,
        role_id=role_id,
        selection_source=selection_source,
        work_dir=work_dir,
    )


# =============================================================================
# Sync Worker (Backward Compatible)
# =============================================================================


class Worker:
    """
    Sync Worker - Process isolation execution unit

    Each Worker:
    - Has independent work directory
    - Runs in independent thread
    - Wakes from direct submissions or task-runtime ready events
    - Can send messages back to main process
    """

    def __init__(
        self,
        config: WorkerConfig,
        task_runtime: TaskRuntimeLike | None = None,
        message_callback: Callable | None = None,
    ) -> None:
        self.config = config
        self.task_runtime = task_runtime
        self.message_callback = message_callback
        self.state = WorkerState.IDLE

        self._thread: threading.Thread | None = None
        self._running = False
        self._current_task: WorkerTask | None = None
        self._result_queue: queue.Queue[WorkerTask] = queue.Queue(maxsize=200)  # 有界队列防止内存泄漏
        self._command_executor = CommandExecutionService(self.config.work_dir)
        self._state_lock = threading.Lock()
        self._wake_condition = threading.Condition()
        self._ready_unsubscribe: Callable[[], None] | None = None

    def start(self) -> None:
        """Start Worker"""
        if self._running:
            return

        self._running = True
        self._ready_unsubscribe = self._register_ready_listener()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, graceful: bool = True) -> None:
        """Stop Worker"""
        with self._state_lock:
            if not self._running:
                return
            self.state = WorkerState.SHUTDOWN

        self._wake()

        if graceful:
            self._wait_for_current_task(timeout=30.0)

        with self._state_lock:
            self._running = False

        self._wake()
        if self._thread:
            self._thread.join(timeout=5)
        if self._ready_unsubscribe is not None:
            self._ready_unsubscribe()
            self._ready_unsubscribe = None

    def _wait_for_current_task(self, timeout: float) -> bool:
        """Wait for current task to complete."""
        wait_timeout = max(0.0, float(timeout))
        with self._wake_condition:
            return self._wake_condition.wait_for(lambda: self._current_task is None, timeout=wait_timeout)

    def submit_task(self, task: WorkerTask) -> bool:
        """Submit task to Worker"""
        if self.state == WorkerState.SHUTDOWN:
            return False

        self._result_queue.put(task)
        self._wake()
        return True

    def _run_loop(self) -> None:
        """Main loop: wait for work events, execute, repeat."""
        while self._running:
            if self.state == WorkerState.SHUTDOWN:
                break

            task = self._next_task()
            if task is None:
                break
            self._execute_worker_task(task)

    def _wake(self) -> None:
        with self._wake_condition:
            self._wake_condition.notify_all()

    def _register_ready_listener(self) -> Callable[[], None] | None:
        if self.task_runtime is None:
            return None
        add_listener = getattr(self.task_runtime, "add_ready_listener", None)
        if not callable(add_listener):
            return None
        return add_listener(self._wake)

    def _has_ready_task(self) -> bool:
        return _has_legacy_ready_task(self.task_runtime)

    def _claim_ready_task(self) -> WorkerTask | None:
        return _claim_ready_runtime_task(
            self.task_runtime,
            worker_id=self.config.worker_id,
            role_id="worker_pool",
            selection_source="roles.runtime.worker_pool",
            work_dir=self.config.work_dir,
        )

    def _next_task(self) -> WorkerTask | None:
        while True:
            with self._state_lock:
                if not self._running or self.state == WorkerState.SHUTDOWN:
                    return None
                self.state = WorkerState.IDLE

            try:
                return self._result_queue.get_nowait()
            except queue.Empty:
                pass

            ready_task = self._claim_ready_task()
            if ready_task is not None:
                return ready_task

            with self._wake_condition:
                self._wake_condition.wait_for(
                    lambda: (
                        not self._running
                        or self.state == WorkerState.SHUTDOWN
                        or not self._result_queue.empty()
                        or self._has_ready_task()
                    )
                )

    def _execute_worker_task(self, task: WorkerTask) -> None:
        with self._state_lock:
            self.state = WorkerState.WORKING
            self._current_task = task
        self._wake()

        result = self._execute_task(task)

        with self._state_lock:
            self._current_task = None

        if self.task_runtime and task.session_id:
            if result.success:
                finalization = self.task_runtime.complete_execution(
                    task.task_id,
                    session_id=task.session_id,
                    result_summary=result.output,
                    metadata={"worker_id": self.config.worker_id},
                )
            else:
                finalization = self.task_runtime.fail_execution(
                    task.task_id,
                    session_id=task.session_id,
                    error=result.error,
                    metadata={"worker_id": self.config.worker_id},
                )
            result = _merge_task_runtime_finalization_result(result, finalization)

        if self.message_callback:
            self.message_callback(result)

        self._wake()

    def _execute_task(self, task: WorkerTask) -> WorkerResult:
        """Execute task (sync version using CommandExecutionService)"""
        start_time = time.time()

        env = os.environ.copy()
        env.update(task.env)
        env["KERNELONE_WORKER_ID"] = self.config.worker_id
        env["KERNELONE_TASK_ID"] = str(task.task_id)

        try:
            tokens = _split_worker_command(task.command)
            if not tokens:
                return WorkerResult(
                    task_id=task.task_id,
                    worker_id=self.config.worker_id,
                    success=False,
                    error="Empty command",
                    duration=time.time() - start_time,
                    metadata=task.metadata,
                )
            from polaris.kernelone.process.command_executor import CommandRequest

            request = CommandRequest(
                executable=tokens[0],
                args=tokens[1:],
                cwd=str(task.work_dir),
                timeout_seconds=max(1, int(task.timeout or 300)),
            )
            result = self._command_executor.run(request, env_overrides=env)
            duration = time.time() - start_time
            output = f"{result.get('stdout', '')}{result.get('stderr', '')}".strip()
            if result.get("timed_out"):
                return WorkerResult(
                    task_id=task.task_id,
                    worker_id=self.config.worker_id,
                    success=False,
                    output=output[:50000],
                    error=str(result.get("error") or f"Timeout after {task.timeout}s"),
                    duration=duration,
                    metadata=task.metadata,
                )
            return WorkerResult(
                task_id=task.task_id,
                worker_id=self.config.worker_id,
                success=bool(result.get("ok")),
                output=output[:50000],
                error="" if result.get("ok") else f"Exit code: {int(result.get('returncode', -1))}",
                duration=duration,
                metadata=task.metadata,
            )
        except (RuntimeError, ValueError) as exc:
            return WorkerResult(
                task_id=task.task_id,
                worker_id=self.config.worker_id,
                success=False,
                error=str(exc),
                duration=time.time() - start_time,
            )


# =============================================================================
# Async Worker (using execution_broker)
# =============================================================================


@dataclass
class AsyncWorkerConfig:
    """Async Worker configuration"""

    worker_id: str
    work_dir: Path
    max_idle_time: int = 60
    poll_interval: float = 5.0
    env: dict = field(default_factory=dict)


class AsyncWorker:
    """
    Async Worker - Uses execution_broker for process execution.

    Key differences from sync Worker:
    - Uses asyncio tasks instead of threading
    - Uses asyncio.Queue instead of queue.Queue
    - Uses execution_broker.launch_process() instead of CommandExecutionService.run()
    - Adds cell="roles" metadata to all executions
    """

    def __init__(
        self,
        config: AsyncWorkerConfig,
        task_runtime: TaskRuntimeLike | None = None,
        message_callback: Callable[[WorkerResult], Any] | None = None,
    ) -> None:
        self.config = config
        self.task_runtime = task_runtime
        self.message_callback = message_callback
        self.state = WorkerState.IDLE

        self._task: asyncio.Task | None = None
        self._running = False
        self._current_task: WorkerTask | None = None
        self._result_queue: asyncio.Queue[WorkerTask] = asyncio.Queue(maxsize=200)
        self._state_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready_unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        """Start Async Worker"""
        if self._running:
            return

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._ready_unsubscribe = self._register_ready_listener()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self, graceful: bool = True) -> None:
        """Stop Async Worker"""
        async with self._state_lock:
            if not self._running:
                return
            self.state = WorkerState.SHUTDOWN

        self._wake()

        if graceful:
            await self._wait_for_current_task(timeout=30.0)

        async with self._state_lock:
            self._running = False

        self._wake()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        if self._ready_unsubscribe is not None:
            self._ready_unsubscribe()
            self._ready_unsubscribe = None
        self._loop = None

    async def _wait_for_current_task(self, timeout: float) -> bool:
        """Wait for current task to complete."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            async with self._state_lock:
                if self._current_task is None:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return False

    async def submit_task(self, task: WorkerTask) -> bool:
        """Submit task to Async Worker"""
        if self.state == WorkerState.SHUTDOWN:
            return False

        await self._result_queue.put(task)
        self._wake()
        return True

    async def _run_loop(self) -> None:
        """Main loop: wait for work events, execute, repeat."""
        while self._running:
            if self.state == WorkerState.SHUTDOWN:
                break

            task = await self._next_task()
            if task is None:
                break
            await self._execute_worker_task(task)

    def _wake(self) -> None:
        self._wake_event.set()

    def _wake_threadsafe(self) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._wake_event.set)

    def _register_ready_listener(self) -> Callable[[], None] | None:
        if self.task_runtime is None:
            return None
        add_listener = getattr(self.task_runtime, "add_ready_listener", None)
        if not callable(add_listener):
            return None
        return add_listener(self._wake_threadsafe)

    def _has_ready_task(self) -> bool:
        return _has_legacy_ready_task(self.task_runtime)

    def _claim_ready_task(self) -> WorkerTask | None:
        return _claim_ready_runtime_task(
            self.task_runtime,
            worker_id=self.config.worker_id,
            role_id="async_worker_pool",
            selection_source="roles.runtime.async_worker_pool",
            work_dir=self.config.work_dir,
        )

    async def _next_task(self) -> WorkerTask | None:
        while True:
            async with self._state_lock:
                if not self._running or self.state == WorkerState.SHUTDOWN:
                    return None
                self.state = WorkerState.IDLE

            try:
                return self._result_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            ready_task = self._claim_ready_task()
            if ready_task is not None:
                return ready_task

            self._wake_event.clear()
            if not self._running or self.state == WorkerState.SHUTDOWN:
                return None
            if not self._result_queue.empty() or self._has_ready_task():
                self._wake_event.set()
                continue
            await self._wake_event.wait()

    async def _execute_worker_task(self, task: WorkerTask) -> None:
        async with self._state_lock:
            self.state = WorkerState.WORKING
            self._current_task = task
        self._wake()

        result = await self._execute_task_async(task)

        async with self._state_lock:
            self._current_task = None

        if self.task_runtime and task.session_id:
            if result.success:
                finalization = self.task_runtime.complete_execution(
                    task.task_id,
                    session_id=task.session_id,
                    result_summary=result.output,
                    metadata={"worker_id": self.config.worker_id},
                )
            else:
                finalization = self.task_runtime.fail_execution(
                    task.task_id,
                    session_id=task.session_id,
                    error=result.error,
                    metadata={"worker_id": self.config.worker_id},
                )
            result = _merge_task_runtime_finalization_result(result, finalization)

        if self.message_callback:
            self.message_callback(result)

        self._wake()

    async def _execute_task_async(self, task: WorkerTask) -> WorkerResult:
        """Execute task using execution_broker.

        This replaces CommandExecutionService.run() with:
        1. broker.launch_process() - launch subprocess
        2. broker.wait_process() - wait for completion
        """
        from polaris.cells.runtime.execution_broker.public.contracts import (
            ExecutionProcessStatusV1,
            LaunchExecutionProcessCommandV1,
        )
        from polaris.cells.runtime.execution_broker.public.service import (
            get_execution_broker_service,
        )

        start_time = time.time()

        # Build metadata with cell="roles" tag
        metadata = {
            **dict(task.metadata),
            "cell": "roles",
            "workspace": str(task.work_dir),
            "task_id": str(task.task_id),
            "worker_id": self.config.worker_id,
        }

        # Build execution environment with UTF-8 settings
        env = dict(os.environ)
        env.update(task.env)
        env["KERNELONE_WORKER_ID"] = self.config.worker_id
        env["KERNELONE_TASK_ID"] = str(task.task_id)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            tokens = _split_worker_command(task.command)
            if not tokens:
                return WorkerResult(
                    task_id=task.task_id,
                    worker_id=self.config.worker_id,
                    success=False,
                    error="Empty command",
                    duration=time.time() - start_time,
                    metadata=metadata,
                )

            broker = get_execution_broker_service()
            command = LaunchExecutionProcessCommandV1(
                name=f"worker-{self.config.worker_id}-{task.task_id}",
                args=tuple(tokens),
                workspace=str(task.work_dir),
                timeout_seconds=max(1.0, float(task.timeout or 300)),
                env=dict(env),
                metadata=metadata,
            )

            launch_result = await broker.launch_process(command)
            if not launch_result.success:
                return WorkerResult(
                    task_id=task.task_id,
                    worker_id=self.config.worker_id,
                    success=False,
                    error=f"Launch failed: {launch_result.error_message}",
                    duration=time.time() - start_time,
                    metadata=metadata,
                )

            wait_result = await broker.wait_process(
                launch_result.handle,  # type: ignore[arg-type]
                timeout_seconds=max(1.0, float(task.timeout or 300)),
            )

            duration = time.time() - start_time
            output = ""

            # Try to read output from log file if available
            # Use asyncio.to_thread to avoid blocking the event loop
            if launch_result.handle and launch_result.handle.log_path:
                log_path = Path(launch_result.handle.log_path)
                if log_path.exists():
                    try:
                        # Read in thread to avoid blocking event loop
                        def _read_log(p: Path = log_path) -> str:
                            return p.read_text(encoding="utf-8", errors="replace")

                        output = await asyncio.to_thread(_read_log)
                    except (RuntimeError, ValueError) as e:
                        logger.debug("Failed to read log file %s: %s", log_path, e)

            # Determine success status
            success = wait_result.success
            if wait_result.status in (ExecutionProcessStatusV1.TIMED_OUT, ExecutionProcessStatusV1.CANCELLED):
                success = False

            error_msg = ""
            if not success:
                exit_code = wait_result.exit_code
                exit_code_text = f"Exit code: {exit_code}" if exit_code is not None else "Exit code: unknown"
                if wait_result.timed_out:
                    error_msg = f"Timeout after {task.timeout}s"
                elif wait_result.error_message:
                    error_msg = str(wait_result.error_message)
                    if exit_code is not None:
                        error_msg = f"{error_msg} ({exit_code_text})"
                else:
                    error_msg = exit_code_text

            return WorkerResult(
                task_id=task.task_id,
                worker_id=self.config.worker_id,
                success=success,
                output=output[:50000] if output else "",
                error=error_msg,
                duration=duration,
                metadata=metadata,
            )

        except asyncio.TimeoutError:
            return WorkerResult(
                task_id=task.task_id,
                worker_id=self.config.worker_id,
                success=False,
                error=f"Timeout after {task.timeout}s",
                duration=time.time() - start_time,
                metadata=metadata,
            )
        except (RuntimeError, ValueError) as exc:
            return WorkerResult(
                task_id=task.task_id,
                worker_id=self.config.worker_id,
                success=False,
                error=str(exc),
                duration=time.time() - start_time,
                metadata=metadata,
            )


# =============================================================================
# Sync WorkerPool (Backward Compatible)
# =============================================================================


class WorkerPool:
    """
    Worker Pool Manager (sync version)

    Manages multiple Worker instances with:
    - Dynamic scaling
    - Task distribution
    - Status monitoring
    """

    def __init__(
        self,
        work_base_dir: Path,
        task_runtime: TaskRuntimeLike | None = None,
        max_workers: int = 4,
    ) -> None:
        self.work_base_dir = work_base_dir
        self.task_runtime = task_runtime
        self.max_workers = max_workers

        self._workers: dict[str, Worker] = {}
        self._lock = threading.RLock()
        self._event_log_path = _resolve_worker_runtime_path(work_base_dir, "runtime/logs/worker_events.jsonl")
        self._event_log_path.parent.mkdir(parents=True, exist_ok=True)

    def spawn_worker(self, worker_id: str | None = None) -> str:
        """Create a new Worker"""
        with self._lock:
            if len(self._workers) >= self.max_workers:
                raise RuntimeError(f"Worker pool full (max: {self.max_workers})")

            if not worker_id:
                worker_id = f"worker-{len(self._workers) + 1}"

            work_dir = _resolve_worker_runtime_path(self.work_base_dir, f"runtime/workers/{worker_id}")
            work_dir.mkdir(parents=True, exist_ok=True)

            config = WorkerConfig(
                worker_id=worker_id,
                work_dir=work_dir,
            )

            worker = Worker(config, self.task_runtime, self._on_worker_message)
            worker.start()

            self._workers[worker_id] = worker
            self._emit_event("worker_spawned", {"worker_id": worker_id, "work_dir": str(work_dir)})

            return worker_id

    def shutdown_worker(self, worker_id: str, graceful: bool = True) -> bool:
        """Shutdown Worker"""
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                return False

            worker.stop(graceful)
            del self._workers[worker_id]
            self._emit_event("worker_shutdown", {"worker_id": worker_id, "graceful": graceful})
            return True

    def submit_task(self, task: WorkerTask, worker_id: str | None = None) -> bool:
        """Submit task to Worker"""
        with self._lock:
            if worker_id:
                worker = self._workers.get(worker_id)
                if worker:
                    return worker.submit_task(task)
                return False

            available = [w for w in self._workers.values() if w.state == WorkerState.IDLE]
            if not available:
                available = list(self._workers.values())

            if available:
                return available[0].submit_task(task)
            return False

    def get_status(self) -> dict:
        """Get Worker pool status"""
        with self._lock:
            return {
                "total_workers": len(self._workers),
                "idle": sum(1 for w in self._workers.values() if w.state == WorkerState.IDLE),
                "working": sum(1 for w in self._workers.values() if w.state == WorkerState.WORKING),
                "workers": {
                    wid: {"state": w.state.value, "current_task": w._current_task.task_id if w._current_task else None}
                    for wid, w in self._workers.items()
                },
            }

    def shutdown_all(self, graceful: bool = True) -> None:
        """Shutdown all Workers"""
        with self._lock:
            for worker_id in list(self._workers.keys()):
                self.shutdown_worker(worker_id, graceful)

    def _on_worker_message(self, result: WorkerResult) -> None:
        """Handle Worker message"""
        self._emit_event(
            "task_completed",
            {
                "worker_id": result.worker_id,
                "task_id": result.task_id,
                "success": result.success,
                "duration": result.duration,
            },
        )

    def _emit_event(self, event: str, data: dict) -> None:
        """Record event (UTF-8 safe)"""
        append_jsonl_atomic(
            str(self._event_log_path),
            {
                "event": event,
                "timestamp": time.time(),
                "data": data,
            },
        )


# =============================================================================
# Async WorkerPool (using execution_broker)
# =============================================================================


class AsyncWorkerPool:
    """
    Async Worker Pool Manager using execution_broker.

    Key differences from sync WorkerPool:
    - Uses asyncio for all operations
    - Workers are AsyncWorker instances
    - All metadata includes cell="roles" tag
    """

    def __init__(
        self,
        work_base_dir: Path,
        task_runtime: TaskRuntimeLike | None = None,
        max_workers: int = 4,
    ) -> None:
        self.work_base_dir = work_base_dir
        self.task_runtime = task_runtime
        self.max_workers = max_workers

        self._workers: dict[str, AsyncWorker] = {}
        self._lock = asyncio.Lock()
        self._event_log_path = _resolve_worker_runtime_path(work_base_dir, "runtime/logs/worker_events.jsonl")
        self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._started = False

    async def _ensure_started(self) -> None:
        """Ensure all workers are started."""
        if self._started:
            return
        self._started = True
        for worker in self._workers.values():
            await worker.start()

    async def spawn_worker(self, worker_id: str | None = None) -> str:
        """Create a new AsyncWorker"""
        async with self._lock:
            if len(self._workers) >= self.max_workers:
                raise RuntimeError(f"Worker pool full (max: {self.max_workers})")

            if not worker_id:
                worker_id = f"worker-{len(self._workers) + 1}"

            work_dir = _resolve_worker_runtime_path(self.work_base_dir, f"runtime/workers/{worker_id}")
            work_dir.mkdir(parents=True, exist_ok=True)

            config = AsyncWorkerConfig(
                worker_id=worker_id,
                work_dir=work_dir,
            )

            worker = AsyncWorker(config, self.task_runtime, self._on_worker_message)
            await worker.start()

            self._workers[worker_id] = worker
            self._emit_event(
                "worker_spawned",
                {
                    "worker_id": worker_id,
                    "work_dir": str(work_dir),
                    "mode": "async",
                },
            )

            return worker_id

    async def shutdown_worker(self, worker_id: str, graceful: bool = True) -> bool:
        """Shutdown AsyncWorker"""
        async with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                return False

            await worker.stop(graceful)
            del self._workers[worker_id]
            self._emit_event(
                "worker_shutdown",
                {
                    "worker_id": worker_id,
                    "graceful": graceful,
                    "mode": "async",
                },
            )
            return True

    async def submit_task(self, task: WorkerTask, worker_id: str | None = None) -> bool:
        """Submit task to AsyncWorker"""
        await self._ensure_started()

        if worker_id:
            worker = self._workers.get(worker_id)
            if worker:
                return await worker.submit_task(task)
            return False

        async with self._lock:
            available = [w for w in self._workers.values() if w.state == WorkerState.IDLE]
            if not available:
                available = list(self._workers.values())

            if available:
                return await available[0].submit_task(task)
            return False

    async def get_status(self) -> dict:
        """Get AsyncWorker pool status"""
        async with self._lock:
            return {
                "total_workers": len(self._workers),
                "idle": sum(1 for w in self._workers.values() if w.state == WorkerState.IDLE),
                "working": sum(1 for w in self._workers.values() if w.state == WorkerState.WORKING),
                "workers": {
                    wid: {"state": w.state.value, "current_task": w._current_task.task_id if w._current_task else None}
                    for wid, w in self._workers.items()
                },
            }

    async def shutdown_all(self, graceful: bool = True) -> None:
        """Shutdown all AsyncWorkers"""
        async with self._lock:
            workers = list(self._workers.items())
            self._workers.clear()
            self._started = False

        for worker_id, worker in workers:
            await worker.stop(graceful)
            self._emit_event(
                "worker_shutdown",
                {
                    "worker_id": worker_id,
                    "graceful": graceful,
                    "mode": "async",
                },
            )

    def _on_worker_message(self, result: WorkerResult) -> None:
        """Handle AsyncWorker message"""
        self._emit_event(
            "task_completed",
            {
                "worker_id": result.worker_id,
                "task_id": result.task_id,
                "success": result.success,
                "duration": result.duration,
                "mode": "async",
            },
        )

    def _emit_event(self, event: str, data: dict) -> None:
        """Record event (UTF-8 safe)"""
        append_jsonl_atomic(
            str(self._event_log_path),
            {
                "event": event,
                "timestamp": time.time(),
                "data": data,
            },
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_worker_pool(
    work_base_dir: Path,
    task_runtime: TaskRuntimeLike | None = None,
    max_workers: int = 4,
) -> WorkerPool:
    """Create sync Worker pool."""
    return WorkerPool(work_base_dir, task_runtime, max_workers)


async def create_async_worker_pool(
    work_base_dir: Path,
    task_runtime: TaskRuntimeLike | None = None,
    max_workers: int = 4,
) -> AsyncWorkerPool:
    """Create async Worker pool using execution_broker"""
    return AsyncWorkerPool(work_base_dir, task_runtime, max_workers)
