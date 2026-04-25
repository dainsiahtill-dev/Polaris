"""Tests for polaris.cells.director.tasking.public.contracts.

Covers all frozen dataclasses, validation logic in __post_init__,
and the custom error class.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.tasking.public.contracts import (
    CancelTaskCommandV1,
    CreateTaskCommandV1,
    DirectorTaskingError,
    TaskCreatedResultV1,
    TaskResultQueryV1,
    TaskResultResultV1,
    TaskStatusQueryV1,
    TaskStatusResultV1,
)


class TestCreateTaskCommandV1:
    """Tests for CreateTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = CreateTaskCommandV1(subject="Fix bug", workspace="/ws")
        assert cmd.subject == "Fix bug"
        assert cmd.workspace == "/ws"
        assert cmd.description == ""
        assert cmd.command is None
        assert cmd.priority == "medium"
        assert cmd.blocked_by == []
        assert cmd.timeout_seconds is None
        assert cmd.metadata == {}

    def test_empty_subject_raises(self) -> None:
        with pytest.raises(ValueError, match="subject must be a non-empty string"):
            CreateTaskCommandV1(subject="", workspace="/ws")

    def test_whitespace_subject_raises(self) -> None:
        with pytest.raises(ValueError, match="subject must be a non-empty string"):
            CreateTaskCommandV1(subject="   ", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            CreateTaskCommandV1(subject="Fix bug", workspace="")

    def test_description_coerced_to_string(self) -> None:
        cmd = CreateTaskCommandV1(subject="Fix bug", workspace="/ws", description=None)  # type: ignore[arg-type]
        assert cmd.description == "None"

    def test_blocked_by_copied(self) -> None:
        original = ["task-1"]
        cmd = CreateTaskCommandV1(subject="Fix bug", workspace="/ws", blocked_by=original)
        assert cmd.blocked_by == ["task-1"]
        original.append("task-2")
        assert cmd.blocked_by == ["task-1"]

    def test_metadata_copied(self) -> None:
        original = {"key": "value"}
        cmd = CreateTaskCommandV1(subject="Fix bug", workspace="/ws", metadata=original)
        assert cmd.metadata == {"key": "value"}
        original["key"] = "changed"
        assert cmd.metadata == {"key": "value"}

    def test_none_metadata_becomes_empty_dict(self) -> None:
        cmd = CreateTaskCommandV1(subject="Fix bug", workspace="/ws", metadata=None)  # type: ignore[arg-type]
        assert cmd.metadata == {}


class TestCancelTaskCommandV1:
    """Tests for CancelTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = CancelTaskCommandV1(task_id="t1", workspace="/ws")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/ws"
        assert cmd.reason == ""

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            CancelTaskCommandV1(task_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            CancelTaskCommandV1(task_id="t1", workspace="")


class TestTaskStatusQueryV1:
    """Tests for TaskStatusQueryV1."""

    def test_valid_query(self) -> None:
        q = TaskStatusQueryV1(workspace="/ws")
        assert q.workspace == "/ws"
        assert q.task_id is None
        assert q.status is None
        assert q.limit == 50

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            TaskStatusQueryV1(workspace="")


class TestTaskResultQueryV1:
    """Tests for TaskResultQueryV1."""

    def test_valid_query(self) -> None:
        q = TaskResultQueryV1(task_id="t1", workspace="/ws")
        assert q.task_id == "t1"
        assert q.workspace == "/ws"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskResultQueryV1(task_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            TaskResultQueryV1(task_id="t1", workspace="")


class TestTaskCreatedResultV1:
    """Tests for TaskCreatedResultV1."""

    def test_valid_result(self) -> None:
        r = TaskCreatedResultV1(ok=True, task_id="t1", workspace="/ws", subject="Fix bug")
        assert r.ok is True
        assert r.status == "pending"

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            TaskCreatedResultV1(ok=False, task_id="t1", workspace="/ws", subject="Fix bug")

    def test_failed_result_with_error_code_ok(self) -> None:
        r = TaskCreatedResultV1(ok=False, task_id="t1", workspace="/ws", subject="Fix bug", error_code="E1")
        assert r.ok is False
        assert r.error_code == "E1"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskCreatedResultV1(ok=True, task_id="", workspace="/ws", subject="Fix bug")

    def test_empty_subject_raises(self) -> None:
        with pytest.raises(ValueError, match="subject must be a non-empty string"):
            TaskCreatedResultV1(ok=True, task_id="t1", workspace="/ws", subject="")


class TestTaskStatusResultV1:
    """Tests for TaskStatusResultV1."""

    def test_valid_result(self) -> None:
        r = TaskStatusResultV1(ok=True, workspace="/ws")
        assert r.tasks == []
        assert r.count == 0

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            TaskStatusResultV1(ok=True, workspace="")


class TestTaskResultResultV1:
    """Tests for TaskResultResultV1."""

    def test_valid_result(self) -> None:
        r = TaskResultResultV1(ok=True, task_id="t1", workspace="/ws")
        assert r.success is None
        assert r.output == ""
        assert r.error is None
        assert r.duration_ms is None
        assert r.evidence == []

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            TaskResultResultV1(ok=False, task_id="t1", workspace="/ws")

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskResultResultV1(ok=True, task_id="", workspace="/ws")


class TestDirectorTaskingError:
    """Tests for DirectorTaskingError."""

    def test_default_code(self) -> None:
        err = DirectorTaskingError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.code == "director_tasking_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = DirectorTaskingError("boom", code="E99", details={"key": "val"})
        assert err.code == "E99"
        assert err.details == {"key": "val"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            DirectorTaskingError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            DirectorTaskingError("boom", code="")

    def test_none_details_becomes_empty_dict(self) -> None:
        err = DirectorTaskingError("boom", details=None)
        assert err.details == {}
