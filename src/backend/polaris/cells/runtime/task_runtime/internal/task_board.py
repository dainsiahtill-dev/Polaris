"""Polaris TaskBoard — file-backed task board with DAG dependency tracking.

This is the canonical home for the Polaris TaskBoard implementation.
It was moved here from the historical KernelOne task-board module (2026-03-22)
to resolve the architectural violation of embedding business logic in KernelOne.

Responsibilities:
- File-backed JSON persistence (atomic writes, survives context compaction)
- Row-local DAG fields (blocked_by, blocks)
- State machine with validated transitions
- Priority-based row ordering
- Compatibility terminal-event projection for legacy observers

This module intentionally remains private to ``runtime.task_runtime``.  Public
callers must use ``TaskRuntimeService`` row/session APIs so execution-control
state changes can be accompanied by ``task_runtime.execution`` evidence.

Architecture note: This module is state-owned by the ``runtime.task_runtime`` cell.
Its row path is ``runtime/tasks/*``. ``taskboard.terminal.events`` is a
compatibility projection, not the authoritative task-state source. No other
cell should read or write either path directly.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, cast

from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactStreamError,
    QueryFactEventsV1,
)
from polaris.cells.events.fact_stream.public.service import append_fact_event, query_fact_events
from polaris.domain.entities.task import (
    TaskPriority as PolarisTaskPriority,
    TaskStatus as PolarisTaskStatus,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

_TASKBOARD_TERMINAL_EVENTS_STREAM = "taskboard.terminal.events"
_TASKBOARD_TERMINAL_EVENT_CAS_RETRIES = 3

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


class TaskBoardRowWriteConflictError(RuntimeError):
    """Raised when a task row changed before an atomic replace."""


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

_EXECUTION_OWNER_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
    }
)

_PRIORITY_LABEL_TO_VALUE: dict[str, int] = {
    TaskPriority.LOW.value: 0,
    TaskPriority.MEDIUM.value: 1,
    TaskPriority.HIGH.value: 2,
    TaskPriority.CRITICAL.value: 3,
}

_PRIORITY_VALUE_TO_LABEL: dict[int, str] = {v: k for k, v in _PRIORITY_LABEL_TO_VALUE.items()}
_TASK_ID_TOKEN_RE = re.compile(r"(?:^|[^0-9A-Za-z])(?P<id>\d+)(?:$|[^0-9A-Za-z])")


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


def _normalize_task_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid task id: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"invalid task id: {value!r}")
    token = str(value or "").strip()
    if not token:
        raise ValueError("task id is required")
    if token.isdigit():
        return int(token)
    match = _TASK_ID_TOKEN_RE.search(token)
    if match:
        return int(match.group("id"))
    raise ValueError(f"invalid task id: {value!r}")


def _normalize_task_id_list(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"invalid task id list: {value!r}")
    return [_normalize_task_id(item) for item in value]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        raw_id = data["id"]
        task_id = _normalize_task_id(raw_id)
        subject = str(data.get("subject") or data.get("title") or f"task-{task_id}")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        elif str(raw_id).strip() != str(task_id):
            metadata = dict(metadata)
            metadata.setdefault("external_task_id", str(raw_id).strip())
        raw_blocked_by = data.get("blocked_by", data.get("blockedBy", []))
        return cls(
            id=task_id,
            subject=subject,
            description=data.get("description", ""),
            status=_normalize_status(data.get("status", "pending")),
            created_at=float(data.get("created_at", 0.0)),
            blocked_by=_normalize_task_id_list(raw_blocked_by),
            blocks=_normalize_task_id_list(data.get("blocks", [])),
            owner=data.get("owner", ""),
            assignee=data.get("assignee", ""),
            claimed_by=data.get("claimed_by"),
            priority=_normalize_priority(data.get("priority", data.get("priority_label", 1))),
            tags=data.get("tags", []),
            metadata=metadata,
            estimated_hours=data.get("estimated_hours", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            claimed_at=data.get("claimed_at"),
            result_summary=data.get("result_summary", ""),
            error_message=data.get("error_message"),
            evidence_refs=data.get("evidence_refs", []),
        )


@dataclass(frozen=True)
class TaskBoardRowWriteReceipt:
    """In-memory anchor for the most recent successful TaskBoard row replace."""

    task_id: int | str
    task_path: str
    before_hash: str
    after_hash: str
    operation: str
    written_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe projection suitable for fact event details."""

        return {
            "task_id": self.task_id,
            "task_path": str(self.task_path),
            "before_hash": str(self.before_hash),
            "after_hash": str(self.after_hash),
            "operation": str(self.operation),
            "written_at": str(self.written_at),
        }


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

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), get_default_adapter())
        self.tasks_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/tasks"))
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

        self._max_id_file = Path(resolve_runtime_path(str(self.workspace), "runtime/tasks/.max_id"))
        self._max_id_file.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._ready_condition = threading.Condition(self._lock)
        self._ready_listeners: list[Callable[[], None]] = []
        self._cache: dict[int, Task] = {}
        self._last_row_write_receipt: TaskBoardRowWriteReceipt | None = None
        self._load_all()

    def _logical_path(self, path: Path) -> str:
        return self._kernel_fs.to_logical_path(str(path))

    @contextmanager
    def transaction(self) -> Any:
        """Board-level transaction lock for atomic cache+filesystem updates."""
        with self._lock:
            yield

    @staticmethod
    def _is_ready_task(task: Task) -> bool:
        return task.status in (TaskStatus.PENDING, TaskStatus.READY) and not task.blocked_by and not task.claimed_by

    def _has_ready_task_locked(self) -> bool:
        return any(self._is_ready_task(task) for task in self._cache.values())

    def _notify_ready_tasks(self) -> None:
        listeners: list[Callable[[], None]] = []
        with self._ready_condition:
            if not self._has_ready_task_locked():
                return
            self._ready_condition.notify_all()
            listeners = list(self._ready_listeners)

        for listener in listeners:
            try:
                listener()
            except RuntimeError as exc:
                logger.debug("TaskBoard ready listener failed: %s", exc)

    def notify_ready_tasks(self) -> None:
        """Notify waiters/listeners when at least one row is ready.

        Cross-row orchestration belongs to ``TaskRuntimeService``, but the
        board still owns the condition variable and listener registry used by
        ready-task waiters.
        """

        self._notify_ready_tasks()

    def wait_ready(self, timeout: float | None = None) -> bool:
        """Block until at least one task is ready, without interval polling."""

        wait_timeout = None if timeout is None else max(0.0, float(timeout))
        with self._ready_condition:
            return self._ready_condition.wait_for(self._has_ready_task_locked, timeout=wait_timeout)

    def add_ready_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when a ready task becomes available."""

        with self._ready_condition:
            self._ready_listeners.append(listener)

        def _unsubscribe() -> None:
            with self._ready_condition, suppress(ValueError):
                self._ready_listeners.remove(listener)

        return _unsubscribe

    def _load_all(self) -> None:
        """Load all tasks from disk into in-memory cache."""
        with self.transaction():
            self._cache.clear()
            for task_file in self.tasks_dir.glob("task_*.json"):
                if task_file.name.endswith(".session.json"):
                    continue
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

    def _load_task_from_disk(self, task_id: int) -> Task | None:
        task_path = self.tasks_dir / f"task_{int(task_id)}.json"
        if not task_path.is_file():
            return None
        try:
            logical = self._logical_path(task_path)
            data = json.loads(self._kernel_fs.read_text(logical, encoding="utf-8"))
            task = Task.from_dict(data)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            logger.warning("Failed to load task from %s: %s", task_path, exc)
            return None
        self._cache[task.id] = task
        return task

    def last_row_write_receipt(self) -> TaskBoardRowWriteReceipt | None:
        """Return the last successful TaskBoard row-write receipt anchor."""
        with self.transaction():
            return self._last_row_write_receipt

    def _read_current_task_file_hash(self, task_path: Path) -> str:
        """Return the current UTF-8 content hash, or empty string when absent."""

        if not task_path.is_file():
            return ""

        try:
            logical = self._logical_path(task_path)
            return _sha256_text(self._kernel_fs.read_text(logical, encoding="utf-8"))
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
                return ""
            raise

    def _assert_task_row_unchanged(
        self,
        *,
        task_id: int | str,
        task_path: Path,
        task_logical: str,
        before_hash: str,
    ) -> None:
        current_hash = self._read_current_task_file_hash(task_path)
        if current_hash == before_hash:
            return

        before_label = before_hash or "<absent>"
        current_label = current_hash or "<absent>"
        logger.warning(
            "TaskBoard row write conflict: task_id=%s task_path=%s before_hash=%s current_hash=%s",
            task_id,
            task_logical,
            before_label,
            current_label,
        )
        raise TaskBoardRowWriteConflictError(
            "TaskBoard row write conflict: "
            f"task_id={task_id!r} task_path={task_logical!r} "
            f"before_hash={before_label!r} current_hash={current_label!r}"
        )

    def _task_row_lock_path(self, task_id: int | str) -> Path:
        """Return the stable per-row lock path for one TaskBoard row."""

        normalized_task_id = _normalize_task_id(task_id)
        return self.tasks_dir / f".task_{normalized_task_id}.json.lock"

    def _save_task(self, task: Task) -> None:
        """Atomically save a task row under a cross-process row lock."""

        with self.transaction():
            task_path = self.tasks_dir / f"task_{task.id}.json"
            tmp_path = self.tasks_dir / f".task_{task.id}.{uuid.uuid4().hex}.tmp"
            tmp_logical = self._logical_path(tmp_path)
            task_logical = self._logical_path(task_path)
            lock_path = self._task_row_lock_path(task.id)
            try:
                with self._file_lock(lock_path):
                    before_hash = self._read_current_task_file_hash(task_path)
                    payload = json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n"
                    after_hash = _sha256_text(payload)
                    self._kernel_fs.write_text(tmp_logical, payload, encoding="utf-8")
                    self._assert_task_row_unchanged(
                        task_id=task.id,
                        task_path=task_path,
                        task_logical=task_logical,
                        before_hash=before_hash,
                    )
                    self._replace_task_file(tmp_path, task_path)
                    self._last_row_write_receipt = TaskBoardRowWriteReceipt(
                        task_id=task.id,
                        task_path=task_logical,
                        before_hash=before_hash,
                        after_hash=after_hash,
                        operation="replace",
                        written_at=datetime.now(timezone.utc).isoformat(),
                    )
            finally:
                with suppress(OSError):
                    tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _replace_task_file(tmp_path: Path, task_path: Path) -> None:
        """Replace task JSON with short retry for transient file locks."""
        attempts = 8
        delay = 0.025
        for attempt in range(attempts):
            try:
                os.replace(tmp_path, task_path)
                return
            except PermissionError:
                if attempt >= attempts - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.5)

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
    def _file_lock(self, lock_file_path: Path) -> Any:
        """Cross-platform exclusive file lock context manager."""
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = None
        try:
            lock_file = open(lock_file_path, "a+", encoding="utf-8")  # noqa: SIM115
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt_vars = vars(msvcrt)
                locking = cast(Callable[[int, int, int], None], msvcrt_vars["locking"])
                lock_flag = cast(int, msvcrt_vars["LK_LOCK"])
                locking(lock_file.fileno(), lock_flag, 1)
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
                        msvcrt_vars = vars(msvcrt)
                        locking = cast(Callable[[int, int, int], None], msvcrt_vars["locking"])
                        unlock_flag = cast(int, msvcrt_vars["LK_UNLCK"])
                        locking(lock_file.fileno(), unlock_flag, 1)
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

        should_notify_ready = False
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

            self._cache[task_id] = task
            self._save_task(task)

            should_notify_ready = self._is_ready_task(task)

        if should_notify_ready:
            self._notify_ready_tasks()

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
        *,
        allow_terminal_status: bool = False,
        allow_execution_status: bool = False,
    ) -> Task | None:
        """Update task status with state machine validation.

        When entering a terminal state, appends a lightweight compatibility
        event to ``taskboard.terminal.events``. Execution-control consumers must
        use ``TaskRuntimeService`` / ``task_runtime.execution`` projections as
        the authoritative state source.
        """
        import copy

        next_status = _normalize_status(status)
        is_terminal = next_status.is_terminal
        if is_terminal and not allow_terminal_status:
            raise RuntimeError(f"terminal_taskboard_status_requires_task_runtime_owner_transition:{next_status.value}")
        if next_status in _EXECUTION_OWNER_STATUSES and not allow_execution_status:
            raise RuntimeError(f"taskboard_execution_status_requires_task_runtime_owner_transition:{next_status.value}")

        terminal_event_data: dict[str, Any] | None = None
        if is_terminal:
            terminal_event_data = {
                "task_id": task_id,
                "status": next_status.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result_summary": result_summary[:240] if result_summary else "",
            }

        result_task: Task | None = None
        should_notify_ready = False
        with self.transaction():
            task = self._load_task_from_disk(task_id) or self._cache.get(task_id)
            if not task:
                return None

            old_status = task.status

            # Idempotency guard: re-applying the same terminal status is a no-op.
            # Without this, a second complete()/fail() call would clobber the
            # original completed_at and append a duplicate terminal event.
            # Return the existing task untouched.
            if old_status == next_status and old_status.is_terminal:
                return copy.deepcopy(task)

            if old_status != next_status:
                self._validate_transition(old_status, next_status)

            if old_status.is_terminal and not next_status.is_terminal:
                # Sanctioned terminal -> non-terminal transition (deliberate
                # retry, e.g. FAILED -> PENDING/READY). Record when the row
                # left its terminal state so runtime claim reconciliation can
                # distinguish a deliberate reset from a stale row write that
                # predates authoritative terminal execution evidence.
                task.metadata["terminal_reset_at"] = time.time()

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

            self._save_task(task)
            should_notify_ready = should_notify_ready or self._is_ready_task(task)

            result_task = copy.deepcopy(task)

        # Outside lock: write terminal event
        if terminal_event_data:
            self._write_terminal_event(terminal_event_data)
        if should_notify_ready:
            self._notify_ready_tasks()

        return result_task

    def _next_terminal_event_expected_seq(self) -> int:
        """Return the next expected sequence for the terminal compatibility stream.

        ``taskboard.terminal.events`` is an append-only compatibility
        projection for legacy observers, not task-state authority. This helper
        still uses the public FactStream query contract so the subsequent
        append can participate in the same CAS boundary as authoritative
        runtime fact streams without reading storage internals directly.
        """

        result = query_fact_events(
            QueryFactEventsV1(
                workspace=str(self.workspace),
                stream=_TASKBOARD_TERMINAL_EVENTS_STREAM,
                limit=1,
                offset=0,
            )
        )
        return int(result.total) + 1

    def _append_terminal_event_with_cas(self, event_data: dict[str, Any]) -> None:
        """Append one terminal compatibility event with bounded CAS retry."""

        event_type = str(event_data.get("status") or "terminal").strip().lower() or "terminal"
        task_id = str(event_data.get("task_id") or "").strip() or None
        last_drift: FactStreamError | None = None

        for attempt in range(1, _TASKBOARD_TERMINAL_EVENT_CAS_RETRIES + 1):
            expected_seq = self._next_terminal_event_expected_seq()
            command = AppendFactEventCommandV1(
                workspace=str(self.workspace),
                stream=_TASKBOARD_TERMINAL_EVENTS_STREAM,
                event_type=event_type,
                payload=dict(event_data),
                source="runtime.task_runtime.task_board",
                task_id=task_id,
                expected_seq=expected_seq,
            )
            try:
                append_fact_event(command)
                return
            except FactStreamError as exc:
                if exc.code != "expected_seq_drift":
                    raise
                last_drift = exc
                if attempt >= _TASKBOARD_TERMINAL_EVENT_CAS_RETRIES:
                    break
                logger.debug(
                    "Retrying TaskBoard terminal compatibility event append after "
                    "expected_seq drift task_id=%s attempt=%s expected_seq=%s",
                    task_id,
                    attempt,
                    expected_seq,
                )

        if last_drift is not None:
            raise last_drift

    def _write_terminal_event(self, event_data: dict[str, Any]) -> None:
        """Write a compatibility terminal event outside the board transaction.

        This stream remains a compatibility projection. Task row state and
        ``task_runtime.execution`` facts continue to be the authoritative
        runtime evidence; terminal projection append failures are logged and
        must not roll back the already-validated TaskBoard row mutation.
        """

        try:
            self._append_terminal_event_with_cas(event_data)
        except FactStreamError as exc:
            logger.warning(
                "Failed to append TaskBoard terminal compatibility event task_id=%s stream=%s code=%s details=%s: %s",
                event_data.get("task_id"),
                _TASKBOARD_TERMINAL_EVENTS_STREAM,
                exc.code,
                exc.details,
                exc,
                exc_info=True,
            )
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to append TaskBoard terminal compatibility event task_id=%s stream=%s error_type=%s: %s",
                event_data.get("task_id"),
                _TASKBOARD_TERMINAL_EVENTS_STREAM,
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    def update(
        self,
        task_id: int,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        allow_terminal_status: bool = False,
        allow_execution_status: bool = False,
        allow_dependency_status: bool = False,
    ) -> Task | None:
        """Compatibility update API (delegates to update_status)."""
        import copy

        with self.transaction():
            task = self._load_task_from_disk(task_id) or self._cache.get(task_id)
            if not task:
                return None
            if status is not None:
                task = self.update_status(
                    task_id,
                    status,
                    allow_terminal_status=allow_terminal_status,
                    allow_execution_status=allow_execution_status,
                )
                if task is None:
                    return None
            if assignee is not None:
                task.assignee = str(assignee or "").strip()
            if owner is not None:
                task.owner = str(owner or "").strip()
            if blocked_by is not None:
                normalized_blockers: list[int] = []
                for dep_id in blocked_by:
                    try:
                        dep_id_int = int(dep_id)
                    except (TypeError, ValueError):
                        continue
                    if dep_id_int != int(task_id) and dep_id_int not in normalized_blockers:
                        normalized_blockers.append(dep_id_int)
                task.blocked_by = normalized_blockers
                if task.status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.BLOCKED):
                    next_status = TaskStatus.BLOCKED if task.blocked_by else TaskStatus.PENDING
                    if task.status != next_status and not allow_dependency_status:
                        raise RuntimeError("taskboard_dependency_status_requires_task_runtime_owner_transition")
                    task.status = next_status
            if isinstance(metadata, dict) and metadata:
                task.metadata.update(metadata)
            # Keep in-memory cache in sync when `task` comes from update_status()
            # (which returns a deep copy). Otherwise, metadata/owner/assignee
            # updates are persisted on disk but stale in cache.
            self._cache[task_id] = task
            self._save_task(task)
            return copy.deepcopy(task)

    def update_blocks(self, task_id: int, blocks: list[int]) -> Task | None:
        """Replace the reverse dependency list for one task row.

        ``TaskBoard`` owns row persistence, but callers such as
        ``TaskRuntimeService`` own the cross-row decision that determines which
        reverse dependencies should be written.
        """

        import copy

        normalized_blocks: list[int] = []
        for dependent_id in blocks:
            try:
                dependent_id_int = int(dependent_id)
            except (TypeError, ValueError):
                continue
            if dependent_id_int != int(task_id) and dependent_id_int not in normalized_blocks:
                normalized_blocks.append(dependent_id_int)

        with self.transaction():
            task = self._load_task_from_disk(task_id) or self._cache.get(task_id)
            if not task:
                return None
            task.blocks = normalized_blocks
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
        raise RuntimeError("TaskBoard.claim is retired; use TaskRuntimeService.claim_execution()")

    def complete(self, task_id: int) -> bool:
        raise RuntimeError("TaskBoard.complete is retired; use TaskRuntimeService.complete_execution()")

    def fail(self, task_id: int, reason: str = "") -> bool:
        raise RuntimeError("TaskBoard.fail is retired; use TaskRuntimeService.fail_execution()")

    def reopen(
        self,
        task_id: int,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        allow_terminal_reopen: bool = False,
    ) -> Task | None:
        """Reopen a terminal task for another implementation round."""
        import copy

        should_notify_ready = False
        result_task: Task | None = None
        with self.transaction():
            task = self._cache.get(task_id)
            if not task:
                return None

            if not task.status.is_terminal:
                return copy.deepcopy(task)
            if not allow_terminal_reopen:
                raise RuntimeError("taskboard_reopen_requires_task_runtime_owner_transition")

            # Reopen is the sanctioned terminal-downgrade path; stamp the
            # reset marker so runtime claim reconciliation treats the reopened
            # row as authoritative over older terminal execution sessions.
            task.metadata["terminal_reset_at"] = time.time()
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

            self._save_task(task)

            should_notify_ready = self._is_ready_task(task)
            result_task = copy.deepcopy(task)

        if should_notify_ready:
            self._notify_ready_tasks()

        return result_task

    def reconcile_terminal_status(
        self,
        task_id: int,
        status: TaskStatus | str,
        result_summary: str = "",
        evidence_refs: list[str] | None = None,
    ) -> Task | None:
        """Force a row to a terminal status backed by execution evidence.

        Unlike :meth:`update_status`, this may bridge a non-terminal row whose
        current status has no direct valid transition to the terminal target
        (for example a stale ``ready`` row against a ``failed`` execution
        session). It exists solely so reconciliation of an authoritative
        terminal execution session can never crash the runtime claim path.

        It never rewrites one terminal verdict into another: for a row already
        in a different terminal state it raises
        ``InvalidTaskStateTransitionError`` (``reopen`` is the only sanctioned
        terminal-downgrade path).
        """
        next_status = _normalize_status(status)
        if not next_status.is_terminal:
            raise ValueError(f"reconcile_terminal_status requires a terminal status, got {next_status.value!r}")
        with self.transaction():
            task = self._load_task_from_disk(task_id) or self._cache.get(task_id)
            if not task:
                return None
            if task.status.is_terminal and task.status != next_status:
                raise InvalidTaskStateTransitionError(
                    f"Cannot reconcile terminal task from {task.status.value!r} to "
                    f"{next_status.value!r}; reopen is the only sanctioned terminal downgrade path"
                )
            allowed = _VALID_TRANSITIONS.get(task.status, {task.status})
            if next_status not in allowed:
                # Validation-exempt bridge write: the row is a stale
                # non-terminal projection and the terminal execution session
                # is authoritative. Route through IN_PROGRESS (which has valid
                # transitions to every terminal state) so update_status()
                # applies the full row-local terminal bookkeeping: terminal
                # event, timestamps, and state-bridge notification.
                task.status = TaskStatus.IN_PROGRESS
                self._cache[task_id] = task
                self._save_task(task)
            return self.update_status(
                task_id,
                next_status,
                result_summary=result_summary,
                evidence_refs=evidence_refs,
                allow_terminal_status=True,
            )

    def get_ready_tasks(self) -> list[Task]:
        """Get all tasks that are pending, unblocked, and unclaimed."""
        import copy

        with self.transaction():
            return [copy.deepcopy(t) for t in self._cache.values() if self._is_ready_task(t)]

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


__all__ = [
    "InvalidTaskStateTransitionError",
    "Task",
    "TaskBoard",
    "TaskBoardRowWriteReceipt",
    "TaskPriority",
    "TaskStatus",
]
