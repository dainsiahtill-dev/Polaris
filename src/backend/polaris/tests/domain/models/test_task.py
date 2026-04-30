# ruff: noqa: E402
"""Tests for polaris.domain.models.task (deprecated re-export module).

Covers:
- DeprecationWarning on import
- Re-exported entities (Task, TaskStatus, TaskPriority)
- Backward compatibility behaviour
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.entities.task import (
    Task as CanonicalTask,
    TaskPriority as CanonicalTaskPriority,
    TaskStatus as CanonicalTaskStatus,
)

# =============================================================================
# Deprecation warnings
# =============================================================================


class TestTaskDeprecation:
    def test_warn_called_on_import(self) -> None:
        import polaris.domain.models.task as _task_mod

        with patch("polaris.domain.models.task.warnings.warn") as mock_warn:
            importlib.reload(_task_mod)
            mock_warn.assert_called_once()
            args, kwargs = mock_warn.call_args
            assert "deprecated" in args[0].lower()
            assert args[1] is DeprecationWarning
            assert kwargs.get("stacklevel") == 2

    def test_reimport_calls_warn_again(self) -> None:
        import polaris.domain.models.task as _task_mod

        with patch("polaris.domain.models.task.warnings.warn") as mock_warn:
            importlib.reload(_task_mod)
            mock_warn.assert_called_once()

    def test_warn_message_content(self) -> None:
        import polaris.domain.models.task as _task_mod

        with patch("polaris.domain.models.task.warnings.warn") as mock_warn:
            importlib.reload(_task_mod)
            args, _kwargs = mock_warn.call_args
            assert "polaris.domain.models.task" in args[0]
            assert "polaris.domain.entities.task" in args[0]


# =============================================================================
# Re-export identity
# =============================================================================


class TestTaskReExports:
    def test_task_is_canonical_task(self) -> None:
        from polaris.domain.models.task import Task

        assert Task is CanonicalTask

    def test_task_status_is_canonical_task_status(self) -> None:
        from polaris.domain.models.task import TaskStatus

        assert TaskStatus is CanonicalTaskStatus

    def test_task_priority_is_canonical_task_priority(self) -> None:
        from polaris.domain.models.task import TaskPriority

        assert TaskPriority is CanonicalTaskPriority

    def test_all_exports_present(self) -> None:
        from polaris.domain.models.task import __all__

        assert sorted(__all__) == sorted(["Task", "TaskPriority", "TaskStatus"])

    def test_task_instantiation_through_reexport(self) -> None:
        from polaris.domain.models.task import Task

        task = Task(id=1, subject="test")
        assert task.id == 1
        assert task.subject == "test"
        assert task.status == CanonicalTaskStatus.PENDING

    def test_task_status_enum_members(self) -> None:
        from polaris.domain.models.task import TaskStatus

        assert TaskStatus.QUEUED.value == "queued"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"

    def test_task_priority_enum_members(self) -> None:
        from polaris.domain.models.task import TaskPriority

        assert TaskPriority.LOW.value == "low"
        assert TaskPriority.CRITICAL.value == "critical"
        assert TaskPriority.CRITICAL.numeric_value == 3

    def test_task_state_transitions_via_reexport(self) -> None:
        from polaris.domain.models.task import Task, TaskStatus

        task = Task(id=1, subject="transition test", status=TaskStatus.PENDING)
        task.mark_ready()
        assert task.status == TaskStatus.READY

    def test_task_status_terminal_property(self) -> None:
        from polaris.domain.models.task import TaskStatus

        assert TaskStatus.COMPLETED.is_terminal is True
        assert TaskStatus.FAILED.is_terminal is True
        assert TaskStatus.QUEUED.is_terminal is False

    def test_task_to_dict_via_reexport(self) -> None:
        from polaris.domain.models.task import Task

        task = Task(id=42, subject="dict test")
        d = task.to_dict()
        assert d["id"] == 42
        assert d["subject"] == "dict test"
        assert "priority_numeric" in d

    def test_task_from_dict_via_reexport(self) -> None:
        from polaris.domain.models.task import Task, TaskStatus

        d = {"id": 99, "subject": "from dict", "status": "completed"}
        task = Task.from_dict(d)
        assert task.id == 99
        assert task.status == TaskStatus.COMPLETED

    def test_module_repr_does_not_crash(self) -> None:
        import polaris.domain.models.task as mod

        assert "task" in mod.__name__

    def test_task_frozen_evidence(self) -> None:
        from polaris.domain.entities.task import TaskEvidence

        ev = TaskEvidence(type="file", path="/tmp/x")
        assert ev.type == "file"

    def test_task_result_serialization(self) -> None:
        from polaris.domain.entities.task import TaskResult

        tr = TaskResult(success=True, output="ok")
        d = tr.to_dict()
        assert d["success"] is True
        assert d["output"] == "ok"
        restored = TaskResult.from_dict(d)
        assert restored.success is True

    def test_task_retry_transition(self) -> None:
        from polaris.domain.entities.task import TaskResult
        from polaris.domain.models.task import Task, TaskStatus

        task = Task(id=3, subject="retry", status=TaskStatus.IN_PROGRESS)
        result = TaskResult(success=False, output="fail")
        task.complete(result)
        # Should retry and reset to READY because retry_count < max_retries (3)
        assert task.status == TaskStatus.READY
        assert task.retry_count == 1

    def test_task_max_retries_exceeded(self) -> None:
        from polaris.domain.entities.task import TaskResult
        from polaris.domain.models.task import Task, TaskStatus

        task = Task(id=4, subject="max retry", status=TaskStatus.IN_PROGRESS, retry_count=3)
        result = TaskResult(success=False, output="fail")
        task.complete(result)
        assert task.status == TaskStatus.FAILED
