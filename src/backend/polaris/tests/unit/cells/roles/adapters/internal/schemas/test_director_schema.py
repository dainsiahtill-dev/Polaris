"""Tests for polaris.cells.roles.adapters.internal.schemas.director_schema."""

from __future__ import annotations

import pytest
from polaris.cells.roles.adapters.internal.schemas.base import ToolCall
from polaris.cells.roles.adapters.internal.schemas.director_schema import (
    DirectorOutput,
    DirectorValidationResult,
    PatchOperation,
    ValidationResult,
)
from pydantic import ValidationError


class TestPatchOperation:
    def test_valid_patch_operation(self) -> None:
        patch = PatchOperation(file="src/app.py", search="old_code", replace="new_code")
        assert patch.file == "src/app.py"
        assert patch.search == "old_code"
        assert patch.replace == "new_code"

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PatchOperation(file="", search="old", replace="new")
        assert "File path cannot be empty" in str(exc_info.value)

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PatchOperation(file="/absolute/path", search="old", replace="new")
        assert "File path must be relative" in str(exc_info.value)

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PatchOperation(file="../etc/passwd", search="old", replace="new")
        assert "traversal pattern" in str(exc_info.value)

    def test_dangerous_path_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PatchOperation(file=";rm -rf", search="old", replace="new")
        assert "dangerous" in str(exc_info.value).lower()

    def test_new_file_empty_search(self) -> None:
        patch = PatchOperation(file="new_file.py", search="", replace="# new content")
        assert patch.search == ""
        assert patch.replace == "# new content"


class TestDirectorValidationResult:
    def test_valid_validation_result(self) -> None:
        result = DirectorValidationResult(passed=True, command="pytest", output="OK")
        assert result.passed is True
        assert result.command == "pytest"
        assert result.output == "OK"
        assert result.error is None

    def test_failed_result_with_error(self) -> None:
        result = DirectorValidationResult(passed=False, error="Test failed")
        assert result.passed is False
        assert result.error == "Test failed"

    def test_validation_result_alias(self) -> None:
        """ValidationResult is an alias for DirectorValidationResult."""
        result = ValidationResult(passed=True)
        assert isinstance(result, DirectorValidationResult)
        assert result.passed is True


class TestDirectorOutput:
    def test_valid_patch_mode(self) -> None:
        patch = PatchOperation(file="a.py", search="x", replace="y")
        output = DirectorOutput(
            mode="patch",
            summary="Updated file a.py",
            patches=[patch],
        )
        assert output.mode == "patch"
        assert len(output.patches) == 1

    def test_valid_tool_calls_mode(self) -> None:
        tool_call = ToolCall(tool="read_file", arguments={"path": "a.py"})
        output = DirectorOutput(
            mode="tool_calls",
            summary="Reading files for analysis",
            tool_calls=[tool_call],
        )
        assert output.mode == "tool_calls"
        assert len(output.tool_calls) == 1

    def test_valid_mixed_mode(self) -> None:
        patch = PatchOperation(file="a.py", search="x", replace="y")
        tool_call = ToolCall(tool="read_file", arguments={"path": "a.py"})
        output = DirectorOutput(
            mode="mixed",
            summary="Updated and read files",
            patches=[patch],
            tool_calls=[tool_call],
        )
        assert output.mode == "mixed"
        assert output.patches and output.tool_calls

    def test_patch_mode_requires_patches(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DirectorOutput(mode="patch", summary="No patches provided")
        assert "requires at least one patch" in str(exc_info.value)

    def test_tool_calls_mode_requires_tool_calls(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DirectorOutput(mode="tool_calls", summary="No tool calls provided")
        assert "requires at least one tool call" in str(exc_info.value)

    def test_mixed_mode_requires_either(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DirectorOutput(mode="mixed", summary="Nothing provided")
        assert "requires patches or tool calls" in str(exc_info.value)

    def test_duplicate_patch_rejected(self) -> None:
        patch1 = PatchOperation(file="a.py", search="x", replace="y")
        patch2 = PatchOperation(file="a.py", search="x", replace="z")
        with pytest.raises(ValidationError) as exc_info:
            DirectorOutput(mode="patch", summary="Dup patches", patches=[patch1, patch2])
        assert "Duplicate patch" in str(exc_info.value)

    def test_next_steps_optional(self) -> None:
        output = DirectorOutput(
            mode="patch",
            summary="Test summary that is long enough",
            patches=[PatchOperation(file="a.py", search="x", replace="y")],
            next_steps=["Run tests", "Commit changes"],
        )
        assert len(output.next_steps) == 2

    def test_validation_result_optional(self) -> None:
        output = DirectorOutput(
            mode="patch",
            summary="Test summary that is long enough",
            patches=[PatchOperation(file="a.py", search="x", replace="y")],
            validation=DirectorValidationResult(passed=True),
        )
        assert output.validation is not None
        assert output.validation.passed is True
