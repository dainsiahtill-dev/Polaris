"""Tests for polaris.cells.audit.diagnosis.internal.toolkit.triage.

Covers triage bundle building and extraction helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from polaris.cells.audit.diagnosis.internal.toolkit.triage import (
    _extract_acceptance,
    _extract_fixed_issues,
    _extract_leakage_findings,
    _extract_pm_history,
    _extract_tool_audit,
    _identify_risks,
    _infer_run_id,
    build_triage_bundle,
)


class TestExtractPmHistory:
    """PM history extraction from events."""

    def test_extracts_pm_events(self) -> None:
        events = [
            {
                "source": {"role": "pm"},
                "action": {"name": "plan", "result": "success"},
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "source": {"role": "director"},
                "action": {"name": "exec", "result": "success"},
                "timestamp": "2024-01-01T00:01:00Z",
            },
        ]
        result = _extract_pm_history(events)
        assert len(result) == 1
        assert result[0]["action"] == "plan"

    def test_empty_events(self) -> None:
        assert _extract_pm_history([]) == []

    def test_non_dict_source_skipped(self) -> None:
        events = [
            {"source": "pm", "action": {"name": "plan"}, "timestamp": "2024-01-01T00:00:00Z"},
        ]
        assert _extract_pm_history(events) == []


class TestInferRunId:
    """Run ID inference from events."""

    def test_infers_from_task_run_id(self) -> None:
        events = [
            {"task": {"run_id": "r1"}},
        ]
        assert _infer_run_id(events) == "r1"

    def test_skips_non_dict_task(self) -> None:
        events = [
            {"task": "r1"},
        ]
        assert _infer_run_id(events) is None

    def test_empty_events(self) -> None:
        assert _infer_run_id([]) is None

    def test_skips_empty_run_id(self) -> None:
        events = [
            {"task": {"run_id": ""}},
            {"task": {"run_id": "r2"}},
        ]
        assert _infer_run_id(events) == "r2"


class TestExtractLeakageFindings:
    """Leakage findings extraction (currently placeholder)."""

    def test_returns_empty_list(self) -> None:
        assert _extract_leakage_findings([]) == []
        assert _extract_leakage_findings([{"x": 1}]) == []


class TestExtractToolAudit:
    """Tool audit extraction from events."""

    def test_empty_events(self) -> None:
        result = _extract_tool_audit([])
        assert result["total"] == 0
        assert result["failed"] == 0
        assert result["tools_used"] == []
        assert result["errors"] == []

    def test_audit_event_format(self) -> None:
        events = [
            {
                "event_type": "tool_execution",
                "resource": {"path": "git", "operation": "status"},
                "action": {"result": "success"},
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "event_type": "tool_execution",
                "resource": {"path": "pytest", "operation": "run"},
                "action": {"result": "failure"},
                "data": {"error": "test failed"},
                "timestamp": "2024-01-01T00:01:00Z",
            },
        ]
        result = _extract_tool_audit(events)
        assert result["total"] == 2
        assert result["failed"] == 1
        assert len(result["tools_used"]) == 2
        assert result["errors"][0]["error"] == "test failed"

    def test_journal_tool_call_format(self) -> None:
        events = [
            {
                "kind": "action",
                "message": "tool_call:WRITE_FILE",
                "raw": {"tool": "WRITE_FILE", "args": {"file": "a.py"}},
                "ts": "2024-01-01T00:00:00Z",
            },
            {
                "kind": "output",
                "message": "tool_result:WRITE_FILE",
                "raw": {"tool": "WRITE_FILE", "result": {"success": True}},
            },
        ]
        result = _extract_tool_audit(events)
        assert result["total"] == 1
        assert len(result["tools_used"]) == 1
        assert result["tools_used"][0]["tool"] == "WRITE_FILE"

    def test_journal_tool_result_failure(self) -> None:
        events = [
            {"kind": "action", "message": "tool_call:EXEC", "raw": {"tool": "EXEC", "args": {}}},
            {
                "kind": "output",
                "message": "tool_result:EXEC",
                "raw": {"tool": "EXEC", "result": {"success": False, "error": "cmd failed"}},
            },
        ]
        result = _extract_tool_audit(events)
        assert result["failed"] == 1
        assert len(result["errors"]) == 1


class TestExtractFixedIssues:
    """Fixed issues extraction."""

    def test_extracts_successful_tasks(self) -> None:
        events = [
            {
                "event_type": "task_complete",
                "action": {"result": "success"},
                "task": {"task_id": "t1"},
                "data": {"description": "fixed bug"},
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "event_type": "task_complete",
                "action": {"result": "failure"},
                "task": {"task_id": "t2"},
                "data": {"description": "failed"},
                "timestamp": "2024-01-01T00:01:00Z",
            },
        ]
        result = _extract_fixed_issues(events)
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"

    def test_empty_events(self) -> None:
        assert _extract_fixed_issues([]) == []


class TestExtractAcceptance:
    """Acceptance results extraction."""

    def test_empty_events(self) -> None:
        result = _extract_acceptance([])
        assert result["passed"] == 0
        assert result["failed"] == 0
        assert result["inconclusive"] == 0

    def test_counts_verdicts(self) -> None:
        events = [
            {"event_type": "audit_verdict", "data": {"verdict": "PASS"}, "timestamp": "2024-01-01T00:00:00Z"},
            {"event_type": "audit_verdict", "data": {"verdict": "FAIL"}, "timestamp": "2024-01-01T00:01:00Z"},
            {"event_type": "audit_verdict", "data": {"verdict": "MAYBE"}, "timestamp": "2024-01-01T00:02:00Z"},
        ]
        result = _extract_acceptance(events)
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["inconclusive"] == 1
        assert len(result["details"]) == 3


class TestIdentifyRisks:
    """Risk identification from events."""

    def test_no_risks(self) -> None:
        assert _identify_risks([]) == []

    def test_multiple_failures_risk(self) -> None:
        events = [{"event_type": "task_failed"} for _ in range(5)]
        result = _identify_risks(events)
        assert any("Multiple failures" in r for r in result)

    def test_tool_errors_risk(self) -> None:
        events = [
            {"event_type": "tool_execution", "action": {"result": "failure"}},
        ]
        result = _identify_risks(events)
        assert any("Tool execution failures" in r for r in result)


class TestBuildTriageBundle:
    """Triage bundle building."""

    def test_no_events_returns_not_found(self, tmp_path: Path) -> None:
        with patch(
            "polaris.cells.audit.diagnosis.internal.toolkit.triage.resolve_runtime_path", return_value=str(tmp_path)
        ):
            result = build_triage_bundle(workspace="/ws", run_id="r1")
        assert result["status"] == "not_found"
        assert result["run_id"] == "r1"

    def test_bundle_structure(self, tmp_path: Path) -> None:
        # Create minimal audit event file
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        (audit_dir / "audit-2024-01-01.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "event_type": "audit_verdict",
                    "data": {"verdict": "PASS"},
                    "task": {"run_id": "r1", "task_id": "t1"},
                    "source": {"role": "pm"},
                    "action": {"name": "plan", "result": "success"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "polaris.cells.audit.diagnosis.internal.toolkit.triage.resolve_runtime_path", return_value=str(tmp_path)
        ):
            result = build_triage_bundle(workspace="/ws", run_id="r1")

        assert result["status"] == "success"
        assert result["run_id"] == "r1"
        assert "pm_quality_history" in result
        assert "acceptance_results" in result
        assert "failure_hops" in result
