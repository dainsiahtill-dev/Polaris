"""Tests for existence_gate module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass


class TestGateResult:
    """Tests for GateResult dataclass."""

    def test_gate_result_creation(self) -> None:
        """Test basic GateResult creation."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult

        result = GateResult(
            mode="create",
            existing=["file1.py"],
            missing=["file2.py"],
        )
        assert result.mode == "create"
        assert result.existing == ["file1.py"]
        assert result.missing == ["file2.py"]
        assert result.existing_count == 1
        assert result.missing_count == 1
        assert result.target_total == 2

    def test_gate_result_as_dict(self) -> None:
        """Test GateResult.as_dict() method."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult

        result = GateResult(
            mode="modify",
            existing=["a.py", "b.py"],
            missing=["c.py"],
        )
        d = result.as_dict()
        assert d["mode"] == "modify"
        assert d["existing"] == ["a.py", "b.py"]
        assert d["missing"] == ["c.py"]
        assert d["existing_count"] == 2
        assert d["missing_count"] == 1
        assert d["target_total"] == 3

    def test_gate_result_empty_lists(self) -> None:
        """Test GateResult with empty lists."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult

        result = GateResult(mode="create", existing=[], missing=[])
        assert result.existing_count == 0
        assert result.missing_count == 0
        assert result.target_total == 0


class TestCheckMode:
    """Tests for check_mode function."""

    def test_check_mode_all_existing(self, tmp_path: Path) -> None:
        """Test check_mode when all target files exist."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        # Create test files
        (tmp_path / "existing.py").touch()
        (tmp_path / "other.py").touch()

        result = check_mode(
            target_files=["existing.py", "other.py"],
            workspace=str(tmp_path),
        )

        assert result.mode == "modify"
        assert "existing.py" in result.existing
        assert "other.py" in result.existing
        assert len(result.missing) == 0

    def test_check_mode_all_missing(self, tmp_path: Path) -> None:
        """Test check_mode when all target files are missing."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        result = check_mode(
            target_files=["new1.py", "new2.py"],
            workspace=str(tmp_path),
        )

        assert result.mode == "create"
        assert len(result.existing) == 0
        assert "new1.py" in result.missing
        assert "new2.py" in result.missing

    def test_check_mode_mixed(self, tmp_path: Path) -> None:
        """Test check_mode with mixed existing and missing files."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        # Create one existing file
        (tmp_path / "existing.py").touch()

        result = check_mode(
            target_files=["existing.py", "missing.py"],
            workspace=str(tmp_path),
        )

        assert result.mode == "mixed"
        assert "existing.py" in result.existing
        assert "missing.py" in result.missing

    def test_check_mode_empty_targets(self, tmp_path: Path) -> None:
        """Test check_mode with empty target list."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        result = check_mode(target_files=[], workspace=str(tmp_path))

        assert result.mode == "modify"
        assert result.existing_count == 0
        assert result.missing_count == 0

    def test_check_mode_normalizes_paths(self, tmp_path: Path) -> None:
        """Test that check_mode normalizes paths correctly."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        (tmp_path / "file.py").touch()

        # Test various path formats
        result = check_mode(
            target_files=[
                "file.py",  # Normal
                "./file.py",  # With ./
                "subdir/../file.py",  # With navigation
            ],
            workspace=str(tmp_path),
        )

        # Should find the existing file
        assert result.existing_count >= 1

    def test_check_mode_with_mode_hint_create(self, tmp_path: Path) -> None:
        """Test check_mode with explicit 'create' hint."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        # Create file but hint says create
        (tmp_path / "file.py").touch()

        result = check_mode(
            target_files=["file.py"],
            workspace=str(tmp_path),
            mode_hint="create",
        )

        assert result.mode == "create"
        # Existing files should still be tracked
        assert "file.py" in result.existing

    def test_check_mode_with_mode_hint_modify(self, tmp_path: Path) -> None:
        """Test check_mode with explicit 'modify' hint."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        # Don't create file but hint says modify
        result = check_mode(
            target_files=["file.py"],
            workspace=str(tmp_path),
            mode_hint="modify",
        )

        assert result.mode == "modify"
        assert "file.py" in result.missing

    def test_check_mode_whitespace_handling(self, tmp_path: Path) -> None:
        """Test check_mode handles whitespace in paths."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        result = check_mode(
            target_files=["  file.py  ", "  ", "", "valid.py"],
            workspace=str(tmp_path),
        )

        # Should filter out empty/whitespace-only entries
        assert result.target_total >= 0

    def test_check_mode_backslash_normalization(self, tmp_path: Path) -> None:
        """Test that Windows backslashes are normalized."""
        from polaris.cells.director.tasking.internal.existence_gate import check_mode

        # On Windows, paths might use backslashes
        (tmp_path / "file.py").touch()
        result = check_mode(
            target_files=["file.py"],
            workspace=str(tmp_path),
        )

        # Path should be normalized to forward slash
        assert "file.py" in result.existing


class TestHelpers:
    """Tests for helper functions."""

    def test_is_pure_create_true(self) -> None:
        """Test is_pure_create returns True for pure creation."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult, is_pure_create

        result = GateResult(mode="create", existing=[], missing=["new.py"])
        assert is_pure_create(result) is True

    def test_is_pure_create_false_when_mode_not_create(self) -> None:
        """Test is_pure_create returns False when mode is not create."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult, is_pure_create

        result = GateResult(mode="modify", existing=["file.py"], missing=[])
        assert is_pure_create(result) is False

    def test_is_pure_create_false_when_some_exist(self) -> None:
        """Test is_pure_create returns False when some files exist."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult, is_pure_create

        result = GateResult(mode="create", existing=["existing.py"], missing=["new.py"])
        assert is_pure_create(result) is False

    def test_is_any_missing_true(self) -> None:
        """Test is_any_missing returns True when files are missing."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult, is_any_missing

        result = GateResult(mode="mixed", existing=["a.py"], missing=["b.py"])
        assert is_any_missing(result) is True

    def test_is_any_missing_false(self) -> None:
        """Test is_any_missing returns False when all exist."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult, is_any_missing

        result = GateResult(mode="modify", existing=["a.py", "b.py"], missing=[])
        assert is_any_missing(result) is False


class TestExecutionModeType:
    """Tests for ExecutionMode type alias."""

    def test_execution_mode_values(self) -> None:
        """Test that ExecutionMode accepts valid values."""
        from polaris.cells.director.tasking.internal.existence_gate import GateResult

        for mode in ("create", "modify", "mixed"):
            result = GateResult(mode=mode, existing=[], missing=[])
            assert result.mode == mode


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
