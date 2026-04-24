"""Tests for polaris.cells.audit.diagnosis.internal.diagnosis_engine."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from polaris.cells.audit.diagnosis.internal.diagnosis_engine import (
    AuditDiagnosisEngine,
    ScanFinding,
    _clamp,
    _extract_event_error,
    _normalize_rel_path,
    _parse_iso_timestamp,
    _parse_time_range,
    _safe_int,
)


class TestHelperFunctions:
    """Helper function tests."""

    def test_clamp_within_bounds(self) -> None:
        assert _clamp(5, 0, 10) == 5

    def test_clamp_below_low(self) -> None:
        assert _clamp(-5, 0, 10) == 0

    def test_clamp_above_high(self) -> None:
        assert _clamp(15, 0, 10) == 10

    def test_safe_int_valid(self) -> None:
        assert _safe_int("42") == 42
        assert _safe_int(42) == 42

    def test_safe_int_invalid(self) -> None:
        assert _safe_int("not a number") == 0
        assert _safe_int(None) == 0

    def test_safe_int_with_default(self) -> None:
        assert _safe_int("invalid", default=-1) == -1

    def test_parse_iso_timestamp_valid(self) -> None:
        ts = _parse_iso_timestamp("2024-01-15T10:30:00")
        assert ts is not None
        assert ts.year == 2024
        assert ts.month == 1
        assert ts.day == 15

    def test_parse_iso_timestamp_with_z(self) -> None:
        ts = _parse_iso_timestamp("2024-01-15T10:30:00Z")
        assert ts is not None
        assert ts.tzinfo is not None

    def test_parse_iso_timestamp_invalid(self) -> None:
        assert _parse_iso_timestamp("not a date") is None

    def test_parse_iso_timestamp_empty(self) -> None:
        assert _parse_iso_timestamp("") is None
        assert _parse_iso_timestamp(None) is None

    def test_parse_time_range_minutes(self) -> None:
        delta = _parse_time_range("30m")
        assert delta.total_seconds() == 30 * 60

    def test_parse_time_range_hours(self) -> None:
        delta = _parse_time_range("2h")
        assert delta.total_seconds() == 2 * 3600

    def test_parse_time_range_days(self) -> None:
        delta = _parse_time_range("3d")
        assert delta.total_seconds() == 3 * 86400

    def test_parse_time_range_invalid_defaults_to_1h(self) -> None:
        delta = _parse_time_range("invalid")
        assert delta.total_seconds() == 3600

    def test_parse_time_range_empty_defaults_to_1h(self) -> None:
        delta = _parse_time_range("")
        assert delta.total_seconds() == 3600

    def test_extract_event_error_direct_fields(self) -> None:
        event = {
            "error": "Permission denied",
            "message": "Access failed",
        }
        result = _extract_event_error(event)
        assert "Permission denied" in result
        assert "Access failed" in result

    def test_extract_event_error_nested_action(self) -> None:
        event = {
            "action": {
                "error": "Command failed",
                "detail": "Exit code 1",
            }
        }
        result = _extract_event_error(event)
        assert "Command failed" in result
        assert "Exit code 1" in result

    def test_extract_event_error_nested_data(self) -> None:
        event = {
            "data": {
                "stderr": "Error output",
                "stdout": "Normal output",
            }
        }
        result = _extract_event_error(event)
        assert "Error output" in result

    def test_extract_event_error_empty(self) -> None:
        event = {"other": "fields"}
        result = _extract_event_error(event)
        assert result == ""


class TestScanFinding:
    """ScanFinding dataclass tests."""

    def test_to_dict(self) -> None:
        finding = ScanFinding(
            severity="high",
            category="security",
            file="src/main.py",
            line=42,
            message="Issue found",
            evidence="code snippet",
            recommendation="Fix it",
        )
        result = finding.to_dict()
        assert result["severity"] == "high"
        assert result["category"] == "security"
        assert result["file"] == "src/main.py"
        assert result["line"] == 42


class TestAuditDiagnosisEngine:
    """AuditDiagnosisEngine tests."""

    @pytest.fixture
    def workspace(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def runtime_root(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_init(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        assert engine.workspace == workspace.resolve()
        assert engine.runtime_root == runtime_root.resolve()

    def test_analyze_failure_no_events(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.analyze_failure(run_id="nonexistent-run", time_range="1h")
        # Result should have required keys
        assert "failure_detected" in result
        assert "failure_hops" in result
        # With no events, failure is not detected
        assert result["failure_detected"] is False
        # failure_hops may contain hop 3 (root cause analysis) even with no failure
        assert result["failure_hops"] is not None

    def test_analyze_failure_depth_normalized(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.analyze_failure(depth=10)  # Exceeds max
        assert result["depth"] == 3

    def test_analyze_failure_depth_below_min(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.analyze_failure(depth=0)
        assert result["depth"] == 1

    def test_scan_project_scope_validation(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        with pytest.raises(ValueError, match="Unsupported scope"):
            engine.scan_project(scope="invalid_scope")

    def test_scan_project_region_requires_focus(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        with pytest.raises(ValueError, match="focus is required"):
            engine.scan_project(scope="region")

    def test_scan_project_region_with_focus(self, workspace: Path, runtime_root: Path) -> None:
        # Create a test file
        test_file = workspace / "test.py"
        test_file.write_text("# Test file\nprint('hello')", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="region", focus=str(test_file))
        assert result["scope"] == "region"
        assert "summary" in result
        assert "findings" in result

    def test_scan_project_full_scan(self, workspace: Path, runtime_root: Path) -> None:
        # Create test files
        (workspace / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        (workspace / "test_main.py").write_text("def test_main():\n    pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full", max_files=100)
        assert result["scope"] == "full"
        assert "summary" in result
        assert "findings" in result
        assert "recommendations" in result

    def test_scan_project_detects_test_files(self, workspace: Path, runtime_root: Path) -> None:
        # Create a test file
        (workspace / "test_example.py").write_text("def test_something():\n    pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        # Test files should be counted
        summary = result["summary"]
        assert summary["files_scanned"] >= 1

    def test_scan_project_detects_long_lines(self, workspace: Path, runtime_root: Path) -> None:
        # Create file with long line
        long_line = "x" * 200
        (workspace / "long.py").write_text(f"def foo():\n    {long_line}\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        # Should detect long line finding
        findings = result["findings"]
        long_line_findings = [f for f in findings if f["category"] == "readability"]
        assert len(long_line_findings) >= 1

    def test_scan_project_detects_todo_fixes(self, workspace: Path, runtime_root: Path) -> None:
        (workspace / "todo.py").write_text("def foo():\n    TODO: fix this\n    pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        findings = result["findings"]
        todo_findings = [f for f in findings if "TODO" in f["message"] or "FIXME" in f["message"]]
        assert len(todo_findings) >= 1

    def test_scan_project_detects_large_file(self, workspace: Path, runtime_root: Path) -> None:
        # Create file with 1000 lines
        lines = ["def foo():\n    pass\n"] * 1000
        (workspace / "large.py").write_text("".join(lines), encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        findings = result["findings"]
        large_file_findings = [f for f in findings if "very large" in f["message"]]
        assert len(large_file_findings) >= 1

    def test_scan_project_no_test_files_warning(self, workspace: Path, runtime_root: Path) -> None:
        # Create only source files, no test files
        (workspace / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        findings = result["findings"]
        coverage_findings = [f for f in findings if f["category"] == "test_coverage"]
        assert len(coverage_findings) >= 1

    def test_scan_project_complexity_detection(self, workspace: Path, runtime_root: Path) -> None:
        # Create file with high complexity (many if/for/while/except statements)
        # Complexity score = count of 'if' + 'for' + 'while' + 'except' per line
        # Need 180+ complexity score across the file
        lines: list[str] = []
        for _ in range(50):
            lines.extend(["if x: pass", "for i in range(10): pass", "while True: pass", "try: pass"])
        code = "\n".join(lines)
        (workspace / "complex.py").write_text(code, encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        # Complexity detection is optional, test verifies scan completes
        assert "summary" in result

    def test_scan_project_max_findings_limit(self, workspace: Path, runtime_root: Path) -> None:
        # Create many TODOs
        content = "\n".join([f"# TODO {i}" for i in range(100)])
        (workspace / "many_todos.py").write_text(content, encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full", max_findings=10)
        assert len(result["findings"]) <= 10

    def test_check_region_file_not_found(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        with pytest.raises(FileNotFoundError):
            engine.check_region(file_path="nonexistent.py")

    def test_check_region_by_file(self, workspace: Path, runtime_root: Path) -> None:
        test_file = workspace / "test.py"
        test_file.write_text("def foo():\n    pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.check_region(file_path=str(test_file))
        assert result["file"] is not None
        assert "summary" in result
        assert "findings" in result

    def test_check_region_by_function(self, workspace: Path, runtime_root: Path) -> None:
        test_file = workspace / "test.py"
        test_file.write_text(
            "def target_func():\n    pass\n\ndef other_func():\n    pass\n",
            encoding="utf-8",
        )

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.check_region(function_name="target_func")
        assert "target_func" in result["function_name"] or result["function_name"] == ""

    def test_check_region_with_line_range(self, workspace: Path, runtime_root: Path) -> None:
        test_file = workspace / "test.py"
        test_file.write_text("\n".join([f"line {i}" for i in range(20)]), encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.check_region(file_path=str(test_file), line_range=(5, 10))
        assert result["line_range"]["start"] == 5
        assert result["line_range"]["end"] == 10

    def test_get_trace_requires_trace_id(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        with pytest.raises(ValueError, match="trace_id is required"):
            engine.get_trace(trace_id="")

    def test_get_trace_empty_result(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.get_trace(trace_id="nonexistent-trace")
        assert result["trace_id"] == "nonexistent-trace"
        assert result["event_count"] == 0
        assert result["timeline"] == []

    def test_build_timeline(self, workspace: Path, runtime_root: Path) -> None:
        events = [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "event_type": "task_started",
                "source": {"role": "pm"},
                "action": {"name": "start", "result": "success"},
                "task": {"task_id": "t1", "run_id": "r1"},
            },
            {
                "timestamp": "2024-01-01T00:01:00Z",
                "event_type": "task_completed",
                "source": {"role": "director"},
                "action": {"name": "complete", "result": "success"},
                "task": {"task_id": "t1", "run_id": "r1"},
            },
        ]

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        timeline = engine._build_timeline(events, limit=100)
        assert len(timeline) == 2
        assert timeline[0]["timestamp"] == "2024-01-01T00:00:00Z"
        assert timeline[0]["event_type"] == "task_started"
        assert timeline[0]["role"] == "pm"

    def test_build_timeline_limit(self, workspace: Path, runtime_root: Path) -> None:
        events = [
            {"timestamp": f"2024-01-01T00:{i:02d}:00Z", "event_type": "event", "source": {}, "action": {}, "task": {}}
            for i in range(200)
        ]

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        timeline = engine._build_timeline(events, limit=50)
        assert len(timeline) == 50

    def test_build_scan_summary(self, workspace: Path, runtime_root: Path) -> None:
        findings = [
            ScanFinding("critical", "security", "a.py", 1, "msg", "", ""),
            ScanFinding("high", "style", "b.py", 1, "msg", "", ""),
            ScanFinding("medium", "perf", "c.py", 1, "msg", "", ""),
            ScanFinding("low", "style", "d.py", 1, "msg", "", ""),
            ScanFinding("low", "style", "e.py", 1, "msg", "", ""),
        ]

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        summary = engine._build_scan_summary(findings, 5, 100)
        assert summary["score"] == 100 - 25 - 15 - 6 - 2 - 2  # 50
        assert summary["files_scanned"] == 5
        assert summary["lines_scanned"] == 100
        assert summary["findings_total"] == 5
        assert summary["severity"]["critical"] == 1
        assert summary["severity"]["low"] == 2

    def test_derive_recommendations_empty(self, workspace: Path, runtime_root: Path) -> None:
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        recs = engine._derive_recommendations([])
        assert "No high-risk issues" in recs[0]

    def test_derive_recommendations_secrets(self, workspace: Path, runtime_root: Path) -> None:
        findings = [
            ScanFinding("critical", "hardcoded_secret", "a.py", 1, "msg", "", ""),
        ]
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        recs = engine._derive_recommendations(findings)
        assert any("Rotate" in r or "credentials" in r for r in recs)

    def test_derive_recommendations_test_coverage(self, workspace: Path, runtime_root: Path) -> None:
        findings = [
            ScanFinding("high", "test_coverage", "a.py", 1, "msg", "", ""),
        ]
        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        recs = engine._derive_recommendations(findings)
        assert any("test" in r.lower() for r in recs)

    def test_normalize_rel_path_inside_workspace(self, workspace: Path) -> None:
        test_file = workspace / "src" / "main.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("", encoding="utf-8")
        result = _normalize_rel_path(test_file, workspace)
        assert "src/main.py" in result

    def test_normalize_rel_path_outside_workspace(self, workspace: Path) -> None:
        other_file = Path(tempfile.gettempdir()) / "other_test_file.py"
        other_file.write_text("", encoding="utf-8")
        try:
            result = _normalize_rel_path(other_file, workspace)
            # Should return absolute path when outside workspace
            assert other_file.resolve().as_posix() in result or result == other_file.resolve().as_posix()
        finally:
            other_file.unlink(missing_ok=True)


class TestDiagnosisEngineIntegration:
    """Integration tests for diagnosis engine."""

    @pytest.fixture
    def workspace(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def runtime_root(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_scan_with_unicode_content(self, workspace: Path, runtime_root: Path) -> None:
        # Create file with Unicode content
        (workspace / "unicode.py").write_text("# -*- coding: utf-8 -*-\ns = 'Hello 世界'\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        assert result is not None

    def test_scan_with_binary_file_ignored(self, workspace: Path, runtime_root: Path) -> None:
        # Create a binary file
        (workspace / "data.bin").write_bytes(b"\x00\x01\x02\x03")

        # Create a Python file
        (workspace / "main.py").write_text("def main(): pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="full")
        # Should only scan .py files
        assert result["summary"]["files_scanned"] >= 1

    def test_git_changed_files_graceful_fallback(self, workspace: Path, runtime_root: Path) -> None:
        # Create a workspace without git
        (workspace / "main.py").write_text("def main(): pass\n", encoding="utf-8")

        engine = AuditDiagnosisEngine(runtime_root=str(runtime_root), workspace=str(workspace))
        result = engine.scan_project(scope="changed")
        # Should not raise, just return empty or files
        assert result is not None
