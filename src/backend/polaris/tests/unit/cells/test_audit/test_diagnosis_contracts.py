"""Tests for polaris.cells.audit.diagnosis.public.contracts.

Covers P0 data classes / enums / constants with validation logic.
"""

from __future__ import annotations

import pytest
from polaris.cells.audit.diagnosis.public.contracts import (
    AuditDiagnosisCompletedEventV1,
    AuditDiagnosisError,
    AuditDiagnosisResultV1,
    IAuditDiagnosisService,
    QueryAuditDiagnosisTrailV1,
    RunAuditDiagnosisCommandV1,
)


class TestRunAuditDiagnosisCommandV1:
    """RunAuditDiagnosisCommandV1 construction and validation."""

    def test_valid_command(self) -> None:
        cmd = RunAuditDiagnosisCommandV1(workspace="/ws", command="scan")
        assert cmd.workspace == "/ws"
        assert cmd.command == "scan"
        assert cmd.args == {}
        assert cmd.cache_root is None

    def test_command_with_args_and_cache_root(self) -> None:
        cmd = RunAuditDiagnosisCommandV1(
            workspace="/ws",
            command="scan",
            args={"scope": "full"},
            cache_root="/cache",
        )
        assert cmd.args == {"scope": "full"}
        assert cmd.cache_root == "/cache"

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            RunAuditDiagnosisCommandV1(workspace="", command="scan")

    def test_whitespace_only_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            RunAuditDiagnosisCommandV1(workspace="   ", command="scan")

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="command"):
            RunAuditDiagnosisCommandV1(workspace="/ws", command="")

    def test_empty_cache_root_rejected(self) -> None:
        with pytest.raises(ValueError, match="cache_root"):
            RunAuditDiagnosisCommandV1(workspace="/ws", command="scan", cache_root="")

    def test_args_is_copied(self) -> None:
        original = {"key": "value"}
        cmd = RunAuditDiagnosisCommandV1(workspace="/ws", command="scan", args=original)
        original["key"] = "mutated"
        assert cmd.args["key"] == "value"


class TestQueryAuditDiagnosisTrailV1:
    """QueryAuditDiagnosisTrailV1 construction and validation."""

    def test_default_values(self) -> None:
        q = QueryAuditDiagnosisTrailV1(workspace="/ws")
        assert q.workspace == "/ws"
        assert q.run_id is None
        assert q.task_id is None
        assert q.limit == 200

    def test_custom_limit(self) -> None:
        q = QueryAuditDiagnosisTrailV1(workspace="/ws", limit=50)
        assert q.limit == 50

    def test_limit_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            QueryAuditDiagnosisTrailV1(workspace="/ws", limit=0)

    def test_empty_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryAuditDiagnosisTrailV1(workspace="")

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            QueryAuditDiagnosisTrailV1(workspace="/ws", run_id="")

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            QueryAuditDiagnosisTrailV1(workspace="/ws", task_id="")


class TestAuditDiagnosisCompletedEventV1:
    """AuditDiagnosisCompletedEventV1 construction and validation."""

    def test_valid_event(self) -> None:
        ev = AuditDiagnosisCompletedEventV1(
            event_id="e1",
            workspace="/ws",
            command="scan",
            status="success",
            completed_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.status == "success"
        assert ev.run_id is None
        assert ev.task_id is None

    def test_with_optional_fields(self) -> None:
        ev = AuditDiagnosisCompletedEventV1(
            event_id="e1",
            workspace="/ws",
            command="scan",
            status="success",
            completed_at="2024-01-01T00:00:00Z",
            run_id="r1",
            task_id="t1",
        )
        assert ev.run_id == "r1"
        assert ev.task_id == "t1"

    def test_empty_event_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            AuditDiagnosisCompletedEventV1(
                event_id="",
                workspace="/ws",
                command="scan",
                status="success",
                completed_at="2024-01-01T00:00:00Z",
            )

    def test_empty_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            AuditDiagnosisCompletedEventV1(
                event_id="e1",
                workspace="/ws",
                command="scan",
                status="",
                completed_at="2024-01-01T00:00:00Z",
            )


class TestAuditDiagnosisResultV1:
    """AuditDiagnosisResultV1 construction and validation."""

    def test_success_result(self) -> None:
        result = AuditDiagnosisResultV1(
            ok=True,
            status="success",
            workspace="/ws",
        )
        assert result.ok is True
        assert result.payload == {}
        assert result.error_code is None

    def test_failed_result_with_error(self) -> None:
        result = AuditDiagnosisResultV1(
            ok=False,
            status="error",
            workspace="/ws",
            error_code="E001",
            error_message="boom",
        )
        assert result.ok is False
        assert result.error_code == "E001"

    def test_failed_result_requires_error_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            AuditDiagnosisResultV1(
                ok=False,
                status="error",
                workspace="/ws",
            )

    def test_empty_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            AuditDiagnosisResultV1(ok=True, status="", workspace="/ws")

    def test_payload_is_copied(self) -> None:
        original = {"key": "value"}
        result = AuditDiagnosisResultV1(ok=True, status="success", workspace="/ws", payload=original)
        original["key"] = "mutated"
        assert result.payload["key"] == "value"


class TestAuditDiagnosisError:
    """AuditDiagnosisError exception behavior."""

    def test_default_construction(self) -> None:
        err = AuditDiagnosisError("something wrong")
        assert str(err) == "something wrong"
        assert err.code == "audit_diagnosis_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = AuditDiagnosisError("msg", code="CUSTOM", details={"a": 1})
        assert err.code == "CUSTOM"
        assert err.details == {"a": 1}

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValueError, match="message"):
            AuditDiagnosisError("")

    def test_empty_code_rejected(self) -> None:
        with pytest.raises(ValueError, match="code"):
            AuditDiagnosisError("msg", code="")


class TestIAuditDiagnosisService:
    """Protocol existence check."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert callable(IAuditDiagnosisService)
