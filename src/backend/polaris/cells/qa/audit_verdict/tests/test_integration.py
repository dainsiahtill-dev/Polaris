"""Integration tests for qa.audit_verdict main execution paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.qa.audit_verdict.internal.qa_agent import (
    QAAgent,
    ReviewRecord,
    ReviewStatus,
)
from polaris.cells.qa.audit_verdict.internal.qa_service import (
    PathSecurityError,
    QAConfig,
    QAService,
)
from polaris.cells.qa.audit_verdict.public.contracts import (
    QaAuditError,
    QaAuditResultV1,
    QaVerdictIssuedEventV1,
    RunQaAuditCommandV1,
)

if TYPE_CHECKING:
    from pathlib import Path

# ─── QAService integration tests ─────────────────────────────────────────────


@pytest.fixture
def qa_service(tmp_path: Path) -> QAService:
    config = QAConfig(workspace=str(tmp_path), enable_auto_audit=False)
    service = QAService(config)
    return service


class TestQAServiceAuditTask:
    """End-to-end audit_task() path."""

    @pytest.mark.asyncio
    async def test_audit_task_no_files(self, qa_service: QAService) -> None:
        result = await qa_service.audit_task(
            task_id="t-001",
            task_subject="Empty task",
            changed_files=[],
        )
        assert result.verdict == "PASS"
        assert result.issues == []
        assert result.audit_id.startswith("audit-")

    @pytest.mark.asyncio
    async def test_audit_task_security_violation_blocked(self, qa_service: QAService) -> None:
        result = await qa_service.audit_task(
            task_id="t-002",
            task_subject="Security test",
            changed_files=["../etc/passwd", "../../secrets/key"],
        )
        assert result.verdict == "FAIL"
        assert len(result.issues) == 2
        assert all(
            issue.get("severity") == "error" and "Security violation" in issue.get("message", "")
            for issue in result.issues
        )

    @pytest.mark.asyncio
    async def test_audit_task_null_byte_path_rejected(self, qa_service: QAService) -> None:
        result = await qa_service.audit_task(
            task_id="t-003",
            task_subject="Null byte test",
            changed_files=["safe\0file.py"],
        )
        assert result.verdict == "FAIL"
        assert any("null byte" in issue.get("message", "").lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_audit_task_valid_python_file_audited(self, qa_service: QAService) -> None:
        # Create a valid Python file in the workspace
        py_file = qa_service._workspace / "hello.py"
        py_file.write_text("x = 1\nprint(x)\n", encoding="utf-8")

        result = await qa_service.audit_task(
            task_id="t-004",
            task_subject="Valid Python",
            changed_files=["hello.py"],
        )
        assert result.verdict == "PASS"
        assert result.metrics["files_audited"] == 1

    @pytest.mark.asyncio
    async def test_audit_task_python_syntax_error_detected(self, qa_service: QAService) -> None:
        bad_file = qa_service._workspace / "bad.py"
        bad_file.write_text("def foo(\n    pass\n", encoding="utf-8")

        result = await qa_service.audit_task(
            task_id="t-005",
            task_subject="Syntax error",
            changed_files=["bad.py"],
        )
        assert result.verdict == "FAIL"
        assert any("Syntax error" in issue.get("message", "") for issue in result.issues)

    @pytest.mark.asyncio
    async def test_audit_task_empty_file_reported(self, qa_service: QAService) -> None:
        empty_file = qa_service._workspace / "empty.py"
        empty_file.write_text("", encoding="utf-8")

        result = await qa_service.audit_task(
            task_id="t-006",
            task_subject="Empty file",
            changed_files=["empty.py"],
        )
        assert any("empty" in issue.get("message", "").lower() for issue in result.issues)


class TestQAServicePathValidation:
    """Path security boundary tests."""

    def test_absolute_path_outside_workspace_rejected(self, qa_service: QAService) -> None:
        with pytest.raises(PathSecurityError, match="traversal"):
            qa_service._validate_path("/etc/passwd")

    def test_traversal_sequence_rejected(self, qa_service: QAService) -> None:
        with pytest.raises(PathSecurityError):
            qa_service._validate_path("../secrets/id_rsa")

    def test_valid_relative_path(self, qa_service: QAService) -> None:
        result = qa_service._validate_path("src/main.py")
        assert result.is_absolute()

    def test_empty_path_raises(self, qa_service: QAService) -> None:
        with pytest.raises(ValueError, match="empty"):
            qa_service._validate_path("")

    def test_is_safe_filename_valid(self, qa_service: QAService) -> None:
        assert qa_service._is_safe_filename("main.py") is True
        assert qa_service._is_safe_filename("hello_world_test.py") is True

    def test_is_safe_filename_invalid(self, qa_service: QAService) -> None:
        assert qa_service._is_safe_filename("../etc/passwd") is False
        assert qa_service._is_safe_filename("") is False
        assert qa_service._is_safe_filename(".") is False
        assert qa_service._is_safe_filename("..") is False


# ─── QAAgent integration tests ───────────────────────────────────────────────


@pytest.fixture
def qa_agent(tmp_path: Path) -> QAAgent:
    agent = QAAgent(workspace=str(tmp_path))
    agent.initialize()
    return agent


class TestQAAgentReviewLifecycle:
    """Full review lifecycle: submit → approve → reject → get."""

    def test_submit_review_returns_ok(self, qa_agent: QAAgent) -> None:
        result = qa_agent._tool_submit_review(
            task_id="t-100",
            title="Feature review",
            priority="high",
            content="Review the new feature",
        )
        assert result["ok"] is True
        assert "review" in result
        assert result["review"]["task_id"] == "t-100"
        assert result["review"]["status"] == "pending"

    def test_get_review_found(self, qa_agent: QAAgent) -> None:
        submit = qa_agent._tool_submit_review(task_id="t-101", title="Get test")
        review_id = submit["review"]["review_id"]

        result = qa_agent._tool_get_review(review_id)
        assert result["ok"] is True
        assert result["review"]["review_id"] == review_id

    def test_get_review_not_found(self, qa_agent: QAAgent) -> None:
        result = qa_agent._tool_get_review("does-not-exist")
        assert result["ok"] is False
        assert result["error_code"] == "REVIEW_NOT_FOUND"

    def test_approve_review(self, qa_agent: QAAgent) -> None:
        submit = qa_agent._tool_submit_review(task_id="t-102", title="Approve me")
        review_id = submit["review"]["review_id"]

        result = qa_agent._tool_approve_review(review_id, feedback="LGTM")
        assert result["ok"] is True
        assert result["review"]["status"] == "approved"
        assert result["review"]["feedback"] == "LGTM"

    def test_reject_review(self, qa_agent: QAAgent) -> None:
        submit = qa_agent._tool_submit_review(task_id="t-103", title="Reject me")
        review_id = submit["review"]["review_id"]

        result = qa_agent._tool_reject_review(review_id, reason="Critical bugs", issues=["Bug-1", "Bug-2"])
        assert result["ok"] is True
        assert result["review"]["status"] == "rejected"
        assert result["review"]["issues"] == ["Bug-1", "Bug-2"]

    def test_request_revision(self, qa_agent: QAAgent) -> None:
        submit = qa_agent._tool_submit_review(task_id="t-104", title="Needs work")
        review_id = submit["review"]["review_id"]

        result = qa_agent._tool_request_revision(
            review_id,
            feedback="Please address the comments",
            suggestions=["Add tests", "Update docs"],
        )
        assert result["ok"] is True
        assert result["review"]["status"] == "revision_requested"

    def test_list_pending_reviews(self, qa_agent: QAAgent) -> None:
        qa_agent._tool_submit_review(task_id="t-105", title="P1")
        qa_agent._tool_submit_review(task_id="t-106", title="P2")
        qa_agent._tool_approve_review(qa_agent._tool_submit_review(task_id="t-107", title="P3")["review"]["review_id"])

        result = qa_agent._tool_list_pending_reviews()
        assert result["ok"] is True
        assert result["count"] == 2


class TestQAAgentStatus:
    """Agent status reporting."""

    def test_get_status_counts_reviews(self, qa_agent: QAAgent) -> None:
        qa_agent._tool_submit_review(task_id="t-200", title="A")
        qa_agent._tool_submit_review(task_id="t-201", title="B")
        submit3 = qa_agent._tool_submit_review(task_id="t-202", title="C")
        qa_agent._tool_approve_review(submit3["review"]["review_id"])

        status = qa_agent.get_status()
        assert status["reviews"]["total"] == 3
        assert status["reviews"]["pending"] == 2
        assert status["reviews"]["approved"] == 1


# ─── ReviewRecord serialization ───────────────────────────────────────────────


class TestReviewRecordSerialization:
    """ReviewRecord to_dict / from_dict round-trip."""

    def test_roundtrip(self) -> None:
        original = ReviewRecord(
            review_id="r-001",
            task_id="t-300",
            title="Round-trip test",
            priority="high",
            content="Test content",
            status=ReviewStatus.APPROVED,
            feedback="Looks good",
            issues=["issue-1"],
            suggestions=["suggest-1"],
            reviewed_by="QA",
        )
        as_dict = original.to_dict()
        restored = ReviewRecord.from_dict(as_dict)

        assert restored.review_id == original.review_id
        assert restored.task_id == original.task_id
        assert restored.status == original.status
        assert restored.priority == original.priority

    def test_from_dict_missing_fields_defaults(self) -> None:
        record = ReviewRecord.from_dict({})
        assert record.review_id == ""
        assert record.task_id == ""
        assert record.status == ReviewStatus.PENDING
        assert record.priority == "medium"


# ─── QaAuditResultV1 integration ─────────────────────────────────────────────


class TestQaAuditResultV1Integration:
    """QaAuditResultV1 as used in service responses."""

    def test_pass_result_structure(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t-400",
            workspace="/tmp",
            verdict="PASS",
            score=0.95,
            findings=("minor-style-warning",),  # type: ignore[arg-type]
        )
        assert r.verdict == "PASS"
        assert r.score == 0.95
        assert r.findings == ("minor-style-warning",)
        assert r.ok is True

    def test_fail_result_structure(self) -> None:
        r = QaAuditResultV1(
            ok=False,
            task_id="t-401",
            workspace="/tmp",
            verdict="FAIL",
            findings=("critical-bug", "missing-test"),  # type: ignore[arg-type]
        )
        assert r.ok is False
        assert r.verdict == "FAIL"
        assert len(r.findings) == 2

    def test_score_capped_at_one(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t-402",
            workspace="/tmp",
            verdict="PASS",
            score=1.0,
        )
        assert r.score == 1.0

    def test_result_serialization(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t-403",
            workspace="/tmp",
            verdict="PASS",
            score=0.8,
            suggestions=("Consider refactoring",),  # type: ignore[arg-type]
        )
        d = r.__dict__
        assert d["ok"] is True
        assert d["score"] == 0.8


# ─── QaVerdictIssuedEventV1 integration ─────────────────────────────────────


class TestQaVerdictIssuedEventV1Integration:
    """Event emission and consumption path."""

    def test_event_structure(self) -> None:
        evt = QaVerdictIssuedEventV1(
            event_id="evt-001",
            task_id="t-500",
            workspace="/tmp",
            verdict="PASS",
            issued_at="2026-01-01T00:00:00Z",
        )
        assert evt.verdict == "PASS"
        assert evt.event_id == "evt-001"
        assert evt.task_id == "t-500"

    def test_verdict_whitespace_stripped(self) -> None:
        evt = QaVerdictIssuedEventV1(
            event_id="evt-002",
            task_id="t-501",
            workspace="/tmp",
            verdict="  FAIL  ",
            issued_at="2026-01-01T00:00:00Z",
        )
        assert evt.verdict == "FAIL"


# ─── RunQaAuditCommandV1 integration ─────────────────────────────────────────


class TestRunQaAuditCommandV1Integration:
    """Command as it flows into the service layer."""

    def test_command_with_workspace_resolved_paths(self) -> None:
        cmd = RunQaAuditCommandV1(
            task_id="t-600",
            workspace="/repo",
            run_id="run-1",
            criteria={"min_score": 0.8},
            evidence_paths=("src/main.py", "tests/test_main.py"),  # type: ignore[arg-type]
        )
        assert cmd.task_id == "t-600"
        assert cmd.workspace == "/repo"
        assert cmd.run_id == "run-1"
        assert cmd.criteria["min_score"] == 0.8
        assert cmd.evidence_paths == ("src/main.py", "tests/test_main.py")


# ─── QaAuditError integration ────────────────────────────────────────────────


class TestQaAuditErrorIntegration:
    """Error as propagated across service boundary."""

    def test_error_attributes(self) -> None:
        err = QaAuditError(
            "File not found",
            code="FILE_NOT_FOUND",
            details={"path": "/tmp/missing.py"},
        )
        assert err.code == "FILE_NOT_FOUND"
        assert str(err) == "File not found"
        assert err.details["path"] == "/tmp/missing.py"

    def test_error_default_code(self) -> None:
        err = QaAuditError("Something went wrong")
        assert err.code == "qa_audit_error"


# ─── Additional coverage: protocol FSM, message handling, async paths ─────────


class TestQAAgentProtocolFSM:
    """Protocol FSM paths (approve_request / reject_request / list_pending_approvals)."""

    def test_list_pending_approvals_ok(self, qa_agent: QAAgent) -> None:
        # Covers the happy path where protocol_fsm.list_pending succeeds
        result = qa_agent._tool_list_pending_approvals()
        assert result["ok"] is True
        assert "requests" in result

    def test_approve_request_not_found(self, qa_agent: QAAgent) -> None:
        # FSM returns False when request_id is not found; tool returns error gracefully
        result = qa_agent._tool_approve_request("not-found", "OK")
        assert result["ok"] is False
        assert result["error_code"] == "PROTOCOL_ERROR"

    def test_reject_request_not_found(self, qa_agent: QAAgent) -> None:
        result = qa_agent._tool_reject_request("not-found", "Denied")
        assert result["ok"] is False
        assert result["error_code"] == "PROTOCOL_ERROR"


class TestQAAgentMessageHandling:
    """handle_message and run_cycle paths."""

    def test_handle_message_task_type(self, qa_agent: QAAgent) -> None:
        from polaris.cells.roles.runtime.public.contracts import AgentMessage, MessageType

        msg = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="Director",
            receiver="QA",
            payload={"task": {"id": "t-900", "title": "Test task"}},
        )
        response = qa_agent.handle_message(msg)
        assert response is not None
        assert response.type == MessageType.RESULT
        assert response.payload["action"] == "review_submitted"

    def test_handle_message_command_get_status(self, qa_agent: QAAgent) -> None:
        from polaris.cells.roles.runtime.public.contracts import AgentMessage, MessageType

        msg = AgentMessage.create(
            msg_type=MessageType.COMMAND,
            sender="PM",
            receiver="QA",
            payload={"command": "get_status"},
        )
        response = qa_agent.handle_message(msg)
        assert response is not None
        assert response.type == MessageType.EVENT

    def test_handle_message_unknown_type_returns_none(self, qa_agent: QAAgent) -> None:
        from polaris.cells.roles.runtime.public.contracts import AgentMessage, MessageType

        msg = AgentMessage.create(
            msg_type=MessageType.HEARTBEAT,
            sender="PM",
            receiver="QA",
            payload={},
        )
        assert qa_agent.handle_message(msg) is None

    def test_run_cycle_no_message(self, qa_agent: QAAgent) -> None:
        # No message in queue -> returns False (no-op, no error)
        assert qa_agent.run_cycle() is False


class TestQAServiceAsyncPaths:
    """Async message-bus handlers and main loop."""

    @pytest.mark.asyncio
    async def test_on_task_completed_auto_audit_disabled(self, qa_service: QAService) -> None:
        from polaris.kernelone.events.message_bus import Message, MessageType

        qa_service.config.enable_auto_audit = False
        msg = Message(
            type=MessageType.TASK_COMPLETED,
            sender="director",
            payload={"task_id": "t-800"},
        )
        # Should not raise; just returns early
        await qa_service._on_task_completed(msg)

    @pytest.mark.asyncio
    async def test_on_file_written(self, qa_service: QAService) -> None:
        from polaris.kernelone.events.message_bus import Message, MessageType

        msg = Message(
            type=MessageType.FILE_WRITTEN,
            sender="director",
            payload={"path": "src/main.py"},
        )
        await qa_service._on_file_written(msg)  # should not raise

    @pytest.mark.asyncio
    async def test_start_stop_cycle(self, qa_service: QAService) -> None:
        await qa_service.start()
        assert qa_service._running is True
        await qa_service.stop()
        assert qa_service._running is False


class TestQAServiceAuditResultMetrics:
    """Verify metrics are correctly computed."""

    @pytest.mark.asyncio
    async def test_metrics_files_audited_and_rejected(self, qa_service: QAService) -> None:
        safe = qa_service._workspace / "good.py"
        safe.write_text("x = 1\n", encoding="utf-8")

        result = await qa_service.audit_task(
            task_id="t-700",
            task_subject="Metrics test",
            changed_files=["good.py", "../evil.py"],
        )
        assert result.metrics["files_audited"] == 1
        assert result.metrics["files_rejected"] == 1


class TestQAServiceStatus:
    """get_status and start/stop."""

    @pytest.mark.asyncio
    async def test_get_status_before_start(self, qa_service: QAService) -> None:
        status = qa_service.get_status()
        assert status["running"] is False
        assert "audit_ids" in status


class TestReviewStore:
    """ReviewStore CRUD paths."""

    def test_save_and_get(self) -> None:
        from polaris.cells.qa.audit_verdict.internal.qa_agent import (
            ReviewRecord,
            ReviewStore,
        )

        store = ReviewStore()
        record = ReviewRecord(review_id="r-1", task_id="t-1", title="Test")
        result = store.save(record)
        assert result.is_ok
        get_result = store.get("r-1")
        assert get_result.is_ok
        assert get_result.value is not None, "get_result.value should not be None"
        assert get_result.value.review_id == "r-1"

    def test_get_not_found(self) -> None:
        from polaris.cells.qa.audit_verdict.internal.qa_agent import ReviewStore

        store = ReviewStore()
        result = store.get("does-not-exist")
        assert result.is_err

    def test_get_by_task(self) -> None:
        from polaris.cells.qa.audit_verdict.internal.qa_agent import (
            ReviewRecord,
            ReviewStore,
        )

        store = ReviewStore()
        store.save(ReviewRecord(review_id="r-2", task_id="t-shared", title="A"))
        store.save(ReviewRecord(review_id="r-3", task_id="t-shared", title="B"))
        result = store.get_by_task("t-shared")
        assert result.is_ok
        assert len(result.value or []) == 2

    def test_get_pending(self) -> None:
        from polaris.cells.qa.audit_verdict.internal.qa_agent import (
            ReviewRecord,
            ReviewStatus,
            ReviewStore,
        )

        store = ReviewStore()
        pending = ReviewRecord(review_id="r-4", task_id="t-1", title="P", status=ReviewStatus.PENDING)
        approved = ReviewRecord(review_id="r-5", task_id="t-2", title="A", status=ReviewStatus.APPROVED)
        store.save(pending)
        store.save(approved)
        result = store.get_pending()
        assert result.is_ok
        assert len(result.value or []) == 1

    def test_list_all(self) -> None:
        from polaris.cells.qa.audit_verdict.internal.qa_agent import (
            ReviewRecord,
            ReviewStore,
        )

        store = ReviewStore()
        store.save(ReviewRecord(review_id="r-6", task_id="t-1", title="One"))
        store.save(ReviewRecord(review_id="r-7", task_id="t-2", title="Two"))
        result = store.list_all()
        assert result.is_ok
        assert len(result.value or []) == 2
