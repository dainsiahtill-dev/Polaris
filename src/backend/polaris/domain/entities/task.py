"""Unified Task domain entity for Polaris.

This module provides the single canonical Task model consumed by:
- Director execution cells (worker_pool, task_lifecycle, worker_executor)
- PM/Director/QA role adapters
- V2 API routers and CLI entrypoints
- runtime.task_runtime cell

It merges the lifecycle coverage of all three prior definitions:
  - kernelone/task_graph/task_board.py: PENDING/BLOCKED/IN_PROGRESS/COMPLETED/FAILED/CANCELLED
  - domain/models/task.py: QUEUED/BLOCKED/TIMEOUT
  - domain/entities/task.py (old): READY/CLAIMED + execution config

Migration notes (2026-03-22):
  - kernelone/task_graph/task_board.py TaskStatus/TaskPriority/Task are re-exported
    from here for backward compatibility. KernelOne should NOT contain Polaris
    business semantics; this module is the canonical source.
  - domain/models/task.py is deprecated (its Task was never consumed by any
    active caller; it duplicated domain/entities/task.py).
  - The Polaris TaskBoard implementation (file-backed CRUD + DAG) lives in
    polaris/cells/runtime/task_runtime/internal/task_board.py. kernelone/task_board
    is a thin backward-compat shim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

# ---------------------------------------------------------------------------
# Canonical enums (shared across all Polaris task consumers)
# ---------------------------------------------------------------------------
# Re-export from unified kernelone.errors
from polaris.kernelone.errors import TaskStateError
from polaris.kernelone.utils import utc_now


class TaskStatus(str, Enum):
    """Polaris task lifecycle states.

    Covers the complete workflow from queue → planning → execution → terminal:

    QUEUED      - Waiting for a concurrency slot (not yet eligible for claim)
    PENDING     - Eligible to run but not yet claimed
    READY       - All dependencies satisfied, ready to be claimed
    CLAIMED     - Worker assigned but not yet started
    IN_PROGRESS - Actively executing
    COMPLETED   - Successfully finished
    FAILED      - Execution failed
    CANCELLED   - Manually cancelled
    BLOCKED     - Waiting on unresolved dependencies (pre-READY)
    TIMEOUT     - Execution exceeded time limit
    """

    QUEUED = "queued"
    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    # Alias for backward compat (director task_lifecycle_service uses RUNNING)
    RUNNING = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    # --- Human-in-the-loop state (Chronos Hourglass) ---
    WAITING_HUMAN = "waiting_human"  # Task suspended awaiting human review/approval

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        }

    @property
    def is_active(self) -> bool:
        return self in {
            TaskStatus.QUEUED,
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.CLAIMED,
            TaskStatus.BLOCKED,
            TaskStatus.WAITING_HUMAN,  # Human-in-the-loop: waiting is active (not terminal)
        }

    @property
    def is_executing(self) -> bool:
        return self in {TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}


class TaskPriority(str, Enum):
    """Polaris task priority levels (string-valued for JSON friendliness)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def numeric_value(self) -> int:
        """Return numeric priority (higher = more important)."""
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(self.value, 1)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskEvidence:
    """Evidence of task execution (file reference, test result, log, etc.)."""

    type: str  # e.g. "file", "test_result", "log", "screenshot"
    path: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    """Outcome of a task execution attempt."""

    success: bool
    output: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    evidence: tuple[TaskEvidence, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "evidence": [
                {"type": e.type, "path": e.path, "content": e.content, "metadata": e.metadata} for e in self.evidence
            ],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskResult:
        return cls(
            success=data["success"],
            output=data.get("output", ""),
            exit_code=data.get("exit_code", 0),
            duration_ms=data.get("duration_ms", 0),
            evidence=tuple(TaskEvidence(**e) for e in data.get("evidence", [])),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Canonical Task entity
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """Polaris Task entity.

    Represents a unit of work in the PM/Director/QA collaboration lifecycle.
    Supports the full state graph: QUEUED -> PENDING -> READY -> CLAIMED ->
    IN_PROGRESS -> COMPLETED/FAILED/CANCELLED/TIMEOUT, plus BLOCKED (dependency
    hold) and auto-retry transitions.

    Field design rationale (merged from three prior definitions):
    - ``id`` (int): KernelOne TaskBoard integer ID — used by all active callers.
      String-id variants use ``external_task_id`` in metadata.
    - ``blocked_by`` / ``blocks``: DAG dependency lists (int task IDs).
    - ``owner`` / ``assignee`` / ``claimed_by``: Assignment chain.
    - ``execution_*``: Command-level execution config (from domain/entities).
    - ``role`` / ``constraints`` / ``acceptance_criteria``: PM planning fields
      (from domain/models).
    - ``result_summary`` / ``evidence_refs``: Completion reporting.
    - ``retry_count`` / ``max_retries``: Auto-retry state machine.
    - ``tags`` / ``metadata``: Loose annotation.
    """

    # Identity
    # Accept both str (director execution layer: "task-1") and int (Polaris TaskBoard: 1)
    id: int | str
    subject: str
    description: str = ""

    # Lifecycle
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM

    # DAG
    # Accept both int (Polaris TaskBoard) and str (director execution layer) IDs
    blocked_by: list[int | str] = field(default_factory=list)
    blocks: list[int | str] = field(default_factory=list)

    # Assignment
    owner: str = ""
    assignee: str = ""
    claimed_by: str | None = None

    # PM planning fields
    role: str = ""
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)

    # Execution config (carried through from domain/entities)
    command: str | None = None
    working_directory: str | None = None
    timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    max_retries: int = 3
    retry_count: int = 0

    # Timestamps
    created_at: float = 0.0  # unix epoch seconds
    started_at: float | None = None
    completed_at: float | None = None
    claimed_at: float | None = None

    # Result
    result_summary: str = ""
    error_message: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    _result: TaskResult | None = field(default=None, repr=False)

    # Loose annotation
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------------------
    # Computed properties
    # -------------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_by) > 0 and self.status in (
            TaskStatus.PENDING,
            TaskStatus.BLOCKED,
        )

    @property
    def is_claimable(self) -> bool:
        return self.status == TaskStatus.READY and not self.claimed_by and not any(b for b in self.blocked_by if b != 0)

    @property
    def result(self) -> TaskResult | None:
        return self._result

    # -------------------------------------------------------------------------
    # State transitions
    # -------------------------------------------------------------------------

    def mark_ready(self) -> None:
        """Transition from PENDING to READY when all deps are satisfied."""
        if self.status != TaskStatus.PENDING:
            raise TaskStateError(f"Cannot mark_ready from {self.status.value!r}")
        self.status = TaskStatus.READY

    def claim(self, worker_id: str) -> None:
        """Claim this task for a worker (READY -> CLAIMED)."""
        if not self.is_claimable:
            raise TaskStateError(f"Cannot claim task in status {self.status.value!r}")
        self.status = TaskStatus.CLAIMED
        self.claimed_by = worker_id
        self.claimed_at = _now_seconds()

    def start(self) -> None:
        """Mark as actively executing (CLAIMED -> IN_PROGRESS)."""
        if self.status not in (TaskStatus.CLAIMED, TaskStatus.READY):
            raise TaskStateError(f"Cannot start from {self.status.value!r}")
        self.status = TaskStatus.IN_PROGRESS
        if self.started_at is None:
            self.started_at = _now_seconds()

    def complete(self, result: TaskResult) -> None:
        """Mark task as completed (IN_PROGRESS -> COMPLETED or auto-retry).

        Args:
            result: TaskResult containing success status, output, error, evidence.
        """
        if self.status not in (TaskStatus.IN_PROGRESS, TaskStatus.CLAIMED):
            raise TaskStateError(f"Cannot complete from {self.status.value!r}")

        self._result = result

        if not result.success and self.retry_count < self.max_retries:
            # Auto-retry: reset to READY
            self.status = TaskStatus.READY
            self.retry_count += 1
            self.claimed_by = None
            self.claimed_at = None
            self.started_at = None
            self._result = None
        else:
            self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            self.completed_at = _now_seconds()
            self.result_summary = result.output
            if result.evidence:
                for ev in result.evidence:
                    if ev.path:
                        self.evidence_refs.append(ev.path)
            if result.error:
                self.error_message = result.error

    def cancel(self) -> None:
        """Cancel the task (non-terminal -> CANCELLED)."""
        if self.status.is_terminal:
            raise TaskStateError("Cannot cancel a terminal task")
        self.status = TaskStatus.CANCELLED
        self.completed_at = _now_seconds()

    def timeout_task(self) -> None:
        """Mark task as timed out."""
        if self.status.is_terminal:
            raise TaskStateError("Cannot timeout a terminal task")
        self.status = TaskStatus.TIMEOUT
        self.completed_at = _now_seconds()
        self.error_message = "Execution exceeded timeout limit"

    def reopen(self) -> None:
        """Reopen a terminal task (for rework)."""
        if not self.status.is_terminal:
            raise TaskStateError(f"Cannot reopen non-terminal task: {self.status.value!r}")
        self.status = TaskStatus.BLOCKED if self.blocked_by else TaskStatus.PENDING
        self.claimed_by = None
        self.claimed_at = None
        self.started_at = None
        self.completed_at = None
        self._result = None

    def resolve_dependency(self, dep_id: int | str) -> None:
        """Remove a resolved dependency from blocked_by."""
        if dep_id in self.blocked_by:
            self.blocked_by.remove(dep_id)

    # -------------------------------------------------------------------------
    # Serialisation
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "priority_numeric": self.priority.numeric_value,
            "blocked_by": self.blocked_by.copy(),
            "blocks": self.blocks.copy(),
            "owner": self.owner,
            "assignee": self.assignee,
            "claimed_by": self.claimed_by,
            "role": self.role,
            "constraints": self.constraints.copy(),
            "acceptance_criteria": self.acceptance_criteria.copy(),
            "command": self.command,
            "working_directory": self.working_directory,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "claimed_at": self.claimed_at,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "evidence_refs": self.evidence_refs.copy(),
            "result": self._result.to_dict() if self._result else None,
            "tags": self.tags.copy(),
            "metadata": self.metadata.copy(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """Deserialize from a dict. Accepts both old (str) and new (TaskStatus) status."""
        raw_status = data.get("status", "pending")
        if isinstance(raw_status, TaskStatus):
            status = raw_status
        elif isinstance(raw_status, str):
            try:
                status = TaskStatus(raw_status)
            except ValueError:
                status = TaskStatus.PENDING
        else:
            status = TaskStatus.PENDING

        raw_priority = data.get("priority", "medium")
        if isinstance(raw_priority, TaskPriority):
            priority = raw_priority
        elif isinstance(raw_priority, str):
            try:
                priority = TaskPriority(raw_priority.lower())
            except ValueError:
                priority = TaskPriority.MEDIUM
        else:
            priority = TaskPriority.MEDIUM

        result_data = data.get("result")
        result: TaskResult | None = None
        if result_data and isinstance(result_data, dict):
            result = TaskResult.from_dict(result_data)

        # Handle legacy blockedBy vs blocked_by
        blocked_by = data.get("blocked_by", [])
        if not blocked_by and "blockedBy" in data:
            blocked_by = data["blockedBy"]

        # ID can be int, str, or float (from JSON numbers). Accept all as documented.
        raw_id = data["id"]
        if isinstance(raw_id, (int, str)):
            id_value: int | str = raw_id if isinstance(raw_id, str) else int(raw_id)
        elif isinstance(raw_id, float):
            id_value = int(raw_id)
        else:
            id_value = str(raw_id)

        return cls(
            id=id_value,
            subject=data["subject"],
            description=data.get("description", ""),
            status=status,
            priority=priority,
            blocked_by=blocked_by if isinstance(blocked_by, list) else [],
            blocks=data.get("blocks", []),
            owner=data.get("owner", ""),
            assignee=data.get("assignee", ""),
            claimed_by=data.get("claimed_by"),
            role=data.get("role", ""),
            constraints=data.get("constraints", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            command=data.get("command"),
            working_directory=data.get("working_directory"),
            timeout_seconds=data.get("timeout_seconds", 300),
            max_retries=data.get("max_retries", 3),
            retry_count=data.get("retry_count", 0),
            created_at=float(data.get("created_at", 0)),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            claimed_at=data.get("claimed_at"),
            result_summary=data.get("result_summary", ""),
            error_message=data.get("error_message"),
            evidence_refs=data.get("evidence_refs", []),
            _result=result,
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


def _now_seconds() -> float:
    return utc_now().timestamp()


# ---------------------------------------------------------------------------
# Re-exports for kernelone backward-compatibility shim
# ---------------------------------------------------------------------------

# These are re-exported from kernelone/task_graph/task_board.py so that
# existing import paths continue to work without changes.
# The canonical source is THIS module (domain/entities/task.py).
__all__ = [
    "Task",
    "TaskEvidence",
    "TaskPriority",
    "TaskResult",
    "TaskStateError",
    "TaskStatus",
]
