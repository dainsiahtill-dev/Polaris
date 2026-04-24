"""Tests for polaris.bootstrap.dependency_scanner."""

from __future__ import annotations

from pathlib import Path

from polaris.bootstrap.dependency_scanner import DependencyScanner, ScanResult, Violation


class TestScanResult:
    def test_default_empty(self) -> None:
        result = ScanResult()
        assert result.violations == []
        assert result.type_a_count == 0
        assert result.type_b_count == 0
        assert result.type_c_count == 0

    def test_by_source_cell(self) -> None:
        result = ScanResult()
        result.violations = [
            Violation("a.py", 1, "line", "foo", "bar", "type_a"),
            Violation("b.py", 2, "line", "foo", "baz", "type_a"),
        ]
        by_source = result.by_source_cell()
        assert len(by_source["foo"]) == 2

    def test_by_target_cell(self) -> None:
        result = ScanResult()
        result.violations = [
            Violation("a.py", 1, "line", "foo", "bar", "type_a"),
        ]
        by_target = result.by_target_cell()
        assert len(by_target["bar"]) == 1


class TestDependencyScanner:
    def test_get_cell_name(self) -> None:
        scanner = DependencyScanner()
        path = Path("polaris/cells/foo/internal/bar.py")
        assert scanner._get_cell_name(path) == "foo"

    def test_get_target_cell(self) -> None:
        scanner = DependencyScanner()
        # The regex captures the first two dot-separated segments after polaris.cells
        assert scanner._get_target_cell("from polaris.cells.foo.public.service import x") == ("foo", "public")

    def test_get_target_cell_no_match(self) -> None:
        scanner = DependencyScanner()
        assert scanner._get_target_cell("import os") == ("", "")

    def test_is_violation_same_cell(self) -> None:
        scanner = DependencyScanner()
        path = Path("polaris/cells/foo/internal/bar.py")
        is_violation, _vtype = scanner._is_violation(path, "from polaris.cells.foo.public import x")
        assert is_violation is False

    def test_is_violation_type_a(self) -> None:
        scanner = DependencyScanner()
        path = Path("polaris/cells/foo/internal/bar.py")
        is_violation, vtype = scanner._is_violation(path, "from polaris.cells.bar.internal import x")
        assert is_violation is True
        assert vtype == "type_a"

    def test_scan_file_no_violations(self, tmp_path: Path) -> None:
        scanner = DependencyScanner(base_path=str(tmp_path))
        f = tmp_path / "test.py"
        f.write_text("from polaris.cells.foo.public import x\n", encoding="utf-8")
        violations = scanner.scan_file(f)
        assert violations == []

    def test_scan_file_with_violation(self, tmp_path: Path) -> None:
        scanner = DependencyScanner(base_path=str(tmp_path))
        cell_a = tmp_path / "a"
        cell_a.mkdir()
        f = cell_a / "test.py"
        f.write_text("from polaris.cells.b.internal import x\n", encoding="utf-8")
        violations = scanner.scan_file(f)
        assert len(violations) == 1
        assert violations[0].violation_type == "type_a"

    def test_main_no_violations(self, tmp_path: Path) -> None:
        scanner = DependencyScanner(base_path=str(tmp_path))
        result = scanner.scan()
        assert isinstance(result, ScanResult)
        assert result.violations == []
