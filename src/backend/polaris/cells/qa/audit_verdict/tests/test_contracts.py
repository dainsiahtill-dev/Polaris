"""Tests for qa.audit_verdict public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.qa.audit_verdict.public.contracts import (
    GetQaVerdictQueryV1,
    QaAuditError,
    QaAuditResultV1,
    QaVerdictIssuedEventV1,
    RunQaAuditCommandV1,
)


class TestRunQaAuditCommandV1:
    """RunQaAuditCommandV1 validation and normalisation."""

    def test_required_fields(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/tmp"
        assert cmd.run_id is None
        assert cmd.criteria == {}
        assert cmd.evidence_paths == ()

    def test_strips_whitespace(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="  t2  ", workspace="  /ws  ")
        assert cmd.task_id == "t2"
        assert cmd.workspace == "/ws"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RunQaAuditCommandV1(task_id="", workspace="/tmp")  # type: ignore[arg-type]

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RunQaAuditCommandV1(task_id="t1", workspace="   ")  # type: ignore[arg-type]

    def test_criteria_normalised_to_dict(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp", criteria={"k": "v"})
        assert isinstance(cmd.criteria, dict)
        assert cmd.criteria["k"] == "v"

    def test_evidence_paths_filtered_and_tuple(self) -> None:
        cmd = RunQaAuditCommandV1(
            task_id="t1",
            workspace="/tmp",
            evidence_paths=["a.py", "  ", "b.py"],  # type: ignore[arg-type]
        )
        assert cmd.evidence_paths == ("a.py", "b.py")

    def test_run_id_optional(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp", run_id="r1")
        assert cmd.run_id == "r1"


class TestGetQaVerdictQueryV1:
    """GetQaVerdictQueryV1 validation."""

    def test_required_fields(self) -> None:
        q = GetQaVerdictQueryV1(task_id="t1", workspace="/tmp")
        assert q.task_id == "t1"
        assert q.workspace == "/tmp"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GetQaVerdictQueryV1(task_id="", workspace="/tmp")  # type: ignore[arg-type]

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GetQaVerdictQueryV1(task_id="t1", workspace="")  # type: ignore[arg-type]


class TestQaVerdictIssuedEventV1:
    """QaVerdictIssuedEventV1 validation."""

    def test_required_fields(self) -> None:
        evt = QaVerdictIssuedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            issued_at="2026-01-01T00:00:00Z",
        )
        assert evt.verdict == "PASS"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaVerdictIssuedEventV1(
                event_id="",
                task_id="t1",
                workspace="/tmp",
                verdict="PASS",
                issued_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]

    def test_empty_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaVerdictIssuedEventV1(
                event_id="e1",
                task_id="t1",
                workspace="/tmp",
                verdict="  ",
                issued_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]


class TestQaAuditResultV1:
    """QaAuditResultV1 validation."""

    def test_pass_result(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            score=0.9,
        )
        assert r.ok is True
        assert r.score == 0.9
        assert r.findings == ()
        assert r.suggestions == ()

    def test_fail_result(self) -> None:
        r = QaAuditResultV1(
            ok=False,
            task_id="t1",
            workspace="/tmp",
            verdict="FAIL",
            findings=["f1", "f2"],  # type: ignore[arg-type]
        )
        assert r.ok is False
        assert r.findings == ("f1", "f2")

    def test_negative_score_raises(self) -> None:
        with pytest.raises(ValueError, match="score must be >= 0"):
            QaAuditResultV1(
                ok=True,
                task_id="t1",
                workspace="/tmp",
                verdict="PASS",
                score=-0.1,
            )

    def test_findings_normalised_to_tuple(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            findings=["a", "b"],  # type: ignore[arg-type]
        )
        assert isinstance(r.findings, tuple)
        assert r.findings == ("a", "b")


class TestQaAuditError:
    """QaAuditError structured error."""

    def test_code_defaults_to_qa_audit_error(self) -> None:
        err = QaAuditError("something went wrong")
        assert err.code == "qa_audit_error"
        assert str(err) == "something went wrong"

    def test_custom_code_and_details(self) -> None:
        err = QaAuditError("boom", code="SECURITY_VIOLATION", details={"path": "/etc"})
        assert err.code == "SECURITY_VIOLATION"
        assert err.details["path"] == "/etc"

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaAuditError("")  # type: ignore[arg-type]

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaAuditError("msg", code="")  # type: ignore[arg-type]
