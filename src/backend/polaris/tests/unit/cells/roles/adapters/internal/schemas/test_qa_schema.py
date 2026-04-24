"""Tests for polaris.cells.roles.adapters.internal.schemas.qa_schema."""

from __future__ import annotations

import pytest
from polaris.cells.roles.adapters.internal.schemas.qa_schema import (
    Metrics,
    QAFinding,
    QAReportOutput,
)


class TestQAFinding:
    def test_fields(self) -> None:
        f = QAFinding(
            severity="high",
            category="security",
            description="A security issue found",
            recommendation="Fix it immediately",
        )
        assert f.severity == "high"
        assert f.category == "security"
        assert f.location is None


class TestMetrics:
    def test_defaults(self) -> None:
        m = Metrics()
        assert m.code_coverage is None
        assert m.complexity_score is None
        assert m.maintainability_index is None


class TestQAReportOutput:
    def test_defaults(self) -> None:
        r = QAReportOutput()
        assert r.verdict == ""
        assert r.findings == []
        assert r.blockers == []

    def test_pass_with_critical_finding_raises(self) -> None:
        finding = QAFinding(
            severity="critical",
            category="security",
            description="A critical issue found here",
            recommendation="Fix it now please",
        )
        with pytest.raises(ValueError, match="Cannot have PASS"):
            QAReportOutput(verdict="PASS", findings=[finding])

    def test_fail_without_findings_raises(self) -> None:
        with pytest.raises(ValueError, match="FAIL verdict must have"):
            QAReportOutput(verdict="FAIL", findings=[])

    def test_blocked_without_blockers_raises(self) -> None:
        with pytest.raises(ValueError, match="BLOCKED verdict must have"):
            QAReportOutput(verdict="BLOCKED", blockers=[])

    def test_valid_pass(self) -> None:
        r = QAReportOutput(verdict="PASS", findings=[])
        assert r.verdict == "PASS"

    def test_valid_fail(self) -> None:
        finding = QAFinding(
            severity="high",
            category="security",
            description="A security issue found",
            recommendation="Fix it immediately",
        )
        r = QAReportOutput(verdict="FAIL", findings=[finding])
        assert r.verdict == "FAIL"
