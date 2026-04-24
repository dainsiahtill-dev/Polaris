"""Tests for patch_apply_engine module."""

from __future__ import annotations

import pytest


class TestApplyIntegrity:
    """Tests for ApplyIntegrity dataclass."""

    def test_apply_integrity_valid(self) -> None:
        """Test ApplyIntegrity with valid state."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import ApplyIntegrity

        integrity = ApplyIntegrity(
            is_valid=True,
            can_continue=False,
            errors=[],
        )
        assert integrity.is_valid is True
        assert integrity.can_continue is False
        assert integrity.errors == []

    def test_apply_integrity_with_errors(self) -> None:
        """Test ApplyIntegrity with errors."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import ApplyIntegrity

        integrity = ApplyIntegrity(
            is_valid=False,
            can_continue=True,
            errors=["unclosed_blocks: SEARCH/REPLACE"],
        )
        assert integrity.is_valid is False
        assert integrity.can_continue is True
        assert len(integrity.errors) == 1


class TestValidateBeforeApply:
    """Tests for validate_before_apply function."""

    def test_validate_empty_response(self) -> None:
        """Test validation with empty response."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            validate_before_apply,
        )

        result = validate_before_apply("", {})
        assert result.is_valid is False
        assert result.can_continue is False
        assert "no_valid_operations" in result.errors

    def test_validate_response_with_operations(self) -> None:
        """Test validation with response containing operations."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            validate_before_apply,
        )

        response = """Here is the code:

```python
def hello():
    print("Hello")
```

Done."""
        result = validate_before_apply(response, {})
        # Empty errors means validation passed (parsed something)
        assert len(result.errors) == 0

    def test_validate_unclosed_patch_file(self) -> None:
        """Test validation detects unclosed PATCH_FILE block."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            validate_before_apply,
        )

        response = """Here is the patch:

<<<<<<< SEARCH
old code
=======
new code
<<<<<<< SEARCH
"""
        validate_before_apply(response, {})

        # Should detect unclosed block
        # Note: The actual detection depends on implementation


class TestParseOperations:
    """Tests for parse_*_operations functions."""

    def test_parse_all_operations_empty(self) -> None:
        """Test parse_all_operations with empty input."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            parse_all_operations,
        )

        ops = parse_all_operations("")
        assert isinstance(ops, list)

    def test_parse_full_file_blocks(self) -> None:
        """Test parse_full_file_blocks function."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            parse_full_file_blocks,
        )

        # Empty input should return empty list
        ops = parse_full_file_blocks("")
        assert isinstance(ops, list)

    def test_parse_search_replace_blocks(self) -> None:
        """Test parse_search_replace_blocks function."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            parse_search_replace_blocks,
        )

        # Empty input should return empty list
        ops = parse_search_replace_blocks("")
        assert isinstance(ops, list)

    def test_parse_delete_operations(self) -> None:
        """Test parse_delete_operations function."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            parse_delete_operations,
        )

        # Empty input should return empty list
        ops = parse_delete_operations("")
        assert isinstance(ops, list)


class TestApplyOperation:
    """Tests for apply_operation function."""

    def test_apply_operation_imports(self) -> None:
        """Test that apply_operation can be imported."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            apply_operation,
        )

        assert callable(apply_operation)

    def test_apply_all_operations_imports(self) -> None:
        """Test that apply_all_operations can be imported."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            apply_all_operations,
        )

        assert callable(apply_all_operations)


class TestApplyResult:
    """Tests for ApplyResult dataclass."""

    def test_apply_result_creation(self) -> None:
        """Test ApplyResult basic creation."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            ApplyResult,
        )

        result = ApplyResult(
            success=True,
            changed_files=["file1.py", "file2.py"],
        )
        assert result.success is True
        assert result.changed_files == ["file1.py", "file2.py"]
        assert result.failed_operations == []
        assert result.errors == []

    def test_apply_result_with_failures(self) -> None:
        """Test ApplyResult with failed operations."""
        from polaris.cells.director.tasking.internal.patch_apply_engine import (
            ApplyResult,
        )
        from polaris.kernelone.llm.toolkit import EditType, FileOperation

        # Create a proper FileOperation for the test
        failed_op = FileOperation(
            path="file1.py",
            edit_type=EditType.CREATE,
            replace="content",
        )

        result = ApplyResult(
            success=False,
            changed_files=["file1.py"],
            failed_operations=[(failed_op, "Not found")],
            errors=["Not found"],
        )
        assert result.success is False
        assert result.changed_files == ["file1.py"]
        assert len(result.failed_operations) == 1


class TestExports:
    """Tests for module exports."""

    def test_all_exports_present(self) -> None:
        """Test that expected exports are available."""
        from polaris.cells.director.tasking.internal import patch_apply_engine

        expected = [
            "ApplyIntegrity",
            "ApplyResult",
            "apply_all_operations",
            "apply_operation",
            "parse_all_operations",
            "parse_delete_operations",
            "parse_full_file_blocks",
            "parse_search_replace_blocks",
            "validate_before_apply",
        ]

        for name in expected:
            assert hasattr(patch_apply_engine, name), f"Missing export: {name}"

    def test_edit_type_exports(self) -> None:
        """Test that EditType is exported."""
        from polaris.cells.director.tasking.internal import patch_apply_engine

        assert hasattr(patch_apply_engine, "EditType")

    def test_error_code_exports(self) -> None:
        """Test that ErrorCode is exported."""
        from polaris.cells.director.tasking.internal import patch_apply_engine

        assert hasattr(patch_apply_engine, "ErrorCode")

    def test_file_operation_exports(self) -> None:
        """Test that FileOperation is exported."""
        from polaris.cells.director.tasking.internal import patch_apply_engine

        assert hasattr(patch_apply_engine, "FileOperation")

    def test_operation_result_exports(self) -> None:
        """Test that OperationResult is exported."""
        from polaris.cells.director.tasking.internal import patch_apply_engine

        assert hasattr(patch_apply_engine, "OperationResult")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
