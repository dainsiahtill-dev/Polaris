"""Tests for polaris.domain.verification.write_gate."""

from __future__ import annotations

from polaris.domain.verification.write_gate import (
    WriteGate,
    WriteGateResult,
    _normalize_paths,
    _scope_matches,
    validate_write_scope,
)


class TestWriteGateResult:
    def test_post_init(self) -> None:
        result = WriteGateResult(allowed=True)
        assert result.extra_files == []


class TestWriteGateValidate:
    def test_allowed(self) -> None:
        result = WriteGate.validate(
            changed_files=["src/a.py"],
            act_files=["src/a.py", "src/b.py"],
        )
        assert result.allowed is True

    def test_no_changes_not_required(self) -> None:
        result = WriteGate.validate(
            changed_files=[],
            act_files=["src/a.py"],
            require_change=False,
        )
        assert result.allowed is True

    def test_no_changes_required(self) -> None:
        result = WriteGate.validate(
            changed_files=[],
            act_files=["src/a.py"],
            require_change=True,
        )
        assert result.allowed is False
        assert "No files" in result.reason

    def test_extra_files(self) -> None:
        result = WriteGate.validate(
            changed_files=["src/a.py", "src/c.py"],
            act_files=["src/a.py"],
        )
        assert result.allowed is False
        assert "src/c.py" in (result.extra_files or [])

    def test_pm_scope_match(self) -> None:
        result = WriteGate.validate(
            changed_files=["src/a.py"],
            act_files=["src/a.py"],
            pm_target_files=["src"],
        )
        assert result.allowed is True

    def test_pm_scope_no_match(self) -> None:
        result = WriteGate.validate(
            changed_files=["other/b.py"],
            act_files=["other/b.py"],
            pm_target_files=["src"],
        )
        assert result.allowed is False
        assert "not within PM" in result.reason

    def test_companion_files_allowed(self) -> None:
        result = WriteGate.validate(
            changed_files=["src/a.test.py"],
            act_files=["src/a.test.py"],
            pm_target_files=["src/a.py"],
        )
        assert result.allowed is True


class TestValidateWriteScope:
    def test_convenience_function(self) -> None:
        result = validate_write_scope(["a.py"], ["a.py"])
        assert result.allowed is True


class TestNormalizePaths:
    def test_basic(self) -> None:
        assert _normalize_paths(["./a.py", "b.py"]) == ["a.py", "b.py"]

    def test_empty_strings_filtered(self) -> None:
        assert _normalize_paths(["", "a.py", ""]) == ["a.py"]

    def test_backslash_conversion(self) -> None:
        assert _normalize_paths(["src\\a.py"]) == ["src/a.py"]


class TestScopeMatches:
    def test_exact_match(self) -> None:
        assert _scope_matches("src/main.py", {"src/main.py"}) is True

    def test_directory_prefix(self) -> None:
        assert _scope_matches("src/utils/helper.py", {"src/utils"}) is True

    def test_wildcard(self) -> None:
        assert _scope_matches("anything.py", {"*"}) is True

    def test_no_match(self) -> None:
        assert _scope_matches("other.py", {"src"}) is False
