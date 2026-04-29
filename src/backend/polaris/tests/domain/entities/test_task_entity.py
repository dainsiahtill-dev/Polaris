"""Tests for polaris.domain.entities.task.

Focuses on gaps not covered in test_entities.py:
- TaskEvidence and TaskResult value objects
- from_dict edge cases (float id, invalid priority, legacy blockedBy, non-list blocked_by)
- complete() with evidence handling
- to_dict roundtrip and None value handling
- Additional state transition edge cases
"""

from __future__ import annotations

import pytest
from polaris.domain.entities.task import (
    Task,
    TaskEvidence,
    TaskPriority,
    TaskResult,
    TaskStateError,
    TaskStatus,
    _now_seconds,
)

# =============================================================================
# TaskEvidence
# =============================================================================


class TestTaskEvidence:
    def test_minimal(self) -> None:
        ev = TaskEvidence(type="file")
        assert ev.type == "file"
        assert ev.path is None
        assert ev.content is None
        assert ev.metadata == {}

    def test_full(self) -> None:
        ev = TaskEvidence(
            type="test_result",
            path="/tmp/result.json",
            content='{"pass": true}',
            metadata={"suite": "unit"},
        )
        assert ev.type == "test_result"
        assert ev.path == "/tmp/result.json"
        assert ev.content == '{"pass": true}'
        assert ev.metadata == {"suite": "unit"}

    def test_immutability(self) -> None:
        ev = TaskEvidence(type="file")
        with pytest.raises(AttributeError):
            ev.type = "log"  # type: ignore[misc]


# =============================================================================
# TaskResult
# =============================================================================


class TestTaskResult:
    def test_defaults(self) -> None:
        result = TaskResult(success=True)
        assert result.success is True
        assert result.output == ""
        assert result.exit_code == 0
        assert result.duration_ms == 0
        assert result.evidence == ()
        assert result.error is None

    def test_full_construction(self) -> None:
        ev = TaskEvidence(type="log", path="/tmp/log.txt")
        result = TaskResult(
            success=False,
            output="failed",
            exit_code=1,
            duration_ms=1000,
            evidence=(ev,),
            error="RuntimeError",
        )
        assert result.success is False
        assert result.output == "failed"
        assert result.exit_code == 1
        assert result.duration_ms == 1000
        assert len(result.evidence) == 1
        assert result.error == "RuntimeError"

    def test_to_dict_minimal(self) -> None:
        result = TaskResult(success=True)
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == ""
        assert d["exit_code"] == 0
        assert d["duration_ms"] == 0
        assert d["evidence"] == []
        assert d["error"] is None

    def test_to_dict_with_evidence(self) -> None:
        ev = TaskEvidence(type="file", path="/tmp/f.txt", metadata={"k": "v"})
        result = TaskResult(success=True, evidence=(ev,))
        d = result.to_dict()
        assert len(d["evidence"]) == 1
        assert d["evidence"][0]["type"] == "file"
        assert d["evidence"][0]["path"] == "/tmp/f.txt"
        assert d["evidence"][0]["metadata"] == {"k": "v"}

    def test_from_dict_roundtrip(self) -> None:
        original = TaskResult(
            success=False,
            output="err",
            exit_code=2,
            duration_ms=500,
            evidence=(TaskEvidence(type="log", path="/tmp/l.txt"),),
            error="Oops",
        )
        d = original.to_dict()
        restored = TaskResult.from_dict(d)
        assert restored.success is False
        assert restored.output == "err"
        assert restored.exit_code == 2
        assert restored.duration_ms == 500
        assert len(restored.evidence) == 1
        assert restored.evidence[0].type == "log"
        assert restored.error == "Oops"

    def test_from_dict_empty_evidence(self) -> None:
        result = TaskResult.from_dict({"success": True, "evidence": []})
        assert result.evidence == ()

    def test_from_dict_missing_fields(self) -> None:
        result = TaskResult.from_dict({"success": True})
        assert result.output == ""
        assert result.exit_code == 0
        assert result.duration_ms == 0
        assert result.evidence == ()
        assert result.error is None

    def test_from_dict_missing_evidence(self) -> None:
        result = TaskResult.from_dict({"success": True})
        assert result.evidence == ()


# =============================================================================
# TaskPriority edge cases
# =============================================================================


class TestTaskPriorityEdgeCases:
    def test_numeric_values(self) -> None:
        assert TaskPriority.LOW.numeric_value == 0
        assert TaskPriority.MEDIUM.numeric_value == 1
        assert TaskPriority.HIGH.numeric_value == 2
        assert TaskPriority.CRITICAL.numeric_value == 3

    def test_invalid_value_fallback(self) -> None:
        # The numeric_value property uses .get(self.value, 1)
        # We can"t construct an invalid TaskPriority directly (Enum),
        # but we verify the mapping is complete.
        mapping = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        for prio in TaskPriority:
            assert prio.numeric_value == mapping[prio.value]


# =============================================================================
# Task creation edge cases
# =============================================================================


class TestTaskCreationEdgeCases:
    def test_str_id(self) -> None:
        t = Task(id="task-1", subject="test")
        assert t.id == "task-1"

    def test_int_id(self) -> None:
        t = Task(id=42, subject="test")
        assert t.id == 42

    def test_default_status(self) -> None:
        t = Task(id=1, subject="test")
        assert t.status == TaskStatus.PENDING

    def test_default_priority(self) -> None:
        t = Task(id=1, subject="test")
        assert t.priority == TaskPriority.MEDIUM

    def test_default_retries(self) -> None:
        t = Task(id=1, subject="test")
        assert t.max_retries == 3
        assert t.retry_count == 0

    def test_empty_lists_default(self) -> None:
        t = Task(id=1, subject="test")
        assert t.blocked_by == []
        assert t.blocks == []
        assert t.constraints == []
        assert t.acceptance_criteria == []
        assert t.evidence_refs == []
        assert t.tags == []

    def test_none_command(self) -> None:
        t = Task(id=1, subject="test")
        assert t.command is None


# =============================================================================
# Computed properties
# =============================================================================


class TestComputedProperties:
    def test_is_terminal_completed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        assert t.is_terminal is True

    def test_is_terminal_failed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.FAILED)
        assert t.is_terminal is True

    def test_is_terminal_cancelled(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CANCELLED)
        assert t.is_terminal is True

    def test_is_terminal_timeout(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.TIMEOUT)
        assert t.is_terminal is True

    def test_is_terminal_pending(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        assert t.is_terminal is False

    def test_is_blocked_with_deps(self) -> None:
        t = Task(id=1, subject="test", blocked_by=[2], status=TaskStatus.PENDING)
        assert t.is_blocked is True

    def test_is_blocked_not_blocked(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        assert t.is_blocked is False

    def test_is_blocked_empty_deps(self) -> None:
        t = Task(id=1, subject="test", blocked_by=[], status=TaskStatus.PENDING)
        assert t.is_blocked is False

    def test_is_claimable_ready(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        assert t.is_claimable is True

    def test_is_claimable_when_blocked_by_zero(self) -> None:
        # blocked_by containing only 0 is treated as no block
        t = Task(id=1, subject="test", status=TaskStatus.READY, blocked_by=[0])
        assert t.is_claimable is True

    def test_is_claimable_when_blocked(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY, blocked_by=[2])
        assert t.is_claimable is False

    def test_is_claimable_when_claimed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CLAIMED)
        assert t.is_claimable is False

    def test_is_claimable_not_ready(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        assert t.is_claimable is False

    def test_result_none(self) -> None:
        t = Task(id=1, subject="test")
        assert t.result is None

    def test_result_set(self) -> None:
        result = TaskResult(success=True)
        t = Task(id=1, subject="test", _result=result)
        assert t.result == result


# =============================================================================
# State transitions — additional edge cases
# =============================================================================


class TestStateTransitionsEdgeCases:
    def test_mark_ready_from_pending(self) -> None:
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert t.status == TaskStatus.READY

    def test_mark_ready_from_ready_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        with pytest.raises(TaskStateError):
            t.mark_ready()

    def test_mark_ready_from_in_progress_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        with pytest.raises(TaskStateError):
            t.mark_ready()

    def test_claim_success(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        t.claim("worker-1")
        assert t.status == TaskStatus.CLAIMED
        assert t.claimed_by == "worker-1"
        assert t.claimed_at is not None

    def test_claim_already_claimed_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CLAIMED, claimed_by="w1")
        with pytest.raises(TaskStateError):
            t.claim("worker-2")

    def test_claim_not_ready_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        with pytest.raises(TaskStateError):
            t.claim("worker-1")

    def test_start_from_claimed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CLAIMED, claimed_by="w1")
        t.start()
        assert t.status == TaskStatus.IN_PROGRESS
        assert t.started_at is not None

    def test_start_from_in_progress_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        with pytest.raises(TaskStateError):
            t.start()

    def test_start_from_ready_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        with pytest.raises(TaskStateError):
            t.start()

    def test_complete_from_claimed_success(self) -> None:
        # complete() accepts CLAIMED as well as IN_PROGRESS
        t = Task(id=1, subject="test", status=TaskStatus.CLAIMED, claimed_by="w1")
        result = TaskResult(success=True, output="done")
        t.complete(result)
        assert t.status == TaskStatus.COMPLETED
        assert t.result_summary == "done"
        assert t.completed_at is not None

    def test_complete_from_in_progress_success(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        result = TaskResult(success=True, output="done")
        t.complete(result)
        assert t.status == TaskStatus.COMPLETED

    def test_complete_failure_no_retries(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS, max_retries=0)
        result = TaskResult(success=False)
        t.complete(result)
        assert t.status == TaskStatus.FAILED

    def test_complete_failure_with_evidence_paths(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        ev1 = TaskEvidence(type="file", path="/tmp/a.txt")
        ev2 = TaskEvidence(type="log", path=None)
        result = TaskResult(success=True, evidence=(ev1, ev2))
        t.complete(result)
        assert t.evidence_refs == ["/tmp/a.txt"]

    def test_complete_failure_sets_error_message(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS, max_retries=0)
        result = TaskResult(success=False, error="Something broke")
        t.complete(result)
        assert t.error_message == "Something broke"

    def test_complete_from_pending_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        with pytest.raises(TaskStateError):
            t.complete(TaskResult(success=True))

    def test_complete_from_ready_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        with pytest.raises(TaskStateError):
            t.complete(TaskResult(success=True))

    def test_complete_from_completed_raises(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        with pytest.raises(TaskStateError):
            t.complete(TaskResult(success=True))

    def test_cancel_from_pending(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        t.cancel()
        assert t.status == TaskStatus.CANCELLED
        assert t.completed_at is not None

    def test_cancel_from_ready(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        t.cancel()
        assert t.status == TaskStatus.CANCELLED

    def test_cancel_from_in_progress(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        t.cancel()
        assert t.status == TaskStatus.CANCELLED

    def test_cancel_terminal_raises(self) -> None:
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT):
            t = Task(id=1, subject="test", status=status)
            with pytest.raises(TaskStateError):
                t.cancel()

    def test_timeout_from_in_progress(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.IN_PROGRESS)
        t.timeout_task()
        assert t.status == TaskStatus.TIMEOUT
        assert t.error_message == "Execution exceeded timeout limit"
        assert t.completed_at is not None

    def test_timeout_from_claimed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CLAIMED)
        t.timeout_task()
        assert t.status == TaskStatus.TIMEOUT

    def test_timeout_terminal_raises(self) -> None:
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT):
            t = Task(id=1, subject="test", status=status)
            with pytest.raises(TaskStateError):
                t.timeout_task()

    def test_reopen_completed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        t.reopen()
        assert t.status == TaskStatus.PENDING
        assert t.claimed_by is None
        assert t.claimed_at is None
        assert t.started_at is None
        assert t.completed_at is None
        assert t._result is None

    def test_reopen_failed(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.FAILED)
        t.reopen()
        assert t.status == TaskStatus.PENDING

    def test_reopen_cancelled(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.CANCELLED)
        t.reopen()
        assert t.status == TaskStatus.PENDING

    def test_reopen_timeout(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.TIMEOUT)
        t.reopen()
        assert t.status == TaskStatus.PENDING

    def test_reopen_with_blocked_by(self) -> None:
        t = Task(id=1, subject="test", status=TaskStatus.FAILED, blocked_by=[2])
        t.reopen()
        assert t.status == TaskStatus.BLOCKED

    def test_reopen_non_terminal_raises(self) -> None:
        for status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            t = Task(id=1, subject="test", status=status)
            with pytest.raises(TaskStateError):
                t.reopen()

    def test_resolve_dependency_present(self) -> None:
        t = Task(id=1, subject="test", blocked_by=[2, 3, 4])
        t.resolve_dependency(3)
        assert t.blocked_by == [2, 4]

    def test_resolve_dependency_missing(self) -> None:
        t = Task(id=1, subject="test", blocked_by=[2])
        t.resolve_dependency(99)
        assert t.blocked_by == [2]

    def test_resolve_dependency_empty(self) -> None:
        t = Task(id=1, subject="test", blocked_by=[])
        t.resolve_dependency(1)
        assert t.blocked_by == []


# =============================================================================
# Serialization edge cases
# =============================================================================


class TestSerializationEdgeCases:
    def test_to_dict_preserves_none_fields(self) -> None:
        t = Task(id=1, subject="test")
        d = t.to_dict()
        assert d["claimed_by"] is None
        assert d["started_at"] is None
        assert d["completed_at"] is None
        assert d["claimed_at"] is None
        assert d["result"] is None
        assert d["command"] is None
        assert d["working_directory"] is None
        assert d["error_message"] is None

    def test_to_dict_sets_priority_numeric(self) -> None:
        t = Task(id=1, subject="test", priority=TaskPriority.HIGH)
        d = t.to_dict()
        assert d["priority_numeric"] == 2

    def test_to_dict_with_result(self) -> None:
        result = TaskResult(success=True, output="ok")
        t = Task(id=1, subject="test", _result=result)
        d = t.to_dict()
        assert d["result"] == result.to_dict()

    def test_from_dict_float_id(self) -> None:
        d = {"id": 1.5, "subject": "test"}
        t = Task.from_dict(d)
        assert t.id == 1

    def test_from_dict_str_id(self) -> None:
        d = {"id": "abc", "subject": "test"}
        t = Task.from_dict(d)
        assert t.id == "abc"

    def test_from_dict_int_id(self) -> None:
        d = {"id": 42, "subject": "test"}
        t = Task.from_dict(d)
        assert t.id == 42

    def test_from_dict_invalid_status_fallback(self) -> None:
        d = {"id": 1, "subject": "test", "status": "not_a_status"}
        t = Task.from_dict(d)
        assert t.status == TaskStatus.PENDING

    def test_from_dict_invalid_priority_fallback(self) -> None:
        d = {"id": 1, "subject": "test", "priority": "not_a_priority"}
        t = Task.from_dict(d)
        assert t.priority == TaskPriority.MEDIUM

    def test_from_dict_taskstatus_object(self) -> None:
        d = {"id": 1, "subject": "test", "status": TaskStatus.READY}
        t = Task.from_dict(d)
        assert t.status == TaskStatus.READY

    def test_from_dict_taskpriority_object(self) -> None:
        d = {"id": 1, "subject": "test", "priority": TaskPriority.CRITICAL}
        t = Task.from_dict(d)
        assert t.priority == TaskPriority.CRITICAL

    def test_from_dict_legacy_blockedby(self) -> None:
        d = {"id": 1, "subject": "test", "blockedBy": [10, 20]}
        t = Task.from_dict(d)
        assert t.blocked_by == [10, 20]

    def test_from_dict_non_list_blocked_by(self) -> None:
        d = {"id": 1, "subject": "test", "blocked_by": "not_a_list"}
        t = Task.from_dict(d)
        assert t.blocked_by == []

    def test_from_dict_with_result_dict(self) -> None:
        d = {
            "id": 1,
            "subject": "test",
            "result": {
                "success": True,
                "output": "done",
                "evidence": [{"type": "file", "path": "/tmp/f.txt"}],
            },
        }
        t = Task.from_dict(d)
        assert t._result is not None
        assert t._result.success is True
        assert t._result.output == "done"
        assert len(t._result.evidence) == 1
        assert t._result.evidence[0].type == "file"

    def test_from_dict_result_none(self) -> None:
        d = {"id": 1, "subject": "test", "result": None}
        t = Task.from_dict(d)
        assert t._result is None

    def test_from_dict_result_not_dict(self) -> None:
        d = {"id": 1, "subject": "test", "result": "not_a_dict"}
        t = Task.from_dict(d)
        assert t._result is None

    def test_from_dict_missing_optional_fields(self) -> None:
        d = {"id": 1, "subject": "test"}
        t = Task.from_dict(d)
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.MEDIUM
        assert t.blocked_by == []
        assert t.blocks == []
        assert t.owner == ""
        assert t.assignee == ""
        assert t.claimed_by is None
        assert t.command is None
        assert t.working_directory is None
        assert t.timeout_seconds == 300
        assert t.max_retries == 3
        assert t.retry_count == 0
        assert t.created_at == 0.0
        assert t.started_at is None
        assert t.completed_at is None
        assert t.claimed_at is None
        assert t.result_summary == ""
        assert t.error_message is None
        assert t.evidence_refs == []
        assert t.tags == []
        assert t.metadata == {}

    def test_roundtrip(self) -> None:
        original = Task(
            id=99,
            subject="roundtrip",
            description="desc",
            status=TaskStatus.READY,
            priority=TaskPriority.HIGH,
            blocked_by=[1, 2],
            blocks=[3],
            owner="alice",
            assignee="bob",
            role="dev",
            constraints=["c1"],
            acceptance_criteria=["ac1"],
            command="python test.py",
            working_directory="/tmp",
            timeout_seconds=60,
            max_retries=5,
            retry_count=1,
            created_at=123.0,
            tags=["urgent"],
            metadata={"key": "val"},
        )
        d = original.to_dict()
        restored = Task.from_dict(d)
        assert restored.id == original.id
        assert restored.subject == original.subject
        assert restored.description == original.description
        assert restored.status == original.status
        assert restored.priority == original.priority
        assert restored.blocked_by == original.blocked_by
        assert restored.blocks == original.blocks
        assert restored.owner == original.owner
        assert restored.assignee == original.assignee
        assert restored.role == original.role
        assert restored.constraints == original.constraints
        assert restored.acceptance_criteria == original.acceptance_criteria
        assert restored.command == original.command
        assert restored.working_directory == original.working_directory
        assert restored.timeout_seconds == original.timeout_seconds
        assert restored.max_retries == original.max_retries
        assert restored.retry_count == original.retry_count
        assert restored.created_at == original.created_at
        assert restored.tags == original.tags
        assert restored.metadata == original.metadata


# =============================================================================
# _now_seconds helper
# =============================================================================


class TestNowSeconds:
    def test_returns_positive_float(self) -> None:
        now = _now_seconds()
        assert isinstance(now, float)
        assert now > 0

    def test_increases_over_time(self) -> None:
        import time

        t1 = _now_seconds()
        time.sleep(0.01)
        t2 = _now_seconds()
        assert t2 > t1
