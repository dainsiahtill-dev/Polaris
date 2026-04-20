"""Worker entity for new Director architecture.

Worker represents an execution unit that can claim and execute tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from .task import Task, TaskResult


class WorkerStatus(Enum):
    """Worker lifecycle status."""

    IDLE = auto()  # Ready to accept tasks
    BUSY = auto()  # Executing a task
    STOPPING = auto()  # Gracefully shutting down
    STOPPED = auto()  # Terminated
    FAILED = auto()  # Crashed or unresponsive


class WorkerType(Enum):
    """Type of worker."""

    LOCAL = auto()  # Local process/thread
    REMOTE = auto()  # Remote worker (future)
    CONTAINER = auto()  # Containerized worker (future)


@dataclass
class WorkerCapabilities:
    """Capabilities of a worker."""

    can_execute_bash: bool = True
    can_write_files: bool = True
    can_access_network: bool = True
    max_file_size_mb: int = 100
    supported_languages: list[str] = field(default_factory=lambda: ["python", "bash"])


@dataclass(frozen=True)
class WorkerHealth:
    """Health metrics of a worker.

    This is an immutable value object representing health state at a point in time.
    Use WorkerHealth.with_updates() to create a new instance with updated metrics.
    """

    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_execution_time_ms: int = 0
    consecutive_failures: int = 0

    def is_healthy(self, timeout_seconds: int = 60) -> bool:
        """Check if worker is healthy based on last heartbeat."""
        elapsed = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        return elapsed < timeout_seconds

    def with_updates(
        self,
        last_heartbeat: datetime | None = None,
        tasks_completed: int | None = None,
        tasks_failed: int | None = None,
        total_execution_time_ms: int | None = None,
        consecutive_failures: int | None = None,
    ) -> WorkerHealth:
        """Create a new WorkerHealth instance with updated fields."""
        return WorkerHealth(
            last_heartbeat=last_heartbeat if last_heartbeat is not None else self.last_heartbeat,
            tasks_completed=tasks_completed if tasks_completed is not None else self.tasks_completed,
            tasks_failed=tasks_failed if tasks_failed is not None else self.tasks_failed,
            total_execution_time_ms=total_execution_time_ms
            if total_execution_time_ms is not None
            else self.total_execution_time_ms,
            consecutive_failures=consecutive_failures
            if consecutive_failures is not None
            else self.consecutive_failures,
        )


@dataclass
class Worker:
    """An execution unit that can claim and execute tasks.

    Worker represents a process or thread that:
    - Runs in a loop waiting for tasks
    - Claims tasks from the task queue
    - Executes tasks with proper isolation
    - Reports results back to Director
    - Maintains health metrics
    """

    # Identity
    id: str
    name: str
    worker_type: WorkerType = WorkerType.LOCAL

    # Status
    status: WorkerStatus = WorkerStatus.IDLE
    current_task_id: str | None = None

    # Capabilities and Health
    capabilities: WorkerCapabilities = field(default_factory=WorkerCapabilities)
    health: WorkerHealth = field(default_factory=WorkerHealth)

    # Configuration
    max_concurrent_tasks: int = 1  # Currently only support 1
    heartbeat_interval_seconds: int = 30
    task_timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    stopped_at: datetime | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_available(self) -> bool:
        """Check if worker is available to claim a task."""
        return self.status == WorkerStatus.IDLE and self.health.is_healthy()

    def can_accept_task(self, task: Task) -> bool:
        """Check if worker can accept a specific task."""
        if not self.is_available():
            return False

        # Check capabilities
        return not (task.command and "bash" in task.command and not self.capabilities.can_execute_bash)

    def claim_task(self, task_id: str) -> None:
        """Claim a task for execution."""
        if not self.is_available():
            raise WorkerStateError(f"Worker {self.id} is not available (status: {self.status})")

        self.status = WorkerStatus.BUSY
        self.current_task_id = task_id
        self.started_at = datetime.now(timezone.utc)

    def release_task(self, result: TaskResult) -> None:
        """Release current task and update metrics."""
        if self.status != WorkerStatus.BUSY:
            raise WorkerStateError(f"Worker {self.id} is not busy")

        # Create new health instance with updated metrics (immutable pattern)
        self.health = self.health.with_updates(
            last_heartbeat=datetime.now(timezone.utc),
            tasks_completed=self.health.tasks_completed + (1 if result.success else 0),
            tasks_failed=self.health.tasks_failed + (0 if result.success else 1),
            total_execution_time_ms=self.health.total_execution_time_ms + result.duration_ms,
            consecutive_failures=0 if result.success else self.health.consecutive_failures + 1,
        )

        self.status = WorkerStatus.IDLE
        self.current_task_id = None

    def update_heartbeat(self) -> None:
        """Update worker heartbeat."""
        self.health = self.health.with_updates(last_heartbeat=datetime.now(timezone.utc))

    def mark_failed(self, reason: str) -> None:
        """Mark worker as failed."""
        self.status = WorkerStatus.FAILED
        self.stopped_at = datetime.now(timezone.utc)
        self.metadata["failure_reason"] = reason

    def request_stop(self) -> None:
        """Request graceful shutdown."""
        if self.status == WorkerStatus.BUSY:
            self.status = WorkerStatus.STOPPING
        else:
            self.status = WorkerStatus.STOPPED
            self.stopped_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "worker_type": self.worker_type.name,
            "status": self.status.name,
            "current_task_id": self.current_task_id,
            "capabilities": {
                "can_execute_bash": self.capabilities.can_execute_bash,
                "can_write_files": self.capabilities.can_write_files,
                "can_access_network": self.capabilities.can_access_network,
                "max_file_size_mb": self.capabilities.max_file_size_mb,
                "supported_languages": self.capabilities.supported_languages,
            },
            "health": {
                "last_heartbeat": self.health.last_heartbeat.isoformat(),
                "tasks_completed": self.health.tasks_completed,
                "tasks_failed": self.health.tasks_failed,
                "total_execution_time_ms": self.health.total_execution_time_ms,
                "consecutive_failures": self.health.consecutive_failures,
                "is_healthy": self.health.is_healthy(),
            },
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "task_timeout_seconds": self.task_timeout_seconds,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "metadata": self.metadata,
        }


# Re-export from unified kernelone.errors
from polaris.kernelone.errors import WorkerStateError
