"""Domain entities for Polaris backend.

These are the core business objects that represent the concepts
in the Director system.
"""

from .defect import DEFAULT_DEFECT_TICKET_FIELDS
from .task import Task, TaskEvidence, TaskPriority, TaskResult, TaskStateError, TaskStatus
from .worker import Worker, WorkerCapabilities, WorkerHealth, WorkerStateError, WorkerStatus, WorkerType

__all__ = [
    # Defect
    "DEFAULT_DEFECT_TICKET_FIELDS",
    # Task
    "Task",
    "TaskEvidence",
    "TaskPriority",
    "TaskResult",
    "TaskStateError",
    "TaskStatus",
    # Worker
    "Worker",
    "WorkerCapabilities",
    "WorkerHealth",
    "WorkerStateError",
    "WorkerStatus",
    "WorkerType",
]
