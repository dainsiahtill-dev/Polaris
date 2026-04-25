"""Tests for polaris.cells.audit.verdict.public.contracts.

Covers P0 data classes / enums / constants with validation logic.
"""

from __future__ import annotations

from typing import Any

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
    """RunAuditVerdictCommandV1 construction and validation."""

    def test_valid_command(self) -> None:
        cmd = RunAuditVerdictCommandV1(workspace="/ws", run_id="r1")
        assert cmd.workspace == "/ws"
        assert cmd.run_id == "r1"
        assert cmd.task_id is None
        assert cmd.metadata == {}

    def test_command_with_task_id(self) -> None:
        cmd = RunAuditVerdictCommandV1(workspace="/ws", run_id="r1", task_id="t1")
        assert cmd.task_id == "t1"

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            RunAuditVerdictCommandV1(workspace="", run_id="r1")

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            RunAuditVerdictCommandV1(workspace="/ws", run_id="")

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            RunAuditVerdictCommandV1(workspace="/ws", run_id="r1", task_id="")

    def test_metadata_is_copied(self) -> None:
        original: dict[str, Any] = {"key": "value"}
        cmd = RunAuditVerdictCommandV1(workspace="/ws", run_id="r1", metadata=original)
        original["key"] = "mutated"
        assert cmd.metadata["key"] == "value"


class TestQueryAuditVerdictV1:
    """QueryAuditVerdictV1 construction and validation."""

    def test_default_values(self) -> None:
        q = QueryAuditVerdictV1(workspace="/ws")
        assert q.workspace == "/ws"
        assert q.run_id is None
        assert q.task_id is None
        assert q.include_artifacts is True

    def test_custom_values(self) -> None:
        q = QueryAuditVerdictV1(workspace="/ws", run_id="r1", task_id="t1", include_artifacts=False)
        assert q.run_id == "r1"
        assert q.task_id == "t1"
        assert q.include_artifacts is False

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryAuditVerdictV1(workspace="")

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            QueryAuditVerdictV1(workspace="/ws", run_id="")

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            QueryAuditVerdictV1(workspace="/ws", task_id="")


class TestAuditVerdictIssuedEventV1:
    """AuditVerdictIssuedEventV1 construction and validation."""

    def test_valid_event(self) -> None:
        ev = AuditVerdictIssuedEventV1(
            event_id="e1",
            workspace="/ws",
            run_id="r1",
            verdict="PASS",
            issued_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.verdict == "PASS"
        assert ev.task_id is None
        assert ev.review_id is None

    def test_with_optional_fields(self) -> None:
        ev = AuditVerdictIssuedEventV1(
            event_id="e1",
            workspace="/ws",
            run_id="r1",
            verdict="PASS",
            issued_at="2024-01-01T00:00:00Z",
            task_id="t1",
            review_id="rev1",
        )
        assert ev.task_id == "t1"
        assert ev.review_id == "rev1"

    def test_empty_event_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            AuditVerdictIssuedEventV1(
                event_id="",
                workspace="/ws",
                run_id="r1",
                verdict="PASS",
                issued_at="2024-01-01T00:00:00Z",
            )

    def test_empty_verdict_rejected(self) -> None:
        with pytest.raises(ValueError, match="verdict"):
            AuditVerdictIssuedEventV1(
                event_id="e1",
                workspace="/ws",
                run_id="r1",
                verdict="",
                issued_at="2024-01-01T00:00:00Z",
            )

    def test_empty_issued_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="issued_at"):
            AuditVerdictIssuedEventV1(
                event_id="e1",
                workspace="/ws",
                run_id="r1",
                verdict="PASS",
                issued_at="",
            )


class TestAuditVerdictResultV1:
    """AuditVerdictResultV1 construction and validation."""

    def test_success_result(self) -> None:
        result = AuditVerdictResultV1(
            ok=True,
            status="success",
            workspace="/ws",
            run_id="r1",
        )
        assert result.ok is True
        assert result.details == {}
        assert result.error_code is None

    def test_failed_result_with_error(self) -> None:
        result = AuditVerdictResultV1(
            ok=False,
            status="error",
            workspace="/ws",
            run_id="r1",
            error_code="E001",
            error_message="boom",
        )
        assert result.ok is False
        assert result.error_code == "E001"

    def test_failed_result_requires_error_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            AuditVerdictResultV1(
                ok=False,
                status="error",
                workspace="/ws",
                run_id="r1",
            )

    def test_empty_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            AuditVerdictResultV1(ok=True, status="", workspace="/ws", run_id="r1")

    def test_details_is_copied(self) -> None:
        original: dict[str, Any] = {"key": "value"}
        result = AuditVerdictResultV1(
            ok=True,
            status="success",
            workspace="/ws",
            run_id="r1",
            details=original,
        )
        original["key"] = "mutated"
        assert result.details["key"] == "value"

    def test_verdict_optional(self) -> None:
        result = AuditVerdictResultV1(
            ok=True,
            status="success",
            workspace="/ws",
            run_id="r1",
            verdict="PASS",
        )
        assert result.verdict == "PASS"

    def test_empty_verdict_rejected(self) -> None:
        with pytest.raises(ValueError, match="verdict"):
            AuditVerdictResultV1(
                ok=True,
                status="success",
                workspace="/ws",
                run_id="r1",
                verdict="",
            )


class TestAuditVerdictError:
    """AuditVerdictError exception behavior."""

    def test_default_construction(self) -> None:
        err = AuditVerdictError("something wrong")
        assert str(err) == "something wrong"
        assert err.code == "audit_verdict_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = AuditVerdictError("msg", code="CUSTOM", details={"a": 1})
        assert err.code == "CUSTOM"
        assert err.details == {"a": 1}

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValueError, match="message"):
            AuditVerdictError("")

    def test_empty_code_rejected(self) -> None:
        with pytest.raises(ValueError, match="code"):
            AuditVerdictError("msg", code="")


class TestIAuditVerdictService:
    """Protocol existence check."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert callable(IAuditVerdictService)
