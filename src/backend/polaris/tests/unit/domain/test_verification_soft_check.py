"""Tests for polaris.domain.verification.soft_check."""

from __future__ import annotations

import os
import tempfile

from polaris.domain.verification.soft_check import (
    SoftCheck,
    SoftCheckResult,
    check_missing_targets,
    detect_unresolved_imports,
    normalize_paths,
)


class TestSoftCheckResult:
    def test_verify_ready_true(self) -> None:
        result = SoftCheckResult(missing_targets=[], unresolved_imports=[], files_created=[])
        assert result.verify_ready is True

    def test_verify_ready_false(self) -> None:
        result = SoftCheckResult(missing_targets=["a.py"], unresolved_imports=[], files_created=[])
        assert result.verify_ready is False

    def test_has_issues(self) -> None:
        result = SoftCheckResult(missing_targets=["a.py"], unresolved_imports=[], files_created=[])
        assert result.has_issues is True

    def test_get_summary_all_clear(self) -> None:
        result = SoftCheckResult(missing_targets=[], unresolved_imports=[], files_created=[])
        assert result.get_summary() == "All checks passed"

    def test_get_summary_with_issues(self) -> None:
        result = SoftCheckResult(
            missing_targets=["a.py"],
            unresolved_imports=["b.py:foo"],
            files_created=[],
        )
        assert "missing targets" in result.get_summary()
        assert "unresolved imports" in result.get_summary()


class TestCheckMissingTargets:
    def test_all_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.py"), "w").close()
            missing = check_missing_targets(["a.py"], tmpdir)
            assert missing == []

    def test_some_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = check_missing_targets(["a.py"], tmpdir)
            assert missing == ["a.py"]

    def test_empty_paths_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = check_missing_targets(["", "a.py"], tmpdir)
            assert missing == ["a.py"]


class TestDetectUnresolvedImports:
    def test_js_import_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the imported file
            open(os.path.join(tmpdir, "helper.js"), "w").close()
            source = os.path.join(tmpdir, "main.js")
            with open(source, "w", encoding="utf-8") as f:
                f.write('import { foo } from "./helper";\n')
            unresolved = detect_unresolved_imports("main.js", tmpdir)
            assert unresolved == []

    def test_js_import_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, "main.js")
            with open(source, "w", encoding="utf-8") as f:
                f.write('import { foo } from "./missing";\n')
            unresolved = detect_unresolved_imports("main.js", tmpdir)
            assert len(unresolved) == 1
            assert "missing" in unresolved[0]

    def test_python_relative_import_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "helper.py"), "w").close()
            source = os.path.join(tmpdir, "main.py")
            with open(source, "w", encoding="utf-8") as f:
                f.write("from helper import foo\n")
            unresolved = detect_unresolved_imports("main.py", tmpdir)
            assert unresolved == []

    def test_python_relative_import_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, "main.py")
            with open(source, "w", encoding="utf-8") as f:
                f.write("from .missing import foo\n")
            unresolved = detect_unresolved_imports("main.py", tmpdir)
            assert len(unresolved) == 1
            assert "missing" in unresolved[0]

    def test_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            unresolved = detect_unresolved_imports("nonexistent.py", tmpdir)
            assert unresolved == []

    def test_non_js_py_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, "main.rs")
            with open(source, "w", encoding="utf-8") as f:
                f.write("use std::io;\n")
            unresolved = detect_unresolved_imports("main.rs", tmpdir)
            assert unresolved == []


class TestSoftCheck:
    def test_check_all_good(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.py"), "w").close()
            checker = SoftCheck(tmpdir)
            result = checker.check(target_files=["a.py"], changed_files=["a.py"])
            assert result.missing_targets == []
            assert result.unresolved_imports == []
            assert result.files_created == ["a.py"]

    def test_check_missing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = SoftCheck(tmpdir)
            result = checker.check(target_files=["a.py"])
            assert result.missing_targets == ["a.py"]

    def test_check_no_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = SoftCheck(tmpdir)
            result = checker.check(target_files=[], changed_files=None)
            assert result.files_created == []


class TestNormalizePaths:
    def test_dedupes(self) -> None:
        assert normalize_paths(["a.py", "a.py", "b.py"]) == ["a.py", "b.py"]

    def test_filters_empty(self) -> None:
        assert normalize_paths(["", "  ", "a.py"]) == ["a.py"]
