"""Tests for polaris.application.orchestration.qa_orchestrator.

Regression test for B3: string iteration bug in plan_audit().
"""

from __future__ import annotations

import pytest
from polaris.application.orchestration.qa_orchestrator import (
    QaAuditConfig,
    QaOrchestrator,
    QaOrchestratorError,
)


class TestPlanAudit:
    """Regression tests for plan_audit string-iteration bug (B3)."""

    def test_evidence_paths_none(self) -> None:
        """When evidence_paths is None, use config default."""
        orch = QaOrchestrator(
            QaAuditConfig(workspace="/tmp", evidence_paths=("file1", "file2"))
        )
        plan = orch.plan_audit(task_id="t")
        assert plan["evidence_paths"] == ("file1", "file2")

    def test_evidence_paths_tuple_of_strings(self) -> None:
        """Normal tuple input is consumed element-by-element."""
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        plan = orch.plan_audit(task_id="t", evidence_paths=("path1", "path2"))
        assert plan["evidence_paths"] == ("path1", "path2")

    def test_evidence_paths_string_not_iterated(self) -> None:
        """String input is NOT iterated into characters.

        BUG (B3): A string like "/path/to/file" would iterate into
        ['/', 'p', 'a', 't', 'h', ...] if treated as an iterable.
        After fix: a string is wrapped in a tuple, not iterated.
        """
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        plan = orch.plan_audit(task_id="t", evidence_paths="/path/to/file")
        # Must be a tuple containing the full string, not individual chars
        assert plan["evidence_paths"] == ("/path/to/file",), (
            f"B3: Expected ('/path/to/file',), got {plan['evidence_paths']!r}. "
            "String was iterated into characters instead of being treated as a path."
        )

    def test_evidence_paths_whitespace_string_filtered(self) -> None:
        """A whitespace-only string yields an empty tuple."""
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        plan = orch.plan_audit(task_id="t", evidence_paths="   ")
        assert plan["evidence_paths"] == ()

    def test_evidence_paths_empty_tuple(self) -> None:
        """An empty tuple yields an empty evidence list."""
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        plan = orch.plan_audit(task_id="t", evidence_paths=())
        assert plan["evidence_paths"] == ()

    def test_evidence_paths_list_of_paths(self) -> None:
        """List input is converted to tuple as expected."""
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        plan = orch.plan_audit(task_id="t", evidence_paths=["a", "b"])
        assert plan["evidence_paths"] == ("a", "b")

    def test_plan_audit_task_id_required(self) -> None:
        """Empty task_id raises QaOrchestratorError."""
        orch = QaOrchestrator(QaAuditConfig(workspace="/tmp"))
        with pytest.raises(QaOrchestratorError):
            orch.plan_audit(task_id="")
        with pytest.raises(QaOrchestratorError):
            orch.plan_audit(task_id="   ")
