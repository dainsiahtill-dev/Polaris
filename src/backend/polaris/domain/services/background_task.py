"""Unified background task service.

Consolidates the 3 duplicate BackgroundManager implementations:
- src/backend/core/polaris_loop/background_manager_v2.py
- src/backend/scripts/director/background_manager.py
- src/backend/scripts/director/background_manager_v2.py

Provides a clean, testable, and extensible architecture using:
- Dependency injection for executors and storage
- Protocol-based interfaces for flexibility
- Async/await for concurrency control
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Protocol

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

from .security_service import get_security_service
from .tool_timeout_service import ToolTier, ToolTimeoutService


class TaskState(Enum):
    """Background task lifecycle states."""

    QUEUED = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    TIMEOUT = auto()
    CANCELLED = auto()


@dataclass
class ExecutionResult:
    """Result of executing a background task."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
        }


@dataclass
class BackgroundTask:
    """A background task entity with timeout tier support."""

    command: str
    timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    cwd: str = "."
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    state: TaskState = TaskState.QUEUED
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: ExecutionResult | None = None
    queue_position: int = 0
    tier: ToolTier = ToolTier.BACKGROUND  # Default to background tier

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "timeout": self.timeout,
            "state": self.state.name,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": (
                {
                    "success": self.result.success,
                    "exit_code": self.result.exit_code,
                    "stdout": self.result.stdout,
                    "stderr": self.result.stderr,
                    "duration_ms": self.result.duration_ms,
                }
                if self.result
                else None
            ),
            "queue_position": self.queue_position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackgroundTask:
        """Create from dictionary."""
        result_data = data.get("result")
        result = None
        if result_data:
            result = ExecutionResult(
                success=result_data["success"],
                exit_code=result_data["exit_code"],
                stdout=result_data["stdout"],
                stderr=result_data["stderr"],
                duration_ms=result_data["duration_ms"],
            )

        return cls(
            id=data["id"],
            command=data["command"],
            cwd=data.get("cwd", "."),
            timeout=data.get("timeout", 300),
            state=TaskState[data.get("state", "QUEUED")],
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=(datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None),
            completed_at=(datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None),
            result=result,
            queue_position=data.get("queue_position", 0),
        )


class TaskStorage(Protocol):
    """Protocol for task storage implementations."""

    def save(self, task: BackgroundTask) -> None:
        """Persist a task."""
        ...

    def get(self, task_id: str) -> BackgroundTask | None:
        """Retrieve a task by ID."""
        ...

    def list_all(self) -> list[BackgroundTask]:
        """List all tasks."""
        ...

    def update(self, task: BackgroundTask) -> None:
        """Update an existing task."""
        ...

    def delete(self, task_id: str) -> bool:
        """Delete a task."""
        ...


class TaskExecutor(Protocol):
    """Protocol for task execution implementations."""

    async def execute(
        self,
        command: str,
        cwd: str,
        timeout: int,
        tier: ToolTier = ToolTier.BACKGROUND,
    ) -> ExecutionResult:
        """Execute a command and return the result."""
        ...


class SubprocessExecutor:
    """Default task executor using subprocess with security and timeout management."""

    def __init__(self, workspace: str = ".", timeout_service: ToolTimeoutService | None = None) -> None:
        """Initialize with workspace for security checks.

        Args:
            workspace: Workspace directory for path sandboxing
            timeout_service: Optional ToolTimeoutService for tiered timeouts
        """
        self._security = get_security_service(workspace)
        self._timeout_service = timeout_service or ToolTimeoutService()

    def get_timeout(self, tier: ToolTier | str, requested: int | None = None) -> int:
        """Get validated timeout using ToolTimeoutService.

        Args:
            tier: Tool tier (foreground, background, critical, fast)
            requested: Requested timeout in seconds

        Returns:
            Validated timeout in seconds
        """
        return self._timeout_service.get_timeout(tier, requested)

    async def execute(
        self,
        command: str,
        cwd: str,
        timeout: int,
        tier: ToolTier = ToolTier.BACKGROUND,
    ) -> ExecutionResult:
        """Execute a command using subprocess with security validation."""
        import time

        start_time = time.time()

        # Security check
        check = self._security.is_command_safe(command)
        if not check.is_safe:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Security check failed: {check.reason}",
                duration_ms=duration_ms,
            )

        # Validate timeout using ToolTimeoutService
        validated_timeout = self._timeout_service.get_timeout(tier, timeout)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=validated_timeout)
                duration_ms = int((time.time() - start_time) * 1000)

                return ExecutionResult(
                    success=process.returncode == 0,
                    exit_code=process.returncode or 0,
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    duration_ms=duration_ms,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                duration_ms = int((time.time() - start_time) * 1000)

                return ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stdout="",
                    stderr=f"Task timed out after {validated_timeout} seconds (tier: {tier.value})",
                    duration_ms=duration_ms,
                )

        except PermissionError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Permission denied: {e}",
                duration_ms=duration_ms,
            )
        except FileNotFoundError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Command not found: {e}",
                duration_ms=duration_ms,
            )
        except OSError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"OS error: {e}",
                duration_ms=duration_ms,
            )
        except (RuntimeError, ValueError) as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Unexpected error: {e}",
                duration_ms=duration_ms,
            )


class FileTaskStorage:
    """File-based task storage implementation."""

    def __init__(self, storage_adapter: Any) -> None:
        """Initialize with a storage adapter.

        Args:
            storage_adapter: An object providing path resolution methods
        """
        self._adapter = storage_adapter
        self._state_file = storage_adapter.resolve_path("runtime/state/background_tasks.json")
        self._events_file = storage_adapter.resolve_path("runtime/events/background_tasks.jsonl")
        self._lock = threading.RLock()

    def _load_state(self) -> dict[str, Any]:
        """Load state from disk."""
        data = self._adapter.read_json(self._state_file)
        if not data:
            return {"tasks": {}, "schema_version": 1}
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        """Save state to disk."""
        state["updated_at"] = datetime.now().isoformat()
        self._adapter.ensure_dir(self._state_file)
        self._adapter.write_json(self._state_file, state)

    def _append_event(self, event_type: str, task: BackgroundTask) -> None:
        """Append an event to the event log."""
        event = {
            "type": event_type,
            "task_id": task.id,
            "state": task.state.name,
            "timestamp": datetime.now().isoformat(),
        }
        self._adapter.ensure_dir(self._events_file)
        self._adapter.append_jsonl(self._events_file, event)

    def save(self, task: BackgroundTask) -> None:
        """Persist a task."""
        with self._lock:
            state = self._load_state()
            state["tasks"][task.id] = task.to_dict()
            self._save_state(state)
            self._append_event("created", task)

    def get(self, task_id: str) -> BackgroundTask | None:
        """Retrieve a task by ID."""
        with self._lock:
            state = self._load_state()
            task_data = state["tasks"].get(task_id)
            if task_data:
                return BackgroundTask.from_dict(task_data)
            return None

    def list_all(self) -> list[BackgroundTask]:
        """List all tasks."""
        with self._lock:
            state = self._load_state()
            return [BackgroundTask.from_dict(t) for t in state["tasks"].values()]

    def update(self, task: BackgroundTask) -> None:
        """Update an existing task."""
        with self._lock:
            state = self._load_state()
            if task.id in state["tasks"]:
                state["tasks"][task.id] = task.to_dict()
                self._save_state(state)
                self._append_event("updated", task)

    def delete(self, task_id: str) -> bool:
        """Delete a task."""
        with self._lock:
            state = self._load_state()
            if task_id in state["tasks"]:
                del state["tasks"][task_id]
                self._save_state(state)
                return True
            return False


# Type alias for task runner (async callable with no args)
_TaskRunner = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]


def _default_task_runner(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Default task runner: schedule on the running event loop.

    Uses ``asyncio.create_task`` so the task is observable and cancellable.
    If no event loop is running, logs a warning and returns a detached task.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — create a new task in a fresh loop (detached mode).
        # This path should be avoided in production; prefer scheduling in a loop.
        import logging

        _logger = logging.getLogger(__name__)
        _logger.warning(
            "background_task: no running event loop; task will be scheduled "
            "in a new loop (detached mode). Use an injected task_runner for "
            "production workloads."
        )
        # Create a new loop and run the coroutine
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
        # Return a dummy completed task
        dummy_task = asyncio.create_task(asyncio.sleep(0))
        return dummy_task

    return loop.create_task(coro)


_logger = logging.getLogger(__name__)


class BackgroundTaskService:
    """Unified background task service.

    This service consolidates the 3 duplicate BackgroundManager implementations
    into a single, clean, testable service.

    Usage:
        # With default implementations
        storage = get_storage_adapter(workspace)
        service = BackgroundTaskService.with_defaults(storage)

        # With custom implementations
        service = BackgroundTaskService(
            storage=custom_storage,
            executor=custom_executor,
            max_concurrent=4,
            task_runner=custom_task_runner,
        )

        # Submit a task
        task_id = await service.submit(BackgroundTask(command="echo hello"))

        # Get task status
        task = service.get_task(task_id)
    """

    def __init__(
        self,
        storage: TaskStorage,
        executor: TaskExecutor,
        max_concurrent: int = 2,
        *,
        task_runner: _TaskRunner | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            storage: Task storage backend.
            executor: Task execution backend.
            max_concurrent: Maximum concurrent tasks.
            task_runner: Async task scheduler.  Defaults to ``asyncio.create_task``
                         on the running loop.  Inject for test isolation or when
                         KernelOne trace context propagation is required.
        """
        self._storage = storage
        self._executor = executor
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running_tasks: dict[str, asyncio.Task[None] | None] = {}
        self._lock = asyncio.Lock()
        self._task_runner: _TaskRunner = task_runner or _default_task_runner

    @classmethod
    def with_defaults(cls, storage_adapter: Any, max_concurrent: int = 2) -> BackgroundTaskService:
        """Create a service with default implementations.

        Args:
            storage_adapter: Storage adapter for path resolution
            max_concurrent: Maximum concurrent tasks

        Returns:
            Configured BackgroundTaskService
        """
        storage = FileTaskStorage(storage_adapter)
        # Get workspace from adapter if available, otherwise use current directory
        workspace = getattr(storage_adapter, "workspace", ".")
        executor = SubprocessExecutor(workspace)
        return cls(storage=storage, executor=executor, max_concurrent=max_concurrent)

    async def submit(self, task: BackgroundTask) -> str:
        """Submit a background task.

        The task will be queued and executed when a slot is available.

        Args:
            task: The task to execute

        Returns:
            Task ID
        """
        # Save initial state
        task.state = TaskState.QUEUED
        self._storage.save(task)

        # Start execution in background using the injected task runner.
        # Support both sync (default) and async (test) runners.
        if inspect.iscoroutinefunction(self._task_runner):
            await self._task_runner(self._execute_task(task))
        else:
            self._task_runner(self._execute_task(task))

        return task.id

    async def _execute_task(self, task: BackgroundTask) -> None:
        """Execute a task with concurrency control."""
        async with self._lock:
            current = asyncio.current_task()
            if current is not None:
                self._running_tasks[task.id] = current

        try:
            async with self._semaphore:
                # Update state to running
                task.state = TaskState.RUNNING
                task.started_at = datetime.now()
                self._storage.update(task)

                # Execute the task with tier-based timeout
                result = await self._executor.execute(task.command, task.cwd, task.timeout, task.tier)

                # Update with result
                task.result = result
                if result.success:
                    task.state = TaskState.SUCCESS
                else:
                    task.state = TaskState.FAILED
                task.completed_at = datetime.now()
                self._storage.update(task)

        except asyncio.CancelledError:
            task.state = TaskState.CANCELLED
            task.completed_at = datetime.now()
            self._storage.update(task)
            raise

        except (RuntimeError, ValueError) as e:
            task.state = TaskState.FAILED
            task.result = ExecutionResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=0,
            )
            task.completed_at = datetime.now()
            self._storage.update(task)

        finally:
            async with self._lock:
                if task.id in self._running_tasks:
                    del self._running_tasks[task.id]

    def get_task(self, task_id: str) -> BackgroundTask | None:
        """Get a task by ID."""
        return self._storage.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        """List all tasks."""
        return self._storage.list_all()

    def list_active(self) -> list[BackgroundTask]:
        """List active (queued or running) tasks."""
        return [t for t in self._storage.list_all() if t.state in (TaskState.QUEUED, TaskState.RUNNING)]

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued task.

        Args:
            task_id: Task ID to cancel

        Returns:
            True if task was cancelled
        """
        task = self._storage.get(task_id)
        if not task:
            return False

        if task.state not in (TaskState.QUEUED, TaskState.RUNNING):
            return False

        # Cancel if running
        async with self._lock:
            if task_id in self._running_tasks:
                running_task = self._running_tasks[task_id]
                if running_task is not None:
                    running_task.cancel()

        # Update state
        task.state = TaskState.CANCELLED
        task.completed_at = datetime.now()
        self._storage.update(task)

        return True

    async def wait_for_task(self, task_id: str, timeout: float | None = None) -> BackgroundTask | None:
        """Wait for a task to complete.

        Args:
            task_id: Task ID to wait for
            timeout: Maximum time to wait (None uses hard MAX_WAIT_SECONDS cap)

        Returns:
            Completed task or None if timeout / iteration bound reached
        """
        start = time.monotonic()
        max_wait = timeout if timeout is not None else 30.0
        max_iterations = 100
        iteration = 0

        while True:
            iteration += 1
            elapsed = time.monotonic() - start
            task = self._storage.get(task_id)

            if not task:
                # storage.get() returned None — keep polling (task may not exist yet
                # or storage is temporarily unavailable). Only raise if hard bound hit.
                if iteration >= max_iterations:
                    _logger.warning(
                        "wait_for_task(%s): storage.get() returned None for all "
                        "%d iterations over %.1fs — returning None",
                        task_id,
                        iteration,
                        elapsed,
                    )
                    return None
                if elapsed > max_wait:
                    _logger.debug(
                        "wait_for_task(%s) exited: elapsed=%.1fs > %.1fs",
                        task_id,
                        elapsed,
                        max_wait,
                    )
                    return None
                await asyncio.sleep(0.1)
                continue

            if task.state in (
                TaskState.SUCCESS,
                TaskState.FAILED,
                TaskState.TIMEOUT,
                TaskState.CANCELLED,
            ):
                return task

            # Task exists but is not in a terminal state — still apply bounds
            if iteration >= max_iterations:
                _logger.warning(
                    "wait_for_task(%s): task=%r stayed non-terminal for %d iterations (%.1fs) — returning None",
                    task_id,
                    task.id,
                    iteration,
                    elapsed,
                )
                return None
            if elapsed > max_wait:
                return None

            await asyncio.sleep(0.1)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task.

        Args:
            task_id: Task ID to delete

        Returns:
            True if task was deleted
        """
        return self._storage.delete(task_id)
