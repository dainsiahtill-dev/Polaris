"""Tests for polaris.bootstrap.ci_dependency_check."""

from __future__ import annotations

from pathlib import Path

from polaris.bootstrap.ci_dependency_check import DependencyChecker, Violation


class TestViolation:
    def test_dataclass_fields(self) -> None:
        v = Violation(
            file_path="test.py",
            line_number=1,
            import_line="from polaris.cells.a.internal import x",
            source_cell="a",
            target_cell="b",
            violation_type="cross_cell_internal",
        )
        assert v.source_cell == "a"
        assert v.target_cell == "b"


class TestDependencyChecker:
    def test_get_cell_name(self) -> None:
        checker = DependencyChecker()
        path = Path("polaris/cells/foo/internal/bar.py")
        assert checker._get_cell_name(path) == "foo"

    def test_get_cell_name_no_cells(self) -> None:
        checker = DependencyChecker()
        path = Path("polaris/bootstrap/config.py")
        assert checker._get_cell_name(path) == "unknown"

    def test_check_file_no_violations(self, tmp_path: Path) -> None:
        checker = DependencyChecker(base_path=str(tmp_path))
        f = tmp_path / "test.py"
        f.write_text("from polaris.cells.foo.public import x\n", encoding="utf-8")
        violations = checker.check_file(f)
        assert violations == []

    def test_check_file_cross_cell_internal(self, tmp_path: Path) -> None:
        checker = DependencyChecker(base_path=str(tmp_path))
        cell_a = tmp_path / "a" / "internal"
        cell_a.mkdir(parents=True)
        # The checker resolves source_cell from the absolute path, which on Windows
        # will not contain "cells" when tmp_path is outside the repo. We write the
        # file inside a fake "polaris/cells/a" tree so _get_cell_name resolves to "a".
        real_root = tmp_path / "polaris" / "cells" / "a" / "internal"
        real_root.mkdir(parents=True)
        f2 = real_root / "test.py"
        f2.write_text("from polaris.cells.b.internal.service import x\n", encoding="utf-8")
        violations = checker.check_file(f2)
        assert len(violations) == 1
        assert violations[0].violation_type == "cross_cell_internal"

    def test_check_all(self, tmp_path: Path) -> None:
        checker = DependencyChecker(base_path=str(tmp_path))
        real_root = tmp_path / "polaris" / "cells" / "a" / "internal"
        real_root.mkdir(parents=True)
        f = real_root / "test.py"
        f.write_text("from polaris.cells.b.internal.service import x\n", encoding="utf-8")
        violations = checker.check_all(include_tests=True)
        assert len(violations) == 1

    def test_check_file_cross_cell_internal_public(self, tmp_path: Path) -> None:
        checker = DependencyChecker(base_path=str(tmp_path))
        real_root = tmp_path / "polaris" / "cells" / "a" / "internal"
        real_root.mkdir(parents=True)
        f = real_root / "test.py"
        f.write_text("from polaris.cells.b.public.service import x\n", encoding="utf-8")
        violations = checker.check_file(f)
        assert len(violations) == 0

    def test_main_no_violations(self, tmp_path: Path) -> None:
        checker = DependencyChecker(base_path=str(tmp_path))
        assert checker.check_all() == []
        assert checker.print_report() is None
