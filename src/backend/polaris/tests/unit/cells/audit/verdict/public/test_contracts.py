"""Tests for polaris.cells.audit.verdict.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.audit.verdict.public.contracts import (
    AuditVerdictError,
    AuditVerdictIssuedEventV1,
    AuditVerdictResultV1,
    IAuditVerdictService,
    QueryAuditVerdictV1,
    RunAuditVerdictCommandV1,
)


class TestRunAuditVerdictCommandV1:
    def test_valid_command(self) -> None:
        cmd = RunAuditVerdictCommandV1(
            workspace="/path/to/workspace",
            run_id="run-123",
        )
        assert cmd.workspace == "/path/to/workspace"
        assert cmd.run_id == "run-123"

    def test_command_with_task_id(self) -> None:
        cmd = RunAuditVerdictCommandV1(
            workspace="workspace",
            run_id="run-123",
            task_id="TASK-001",
        )
        assert cmd.task_id == "TASK-001"

    def test_command_with_metadata(self) -> None:
        cmd = RunAuditVerdictCommandV1(
            workspace="workspace",
            run_id="run-123",
            metadata={"key": "value"},
        )
        assert cmd.metadata["key"] == "value"

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RunAuditVerdictCommandV1(workspace="", run_id="run-123")
        assert "workspace" in str(exc_info.value).lower()

    def test_whitespace_only_workspace_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RunAuditVerdictCommandV1(workspace="   ", run_id="run-123")
        assert "workspace" in str(exc_info.value).lower()

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RunAuditVerdictCommandV1(workspace="workspace", run_id="")
        assert "run_id" in str(exc_info.value).lower()

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            RunAuditVerdictCommandV1(workspace="workspace", run_id="run-123", task_id="")
        assert "task_id" in str(exc_info.value).lower()


class TestQueryAuditVerdictV1:
    def test_valid_query(self) -> None:
        query = QueryAuditVerdictV1(workspace="workspace")
        assert query.workspace == "workspace"
        assert query.run_id is None
        assert query.include_artifacts is True

    def test_query_with_run_id(self) -> None:
        query = QueryAuditVerdictV1(workspace="workspace", run_id="run-123")
        assert query.run_id == "run-123"

    def test_query_with_task_id(self) -> None:
        query = QueryAuditVerdictV1(workspace="workspace", task_id="TASK-001")
        assert query.task_id == "TASK-001"

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            QueryAuditVerdictV1(workspace="")
        assert "workspace" in str(exc_info.value).lower()


class TestAuditVerdictIssuedEventV1:
    def test_valid_event(self) -> None:
        event = AuditVerdictIssuedEventV1(
            event_id="evt-123",
            workspace="workspace",
            run_id="run-123",
            verdict="PASS",
            issued_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-123"
        assert event.verdict == "PASS"

    def test_event_with_task_id_and_review_id(self) -> None:
        event = AuditVerdictIssuedEventV1(
            event_id="evt-123",
            workspace="workspace",
            run_id="run-123",
            verdict="PASS",
            issued_at="2024-01-01T00:00:00Z",
            task_id="TASK-001",
            review_id="review-456",
        )
        assert event.task_id == "TASK-001"
        assert event.review_id == "review-456"

    def test_empty_event_id_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictIssuedEventV1(
                event_id="",
                workspace="workspace",
                run_id="run-123",
                verdict="PASS",
                issued_at="2024-01-01T00:00:00Z",
            )
        assert "event_id" in str(exc_info.value).lower()

    def test_empty_verdict_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictIssuedEventV1(
                event_id="evt-123",
                workspace="workspace",
                run_id="run-123",
                verdict="",
                issued_at="2024-01-01T00:00:00Z",
            )
        assert "verdict" in str(exc_info.value).lower()


class TestAuditVerdictResultV1:
    def test_valid_pass_result(self) -> None:
        result = AuditVerdictResultV1(
            ok=True,
            status="completed",
            workspace="workspace",
            run_id="run-123",
            verdict="PASS",
        )
        assert result.ok is True
        assert result.verdict == "PASS"

    def test_valid_fail_result_with_error(self) -> None:
        result = AuditVerdictResultV1(
            ok=False,
            status="failed",
            workspace="workspace",
            run_id="run-123",
            error_code="ERR_001",
            error_message="Something went wrong",
        )
        assert result.ok is False
        assert result.error_code == "ERR_001"

    def test_failed_result_requires_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictResultV1(
                ok=False,
                status="failed",
                workspace="workspace",
                run_id="run-123",
            )
        assert "error_code or error_message" in str(exc_info.value)

    def test_empty_status_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictResultV1(
                ok=True,
                status="",
                workspace="workspace",
                run_id="run-123",
            )
        assert "status" in str(exc_info.value).lower()


class TestAuditVerdictError:
    def test_error_with_defaults(self) -> None:
        err = AuditVerdictError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "audit_verdict_error"
        assert err.details == {}

    def test_error_with_custom_code(self) -> None:
        err = AuditVerdictError("Custom error", code="CUSTOM_CODE")
        assert err.code == "CUSTOM_CODE"

    def test_error_with_details(self) -> None:
        err = AuditVerdictError("Error with details", details={"key": "value"})
        assert err.details["key"] == "value"

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictError("")
        assert "message" in str(exc_info.value).lower()

    def test_empty_code_rejected(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            AuditVerdictError("message", code="")
        assert "code" in str(exc_info.value).lower()


class TestIAuditVerdictService:
    def test_protocol_exists(self) -> None:
        """IAuditVerdictService is a valid runtime-checkable protocol."""
        assert callable(IAuditVerdictService)
        # Runtime-checkable protocol can be used with isinstance in certain contexts
        # This test just verifies the protocol class exists and is properly defined
