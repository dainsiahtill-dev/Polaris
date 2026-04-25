"""Tests for polaris.cells.qa.audit_verdict.public.contracts.

Covers all frozen dataclasses, validation logic in __post_init__,
and the custom error class.
"""

from __future__ import annotations

import pytest
from polaris.cells.qa.audit_verdict.public.contracts import (
    ClaimQaTaskCommandV1,
    GetQaVerdictQueryV1,
    QaAuditCompletedEventV1,
    QaAuditError,
    QaAuditErrorV1,
    QaAuditResultV1,
    QaVerdictIssuedEventV1,
    RunQaAuditCommandV1,
)


class TestRunQaAuditCommandV1:
    """Tests for RunQaAuditCommandV1."""

    def test_valid_command(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/ws")
        assert cmd.run_id is None
        assert cmd.criteria == {}
        assert cmd.evidence_paths == ()

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            RunQaAuditCommandV1(task_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            RunQaAuditCommandV1(task_id="t1", workspace="")

    def test_criteria_copied(self) -> None:
        original = {"severity": "high"}
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/ws", criteria=original)
        assert cmd.criteria == {"severity": "high"}
        original["severity"] = "low"
        assert cmd.criteria == {"severity": "high"}

    def test_evidence_paths_filtered(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/ws", evidence_paths=["a.py", "", "  ", "b.py"])
        assert cmd.evidence_paths == ("a.py", "b.py")


class TestGetQaVerdictQueryV1:
    """Tests for GetQaVerdictQueryV1."""

    def test_valid_query(self) -> None:
        q = GetQaVerdictQueryV1(task_id="t1", workspace="/ws")
        assert q.run_id is None

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetQaVerdictQueryV1(task_id="", workspace="/ws")


class TestQaVerdictIssuedEventV1:
    """Tests for QaVerdictIssuedEventV1."""

    def test_valid_event(self) -> None:
        ev = QaVerdictIssuedEventV1(
            event_id="e1", task_id="t1", workspace="/ws", verdict="pass", issued_at="2026-01-01T00:00:00Z"
        )
        assert ev.run_id is None

    def test_empty_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="verdict must be a non-empty string"):
            QaVerdictIssuedEventV1(
                event_id="e1", task_id="t1", workspace="/ws", verdict="", issued_at="2026-01-01T00:00:00Z"
            )


class TestQaAuditResultV1:
    """Tests for QaAuditResultV1."""

    def test_valid_result(self) -> None:
        r = QaAuditResultV1(ok=True, task_id="t1", workspace="/ws", verdict="pass")
        assert r.score == 0.0
        assert r.findings == ()
        assert r.suggestions == ()

    def test_negative_score_raises(self) -> None:
        with pytest.raises(ValueError, match="score must be >= 0"):
            QaAuditResultV1(ok=True, task_id="t1", workspace="/ws", verdict="pass", score=-1.0)

    def test_findings_coerced(self) -> None:
        r = QaAuditResultV1(ok=True, task_id="t1", workspace="/ws", verdict="pass", findings=["f1"])
        assert r.findings == ("f1",)


class TestQaAuditErrorV1:
    """Tests for QaAuditErrorV1."""

    def test_defaults(self) -> None:
        err = QaAuditErrorV1("boom")
        assert str(err) == "boom"
        assert err.code == "qa_audit_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = QaAuditErrorV1("boom", code="E1", details={"k": "v"})
        assert err.code == "E1"
        assert err.details == {"k": "v"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            QaAuditErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            QaAuditErrorV1("boom", code="")


class TestClaimQaTaskCommandV1:
    """Tests for ClaimQaTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = ClaimQaTaskCommandV1(task_id="t1", workspace="/ws", worker_id="w1")
        assert cmd.run_id is None

    def test_empty_worker_id_raises(self) -> None:
        with pytest.raises(ValueError, match="worker_id must be a non-empty string"):
            ClaimQaTaskCommandV1(task_id="t1", workspace="/ws", worker_id="")


class TestQaAuditCompletedEventV1:
    """Tests for QaAuditCompletedEventV1."""

    def test_valid_event(self) -> None:
        ev = QaAuditCompletedEventV1(event_id="e1", task_id="t1", workspace="/ws")
        assert ev.verdict == "resolved"
        assert ev.findings == ()
        assert ev.completed_at == ""

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            QaAuditCompletedEventV1(event_id="", task_id="t1", workspace="/ws")


class TestBackwardCompatibleAlias:
    """Tests that QaAuditError is an alias for QaAuditErrorV1."""

    def test_alias_identity(self) -> None:
        assert QaAuditError is QaAuditErrorV1
