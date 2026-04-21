"""Polaris TaskBoard — file-backed task board with DAG dependency tracking.

This is the canonical home for the Polaris TaskBoard implementation.
It was moved here from ``polaris.kernelone.task_graph.task_board`` (2026-03-22)
to resolve the architectural violation of embedding business logic in KernelOne.

Responsibilities:
- File-backed JSON persistence (atomic writes, survives context compaction)
- DAG dependency management (blocked_by, blocks)
- State machine with validated transitions
- Priority-based task ordering
- Terminal event emission for async consumers (TaskHistoryArchiver)
- State bridge notifications for workflow runtime sync

This module intentionally mirrors the API of the former kernelone task_board
so that all callers (roles adapters, factory_run_service, CLI entrypoints,
director/pm_service) can migrate over time without immediate import changes.

Consumers of this module:
  - polaris/cells/roles/adapters/internal/{director,pm,qa}_adapter.py
  - polaris/cells/factory/pipeline/internal/factory_run_service.py
  - polaris/cells/orchestration/pm_planning/internal/pm_agent.py
  - polaris/delivery/cli/director/director_service.py
  - polaris/delivery/cli/pm/pm_service.py
  - polaris/cells/runtime/task_runtime/internal/service.py
  - polaris/cells/runtime/task_runtime/internal/worker_pool.py

Architecture note: This module is state-owned by the ``runtime.task_runtime`` cell.
Its state paths are ``runtime/tasks/*`` and ``runtime/events/taskboard.terminal.events.jsonl``.
No other cell should write to these paths.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event
from polaris.domain.entities.task import (
    TaskPriority as PolarisTaskPriority,
    TaskStatus as PolarisTaskStatus,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums (canonical source: domain/entities/task.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Local aliases for the Polaris-specific enums used by this board.
# These are string-valued and JSON-serialisable without conversion.
# ---------------------------------------------------------------------------


class TaskStatus(Enum):
    """Task lifecycle states used by the Polaris TaskBoard.

    Alias for ``PolarisTaskStatus`` for backward compatibility with
    existing callers that import from this module.
    """

    QUEUED = "queued"
    PENDING = "pending"
    BLOCKED = "blocked"
    READY = "ready"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        }


class TaskPriority(Enum):
    """Compatibility priority labels (string-valued for JSON serialisation)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def numeric_value(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(self.value, 1)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class InvalidTaskStateTransitionError(ValueError):
    """Raised when a task status transition is not allowed."""


_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {
        TaskStatus.QUEUED,
        TaskStatus.PENDING,
        TaskStatus.READY,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PENDING: {
        TaskStatus.PENDING,
        TaskStatus.BLOCKED,
        TaskStatus.READY,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.BLOCKED,
        TaskStatus.PENDING,
        TaskStatus.READY,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.READY: {
        TaskStatus.READY,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
    },
    TaskStatus.CLAIMED: {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.PENDING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
    },
    TaskStatus.COMPLETED: {TaskStatus.COMPLETED},
    TaskStatus.FAILED: {
        TaskStatus.FAILED,
        TaskStatus.PENDING,
        TaskStatus.READY,
        TaskStatus.CANCELLED,
    },
    TaskStatus.CANCELLED: {
        TaskStatus.CANCELLED,
        TaskStatus.PENDING,
        TaskStatus.READY,
    },
    TaskStatus.TIMEOUT: {
        TaskStatus.TIMEOUT,
        TaskStatus.PENDING,
        TaskStatus.READY,
        TaskStatus.FAILED,
    },
}

_PRIORITY_LABEL_TO_VALUE: dict[str, int] = {
    TaskPriority.LOW.value: 0,
    TaskPriority.MEDIUM.value: 1,
    TaskPriority.HIGH.value: 2,
    TaskPriority.CRITICAL.value: 3,
}

_PRIORITY_VALUE_TO_LABEL: dict[int, str] = {v: k for k, v in _PRIORITY_LABEL_TO_VALUE.items()}


def _normalize_priority(priority: Any) -> int:
    if isinstance(priority, TaskPriority):
        return _PRIORITY_LABEL_TO_VALUE.get(priority.value, 1)
    if isinstance(priority, PolarisTaskPriority):
        return priority.numeric_value
    if isinstance(priority, (int, float)):
        return int(priority)
    if isinstance(priority, str):
        token = priority.strip().lower()
        if token in _PRIORITY_LABEL_TO_VALUE:
            return _PRIORITY_LABEL_TO_VALUE[token]
        try:
            return int(token)
        except ValueError:
            return 1
    return 1


def _normalize_status(value: Any) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    if isinstance(value, PolarisTaskStatus):
        try:
            return TaskStatus(value.value)
        except ValueError:
            return TaskStatus.PENDING
    token = str(value or "").strip().lower()
    # Legacy aliases
    aliases = {
        "done": "completed",
        "error": "failed",
        "running": "in_progress",
    }
    token = aliases.get(token, token)
    try:
        return TaskStatus(token)
    except ValueError:
        return TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """A task with dependency tracking for the Polaris TaskBoard."""

    id: int
    subject: str
    description: str
    status: TaskStatus
    created_at: float

    # DAG
    blocked_by: list[int] = field(default_factory=list)
    blocks: list[int] = field(default_factory=list)

    # Assignment
    owner: str = ""
    assignee: str = ""
    claimed_by: str | None = None

    # Priority (numeric, higher = more important)
    priority: int = 0

    # Loose annotation
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    estimated_hours: float = 0.0

    # Timestamps
    started_at: float | None = None
    completed_at: float | None = None
    claimed_at: float | None = None

    # Result
    result_summary: str = ""
    error_message: str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_by) > 0

    @property
    def priority_label(self) -> str:
        return _PRIORITY_VALUE_TO_LABEL.get(int(self.priority), str(self.priority))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "blocked_by": self.blocked_by,
            "blockedBy": self.blocked_by,  # Legacy alias for backward compat
            "blocks": self.blocks,
            "owner": self.owner,
            "assignee": self.assignee,
            "claimed_by": self.claimed_by,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "tags": self.tags,
            "metadata": self.metadata,
            "estimated_hours": self.estimated_hours,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "claimed_at": self.claimed_at,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "evidence_refs": self.evidence_refs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=int(data["id"]),
            subject=data["subject"],
            description=data.get("description", ""),
            status=_normalize_status(data.get("status", "pending")),
            created_at=float(data["created_at"]),
            blocked_by=data.get("blocked_by", data.get("blockedBy", [])),
            blocks=data.get("blocks", []),
            owner=data.get("owner", ""),
            assignee=data.get("assignee", ""),
            claimed_by=data.get("claimed_by"),
            priority=_normalize_priority(data.get("priority", data.get("priority_label", 1))),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            estimated_hours=data.get("estimated_hours", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            claimed_at=data.get("claimed_at"),
            result_summary=data.get("result_summary", ""),
            error_message=data.get("error_message"),
            evidence_refs=data.get("evidence_refs", []),
        )


# ---------------------------------------------------------------------------
# TaskBoard
# ---------------------------------------------------------------------------


class TaskBoard:
    """File-backed task board with dependency graph.

    State-owned by ``runtime.task_runtime`` cell.

    Each task is a separate JSON file under ``runtime/tasks/`` for:
    - Atomic updates (write-to-temp + atomic rename)
    - Survives context compaction and process restarts
    - Easy inspection and debugging
    """

    def __init__(self, workspace: str, state_bridge: Any = None) -> None:
        self.workspace = Path(workspace).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), get_default_adapter())
        self.tasks_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/tasks"))
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

        self._max_id_file = Path(resolve_runtime_path(str(self.workspace), "runtime/tasks/.max_id"))
        self._max_id_file.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._cache: dict[int, Task] = {}
        self._state_bridge = state_bridge  # Optional workflow runtime bridge
        self._load_all()

    def _logical_path(self, path: Path) -> str:
        return self._kernel_fs.to_logical_path(str(path))

    @contextmanager
    def transaction(self):
        """Board-level transaction lock for atomic cache+filesystem updates."""
        with self._lock:
            yield

    def _load_all(self) -> None:
        """Load all tasks from disk into in-memory cache."""
        with self.transaction():
            self._cache.clear()
            for task_file in self.tasks_dir.glob("task_*.json"):
                try:
                    logical = self._logical_path(task_file)
                    data = json.loads(self._kernel_fs.read_text(logical, encoding="utf-8"))
                    task = Task.from_dict(data)
                    self._cache[task.id] = task
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    KeyError,
                    json.JSONDecodeError,
                ) as exc:
                    logger.warning("Failed to load task from %s: %s", task_file, exc)

    def _save_task(self, task: Task) -> None:
        """Atomically save a task to disk (write-to-temp + os.replace)."""
        with self.transaction():
            task_path = self.tasks_dir / f"task_{task.id}.json"
            tmp_path = task_path.with_suffix(".tmp")
            tmp_logical = self._logical_path(tmp_path)
            payload = json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n"
            self._kernel_fs.write_text(tmp_logical, payload, encoding="utf-8")
            os.replace(tmp_path, task_path)

    def _load_max_id(self) -> int:
        if not self._max_id_file.exists():
            return 0
        try:
            logical = self._logical_path(self._max_id_file)
            return int(self._kernel_fs.read_text(logical, encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0

    def _save_max_id(self, value: int) -> None:
        logical = self._logical_path(self._max_id_file)
        self._kernel_fs.write_text(logical, str(int(value)), encoding="utf-8")

    @contextmanager
    def _file_lock(self, lock_file_path: Path):
        """Cross-platform exclusive file lock context manager."""
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = None
        try:
            lock_file = open(lock_file_path, "a+", encoding="utf-8")
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
            yield lock_file
        finally:
            if lock_file:
                try:
                    if os.name == "nt":
                        import msvcrt

                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
                except OSError as exc:
                    logger.debug("Failed to unlock file %s: %s", lock_file_path, exc)
                finally:
                    lock_file.close()

    def _get_next_id(self) -> int:
        """Get next task ID with cross-process file locking."""
        lock_file_path = self._max_id_file.with_suffix(".lock")
        with self._file_lock(lock_file_path):
            current_max = self._load_max_id()
            for task_id in self._cache:
                current_max = max(current_max, task_id)
            next_id = current_max + 1
            self._save_max_id(next_id)
            return next_id

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str | TaskPriority = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new task.

        Args:
            subject: Task title.
            description: Detailed description.
            blocked_by: Task IDs that must complete before this task can run.
            priority: Numeric (higher=more important) or TaskPriority label.
            tags: Optional tag list.
            estimated_hours: Estimated work hours.

        Returns:
            The created Task.
        """
        import copy

        with self.transaction():
            task_id = self._get_next_id()
            deps = blocked_by or []

            # Determine initial status based on dependencies
            initial_status = TaskStatus.BLOCKED if deps else TaskStatus.PENDING

            task = Task(
                id=task_id,
                subject=subject,
                description=description,
                status=initial_status,
                created_at=time.time(),
                blocked_by=copy.deepcopy(deps),
                owner=owner,
                assignee=assignee,
                priority=_normalize_priority(priority),
                tags=tags or [],
                estimated_hours=estimated_hours,
                metadata=dict(metadata or {}),
            )

            # Update reverse dependencies (this task blocks others)
            for blocker_id in task.blocked_by:
                blocker = self._cache.get(blocker_id)
                if blocker and task_id not in blocker.blocks:
                    blocker.blocks.append(task_id)
                    self._save_task(blocker)

            self._cache[task_id] = task
            self._save_task(task)

            # Notify state bridge
            if self._state_bridge is not None:
                self._state_bridge.notify_task_created(
                    task_id=task_id,
                    subject=subject,
                    status=task.status.value,
                    blocked_by=deps,
                )

            return task

    def get(self, task_id: int) -> Task | None:
        """Get a task by numeric ID. Returns a deep copy."""
        import copy

        with self.transaction():
            task = self._cache.get(task_id)
            return copy.deepcopy(task) if task is not None else None

    def get_task(self, task_id: int | str) -> dict[str, Any] | None:
        """Compatibility helper: get task as dict, supports 'task-N' tokens."""
        try:
            token = str(task_id or "").strip()
            if token.lower().startswith("task-"):
                token = token.split("-", 1)[1]
            normalized = int(token)
        except (TypeError, ValueError):
            return None
        task = self.get(normalized)
        return task.to_dict() if task is not None else None

    def _validate_transition(self, old_status: TaskStatus, new_status: TaskStatus) -> None:
        allowed = _VALID_TRANSITIONS.get(old_status, {old_status})
        if new_status not in allowed:
            raise InvalidTaskStateTransitionError(
                f"Cannot transition task from {old_status.value!r} to {new_status.value!r}"
            )

    def update_status(
        self,
        task_id: int,
        status: TaskStatus | str,
        result_summary: str = "",
        evidence_refs: list[str] | None = None,
        workflow_id: str = "",
    ) -> Task | None:
        """Update task status with state machine validation.

        When entering a terminal state, appends a lightweight event to
        ``runtime/events/taskboard.terminal.events.jsonl`` for async consumption
        by the TaskHistoryArchiver.
        """
        import copy

        next_status = _normalize_status(status)
        is_terminal = next_status.is_terminal

        terminal_event_data: dict[str, Any] | None = None
        if is_terminal:
            terminal_event_data = {
                "task_id": task_id,
                "status": next_status.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result_summary": result_summary[:240] if result_summary else "",
            }

        result_task: Task | None = None
        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return None

            old_status = task.status
            if old_status != next_status:
                self._validate_transition(old_status, next_status)

            task.status = next_status

            # Track timestamps
            if next_status in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
                if task.claimed_at is None and next_status == TaskStatus.CLAIMED:
                    task.claimed_at = time.time()
                if task.started_at is None:
                    task.started_at = time.time()

            if next_status.is_terminal:
                task.completed_at = time.time()
                task.result_summary = result_summary or task.result_summary
                if evidence_refs:
                    task.evidence_refs.extend(evidence_refs)

                # Dependency unblocking
                self._unblock_dependent_tasks(task_id)

            self._save_task(task)

            # State bridge notification
            if self._state_bridge is not None:
                if next_status == TaskStatus.COMPLETED:
                    self._state_bridge.notify_task_completed(
                        task_id=task_id,
                        result_summary=result_summary,
                        workflow_id=workflow_id or None,
                    )
                else:
                    self._state_bridge.notify_task_updated(
                        task_id=task_id,
                        status=next_status.value,
                        workflow_id=workflow_id or None,
                    )

            result_task = copy.deepcopy(task)

        # Outside lock: write terminal event
        if terminal_event_data:
            self._write_terminal_event(terminal_event_data)

        return result_task

    def _unblock_dependent_tasks(self, completed_task_id: int) -> None:
        """Remove completed task from blocked_by lists of dependent tasks."""
        for task in self._cache.values():
            if completed_task_id in task.blocked_by:
                task.blocked_by.remove(completed_task_id)
                if task.status == TaskStatus.BLOCKED and not task.blocked_by:
                    task.status = TaskStatus.PENDING
                self._save_task(task)

    def _write_terminal_event(self, event_data: dict[str, Any]) -> None:
        """Write terminal event to JSONL outside the board transaction lock."""
        try:
            append_fact_event(
                AppendFactEventCommandV1(
                    workspace=str(self.workspace),
                    stream="taskboard.terminal.events",
                    event_type=str(event_data.get("status") or "terminal").strip().lower() or "terminal",
                    payload=dict(event_data),
                    source="runtime.task_runtime.task_board",
                    task_id=str(event_data.get("task_id") or "").strip() or None,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to write terminal event for task %s: %s",
                event_data.get("task_id"),
                exc,
            )

    def update(
        self,
        task_id: int,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        """Compatibility update API (delegates to update_status)."""
        import copy

        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return None
            if status is not None:
                task = self.update_status(task_id, status)
                if task is None:
                    return None
            if assignee is not None:
                task.assignee = str(assignee or "").strip()
            if owner is not None:
                task.owner = str(owner or "").strip()
            if isinstance(metadata, dict) and metadata:
                task.metadata.update(metadata)
            # Keep in-memory cache in sync when `task` comes from update_status()
            # (which returns a deep copy). Otherwise, metadata/owner/assignee
            # updates are persisted on disk but stale in cache.
            self._cache[task_id] = task
            self._save_task(task)
            return copy.deepcopy(task)

    def assign(self, task_id: int, owner: str) -> Task | None:
        """Assign task owner without changing execution status."""
        import copy

        with self.transaction():
            task = self._cache.get(task_id)
            if task:
                task.owner = str(owner or "").strip()
                self._save_task(task)
            return copy.deepcopy(task) if task else None

    def claim(self, task_id: int, worker_id: str) -> bool:
        """Claim a task for a worker (READY -> CLAIMED)."""
        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return False
            # Check dependencies are satisfied
            blocked = any(
                self._cache.get(dep_id) is not None and self._cache[dep_id].status != TaskStatus.COMPLETED
                for dep_id in task.blocked_by
                if dep_id in self._cache
            )
            if blocked:
                return False
            updated = self.update(task_id, status=TaskStatus.IN_PROGRESS, assignee=worker_id)
            return updated is not None

    def complete(self, task_id: int) -> bool:
        updated = self.update_status(task_id, TaskStatus.COMPLETED)
        return updated is not None

    def fail(self, task_id: int, reason: str = "") -> bool:
        updated = self.update(
            task_id,
            status=TaskStatus.FAILED,
            metadata={"failure_reason": reason},
        )
        return updated is not None

    def reopen(
        self,
        task_id: int,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        """Reopen a terminal task for another implementation round."""
        import copy

        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return None

            if not task.status.is_terminal:
                return copy.deepcopy(task)

            task.status = TaskStatus.BLOCKED if task.blocked_by else TaskStatus.PENDING
            task.assignee = ""
            task.claimed_by = None
            task.started_at = None
            task.completed_at = None
            task.claimed_at = None

            if reason:
                task.result_summary = str(reason).strip()[:240]

            if isinstance(metadata, dict) and metadata:
                task.metadata.update(metadata)

            # Re-block downstream tasks that were waiting on this one
            for dependent_id in task.blocks:
                dependent = self._cache.get(dependent_id)
                if not dependent:
                    continue
                if task_id not in dependent.blocked_by:
                    dependent.blocked_by.append(task_id)
                if dependent.status == TaskStatus.PENDING:
                    dependent.status = TaskStatus.BLOCKED
                self._save_task(dependent)

            self._save_task(task)

            if self._state_bridge is not None:
                self._state_bridge.notify_task_updated(
                    task_id=task_id,
                    status=task.status.value,
                    workflow_id=None,
                )

            return copy.deepcopy(task)

    def get_ready_tasks(self) -> list[Task]:
        """Get all tasks that are pending, unblocked, and unclaimed."""
        import copy

        with self.transaction():
            return [
                copy.deepcopy(t)
                for t in self._cache.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.READY) and not t.blocked_by and not t.claimed_by
            ]

    def list_ready(self) -> list[Task]:
        """Compatibility alias for role-agent worker polling."""
        return self.get_ready_tasks()

    def list_my_tasks(self, worker_id: str) -> list[Task]:
        """List all tasks assigned to a specific worker."""
        import copy

        with self.transaction():
            return [copy.deepcopy(t) for t in self._cache.values() if t.assignee == worker_id]

    def get_blocked_tasks(self) -> list[Task]:
        """Get all tasks that are currently blocked by dependencies."""
        import copy

        with self.transaction():
            return [
                copy.deepcopy(t)
                for t in self._cache.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.BLOCKED) and t.blocked_by
            ]

    def get_dependency_graph(self, task_id: int) -> dict[str, Any]:
        """Get full dependency graph for a task (upstream + downstream)."""
        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return {"error": "Task not found"}

            def get_upstream(tid: int, visited: set[int]) -> list[dict]:
                if tid in visited:
                    return []
                visited.add(tid)
                t = self._cache.get(tid)
                if not t:
                    return []
                result = [{"id": t.id, "subject": t.subject, "status": t.status.value}]
                for bid in t.blocked_by:
                    result.extend(get_upstream(bid, visited))
                return result

            def get_downstream(tid: int, visited: set[int]) -> list[dict]:
                if tid in visited:
                    return []
                visited.add(tid)
                t = self._cache.get(tid)
                if not t:
                    return []
                result = [{"id": t.id, "subject": t.subject, "status": t.status.value}]
                for bid in t.blocks:
                    result.extend(get_downstream(bid, visited))
                return result

            return {
                "task": {
                    "id": task.id,
                    "subject": task.subject,
                    "status": task.status.value,
                },
                "depends_on": get_upstream(task_id, set())[1:],  # Exclude self
                "blocks": get_downstream(task_id, set())[1:],  # Exclude self
            }

    def list_all(
        self,
        status: TaskStatus | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        """List tasks with optional filtering, sorted by priority desc then created_at asc."""
        import copy

        with self.transaction():
            tasks = [copy.deepcopy(t) for t in self._cache.values()]
            if status:
                tasks = [t for t in tasks if t.status == status]
            if owner:
                tasks = [t for t in tasks if t.owner == owner]
            if tag:
                tasks = [t for t in tasks if tag in t.tags]
            tasks.sort(key=lambda t: (-t.priority, t.created_at))
            return tasks

    def get_critical_path(self) -> list[Task]:
        """Estimate the critical path — longest chain of dependencies."""
        import copy

        with self.transaction():
            if not self._cache:
                return []

            # Topological sort via DFS with cycle detection
            lengths: dict[int, int] = {}
            visiting: set[int] = set()

            for root_id in self._cache:
                if root_id in lengths:
                    continue
                stack: list[tuple[int, bool]] = [(root_id, False)]
                while stack:
                    current_id, expanded = stack.pop()
                    if current_id in lengths:
                        continue
                    current_task = self._cache.get(current_id)
                    if current_task is None:
                        lengths[current_id] = 0
                        continue
                    if expanded:
                        best = 1
                        for dep_id in current_task.blocked_by:
                            best = max(best, 1 + lengths.get(dep_id, 0))
                        lengths[current_id] = best
                        visiting.discard(current_id)
                        continue
                    if current_id in visiting:
                        lengths[current_id] = 1
                        continue
                    visiting.add(current_id)
                    stack.append((current_id, True))
                    for dep_id in current_task.blocked_by:
                        if dep_id not in lengths and dep_id not in visiting:
                            stack.append((dep_id, False))

            # Find terminal tasks (completed or leaf nodes)
            terminal_tasks = [t for t in self._cache.values() if t.is_terminal or not t.blocks]
            if not terminal_tasks:
                return []

            critical_task = max(terminal_tasks, key=lambda t: lengths.get(t.id, 1))

            # Reconstruct path
            path: list[Task] = []
            current: Task | None = critical_task
            visited: set[int] = set()

            while current is not None and current.id not in visited:
                visited.add(current.id)
                path.append(copy.deepcopy(current))
                if not current.blocked_by:
                    break
                next_id = max(current.blocked_by, key=lambda dep_id: lengths.get(dep_id, 0))
                current = self._cache.get(next_id)

            return list(reversed(path))

    def get_stats(self) -> dict[str, Any]:
        """Get board statistics."""
        with self.transaction():
            total = len(self._cache)
            by_status: dict[str, int] = {}
            for t in self._cache.values():
                key = t.status.value
                by_status[key] = by_status.get(key, 0) + 1

            blocked = len(self.get_blocked_tasks())
            ready = len(self.get_ready_tasks())
            total_estimated = sum(t.estimated_hours for t in self._cache.values())
            completed = by_status.get("completed", 0)
            completion_rate = completed / total if total > 0 else 0

            return {
                "total": total,
                "by_status": by_status,
                # Compatibility fields expected by role-agent tooling
                "pending": by_status.get("pending", 0),
                "in_progress": by_status.get("in_progress", 0),
                "completed": completed,
                "blocked": blocked,
                "failed": by_status.get("failed", 0),
                "ready": ready,
                "total_estimated_hours": total_estimated,
                "completion_rate": f"{completion_rate * 100:.1f}%",
            }


# ---------------------------------------------------------------------------
# Tool interface (backward compat)
# ---------------------------------------------------------------------------


class TaskBoardToolInterface:
    """Tool interface for task board operations (LLM tool calling)."""

    def __init__(self, board: TaskBoard) -> None:
        self.board = board

    def task_create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int = 1,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Tool: Create a new task."""
        task = self.board.create(
            subject=subject,
            description=description,
            blocked_by=blocked_by,
            priority=priority,
            tags=tags,
        )
        return {
            "ok": True,
            "task_id": task.id,
            "subject": task.subject,
            "status": task.status.value,
            "blocked_by": task.blocked_by,
        }

    def task_update(
        self,
        task_id: int,
        status: str,
        result_summary: str = "",
    ) -> dict[str, Any]:
        """Tool: Update task status."""
        try:
            task_status = TaskStatus(status)
        except ValueError:
            return {"ok": False, "error": f"Invalid status: {status!r}"}

        try:
            task = self.board.update_status(task_id, task_status, result_summary)
        except InvalidTaskStateTransitionError as exc:
            return {"ok": False, "error": str(exc)}
        if not task:
            return {"ok": False, "error": "Task not found"}

        return {
            "ok": True,
            "task_id": task.id,
            "new_status": task.status.value,
            "unblocked_tasks": [{"id": t.id, "subject": t.subject} for t in self.board.get_ready_tasks()],
        }

    def task_list(
        self,
        status: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Tool: List tasks."""
        status_enum: TaskStatus | None = None
        if status:
            try:
                status_enum = TaskStatus(status)
            except ValueError:
                return {"ok": False, "error": f"Invalid status: {status!r}"}

        tasks = self.board.list_all(status=status_enum, owner=owner)

        return {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "subject": t.subject[:50],
                    "status": t.status.value,
                    "owner": t.owner,
                    "blocked_by": t.blocked_by,
                    "priority": t.priority,
                }
                for t in tasks
            ],
            "stats": self.board.get_stats(),
        }

    def task_dependencies(self, task_id: int) -> dict[str, Any]:
        """Tool: Get dependency graph for a task."""
        graph = self.board.get_dependency_graph(task_id)
        if "error" in graph:
            return {"ok": False, "error": graph["error"]}
        return {"ok": True, **graph}

    def task_ready(self) -> dict[str, Any]:
        """Tool: Get all ready-to-work tasks."""
        ready = self.board.get_ready_tasks()
        return {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "subject": t.subject,
                    "priority": t.priority,
                    "estimated_hours": t.estimated_hours,
                }
                for t in sorted(ready, key=lambda x: -x.priority)
            ],
            "count": len(ready),
        }

    def task_assign(self, task_id: int, owner: str) -> dict[str, Any]:
        """Tool: Assign task to owner."""
        task = self.board.assign(task_id, owner)
        if not task:
            return {"ok": False, "error": "Task not found"}
        return {"ok": True, "task_id": task_id, "owner": owner}


def create_taskboard(workspace: str) -> TaskBoard:
    """Compatibility factory used by role-agent subsystem."""
    return TaskBoard(workspace)


__all__ = [
    "InvalidTaskStateTransitionError",
    "Task",
    "TaskBoard",
    "TaskBoardToolInterface",
    "TaskPriority",
    "TaskStatus",
    "create_taskboard",
]
