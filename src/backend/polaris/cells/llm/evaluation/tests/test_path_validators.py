"""Tests for path traversal vulnerability validators.

This test module covers:
- Normal valid inputs
- Boundary cases (empty strings, max length)
- Path traversal attack patterns
- Edge cases and special characters
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from polaris.cells.llm.evaluation.internal.path_validators import (
    DANGEROUS_PATH_CHARS,
    MAX_IDENTIFIER_LENGTH,
    PathTraversalError,
    validate_base_workspace,
    validate_case_id,
    validate_run_id,
    validate_workspace_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Tests for validate_workspace_fixture
# =============================================================================


class TestValidateWorkspaceFixture:
    """Tests for workspace_fixture validation."""

    def test_valid_simple_name(self) -> None:
        """Valid simple fixture name should pass."""
        result = validate_workspace_fixture("my_fixture")
        assert result == "my_fixture"

    def test_valid_with_underscore_and_numbers(self) -> None:
        """Valid fixture name with underscores and numbers."""
        result = validate_workspace_fixture("test_fixture_123")
        assert result == "test_fixture_123"

    def test_valid_with_hyphen(self) -> None:
        """Valid fixture name with hyphens."""
        result = validate_workspace_fixture("workspace-name-456")
        assert result == "workspace-name-456"

    def test_valid_mixed_case(self) -> None:
        """Valid fixture name with mixed case."""
        result = validate_workspace_fixture("Test_Workspace_ABC")
        assert result == "Test_Workspace_ABC"

    def test_empty_string_returns_empty(self) -> None:
        """Empty string should return empty string."""
        result = validate_workspace_fixture("")
        assert result == ""

    def test_whitespace_only_returns_empty(self) -> None:
        """Whitespace-only should return empty string."""
        result = validate_workspace_fixture("   ")
        assert result == ""

    def test_strips_whitespace(self) -> None:
        """Valid fixture name with surrounding whitespace should be stripped."""
        result = validate_workspace_fixture("  valid_fixture  ")
        assert result == "valid_fixture"

    def test_rejects_double_dot(self) -> None:
        """Double dot pattern should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("..")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_double_dot_path(self) -> None:
        """Path with double dot should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("../etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_absolute_path(self) -> None:
        """Absolute path should be rejected for workspace_fixture."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("/etc/passwd")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_absolute_path_starting_with_drive(self) -> None:
        """Windows absolute path should be rejected for workspace_fixture."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("C:\\etc\\passwd")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_leading_dot_slash(self) -> None:
        """Leading ./ pattern should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("./safe")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_leading_dot_dot_slash(self) -> None:
        """Leading ../ pattern should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("../safe")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_home_directory(self) -> None:
        """Home directory pattern should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("~/safe")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_null_byte(self) -> None:
        """Null byte should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("fixture\x00name")
        assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_shell_metacharacters(self) -> None:
        """Shell metacharacters should be rejected."""
        for char in ["$", "`", "|", ";", ">", "<", '"', "'"]:
            with pytest.raises(PathTraversalError) as exc_info:
                validate_workspace_fixture(f"fixture{char}name")
            assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_control_characters(self) -> None:
        """Control characters should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("fixture\x01name")
        assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_plus_sign(self) -> None:
        """Plus sign should be rejected (not in safe pattern)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("fix+ture")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_space(self) -> None:
        """Space character should be rejected (not in safe pattern)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("fix ture")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_at_sign(self) -> None:
        """At sign should be rejected (not in safe pattern)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("fix@ture")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_exceeds_max_length(self) -> None:
        """Identifier exceeding max length should be rejected."""
        long_name = "a" * (MAX_IDENTIFIER_LENGTH + 1)
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture(long_name)
        assert "maximum length" in str(exc_info.value).lower()

    def test_accepts_max_length(self) -> None:
        """Identifier at max length should be accepted."""
        max_name = "a" * MAX_IDENTIFIER_LENGTH
        result = validate_workspace_fixture(max_name)
        assert result == max_name

    def test_path_traversal_encoded_double_dot(self) -> None:
        """URL-encoded double dot should be rejected."""
        with pytest.raises(PathTraversalError):
            validate_workspace_fixture("..%2F..%2Fetc")

    def test_error_includes_field_name(self) -> None:
        """Error message should include field name."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("../etc", field_name="custom_field")
        assert "custom_field" in str(exc_info.value)

    def test_error_includes_value(self) -> None:
        """Error should include the invalid value."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("../etc")
        assert exc_info.value.value == "../etc"


# =============================================================================
# Tests for validate_run_id
# =============================================================================


class TestValidateRunId:
    """Tests for run_id validation."""

    def test_valid_simple_id(self) -> None:
        """Valid simple run ID should pass."""
        result = validate_run_id("run123")
        assert result == "run123"

    def test_valid_with_hyphen(self) -> None:
        """Valid run ID with hyphen should pass."""
        result = validate_run_id("run-001")
        assert result == "run-001"

    def test_valid_with_underscore(self) -> None:
        """Valid run ID with underscore should pass."""
        result = validate_run_id("run_001")
        assert result == "run_001"

    def test_valid_uuid_style(self) -> None:
        """UUID-style run ID should pass."""
        result = validate_run_id("a1b2c3d4")
        assert result == "a1b2c3d4"

    def test_strips_whitespace(self) -> None:
        """Run ID with whitespace should be stripped."""
        result = validate_run_id("  run123  ")
        assert result == "run123"

    def test_rejects_empty_string(self) -> None:
        """Empty string should be rejected (not allowed for run_id)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_rejects_whitespace_only(self) -> None:
        """Whitespace-only should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("   ")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_rejects_double_dot(self) -> None:
        """Double dot should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("run..123")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_path_traversal(self) -> None:
        """Path traversal pattern should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("../../../etc")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_absolute_path(self) -> None:
        """Absolute path should be rejected for run_id."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("/run/123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_windows_absolute_path(self) -> None:
        """Windows absolute path should be rejected for run_id."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("C:\\run\\123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_shell_metacharacters(self) -> None:
        """Shell metacharacters should be rejected."""
        for char in ["$", "`", "|", ";", ">", "<"]:
            with pytest.raises(PathTraversalError) as exc_info:
                validate_run_id(f"run{char}123")
            assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_dot(self) -> None:
        """Single dot should be rejected (not in safe pattern)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("run.123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_slash(self) -> None:
        """Slash should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("run/123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_space(self) -> None:
        """Space should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("run 123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_exceeds_max_length(self) -> None:
        """Identifier exceeding max length should be rejected."""
        long_id = "r" + "u" * (MAX_IDENTIFIER_LENGTH) + "n"  # Too long
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id(long_id)
        assert "maximum length" in str(exc_info.value).lower()

    def test_error_includes_field_name(self) -> None:
        """Error message should include field name."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_run_id("", field_name="custom_run_id")
        assert "custom_run_id" in str(exc_info.value)


# =============================================================================
# Tests for validate_base_workspace
# =============================================================================


class TestValidateBaseWorkspace:
    """Tests for base_workspace validation."""

    def test_valid_existing_directory(self, tmp_path: Path) -> None:
        """Valid existing directory should pass."""
        result = validate_base_workspace(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_valid_existing_directory_with_subpath(self, tmp_path: Path) -> None:
        """Valid existing directory with subpath should pass."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = validate_base_workspace(str(subdir))
        assert result == subdir.resolve()

    def test_rejects_empty_string(self) -> None:
        """Empty string should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_rejects_whitespace_only(self) -> None:
        """Whitespace-only should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("   ")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_accepts_absolute_path(self, tmp_path: Path) -> None:
        """Absolute path should be accepted for base_workspace."""
        # Using an existing path for validation
        result = validate_base_workspace(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_accepts_absolute_nonexistent_path(self) -> None:
        """Absolute path to nonexistent location should be accepted when must_exist=False."""
        result = validate_base_workspace(
            "/nonexistent/path/workspace",
            must_exist=False,
        )
        assert result is not None

    def test_rejects_path_traversal(self) -> None:
        """Path traversal should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("../../../etc")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_absolute_path_to_sensitive_location(self) -> None:
        """Absolute path with path traversal should be rejected."""
        # Note: absolute paths are allowed for base_workspace (user's workspace can be anywhere)
        # But paths with traversal patterns should be rejected
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("/etc/../other")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_shell_metacharacters(self) -> None:
        """Shell metacharacters should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("/path;ls")
        assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_null_byte(self) -> None:
        """Null byte should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("/path\x00name")
        assert "dangerous" in str(exc_info.value).lower()

    def test_accepts_nonexistent_when_must_exist_false(self) -> None:
        """Nonexistent path should be accepted when must_exist=False."""
        result = validate_base_workspace(
            "/nonexistent/path/workspace",
            must_exist=False,
        )
        assert result is not None

    def test_accepts_file_when_must_be_dir_false(self, tmp_path: Path) -> None:
        """File path should be accepted when must_be_dir=False."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content", encoding="utf-8")
        result = validate_base_workspace(
            str(test_file),
            must_exist=True,
            must_be_dir=False,
        )
        assert result == test_file.resolve()

    def test_rejects_file_when_must_be_dir_true(self, tmp_path: Path) -> None:
        """File path should be rejected when must_be_dir=True."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content", encoding="utf-8")
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace(str(test_file), must_be_dir=True)
        assert "not a directory" in str(exc_info.value).lower()

    def test_rejects_nonexistent_when_must_exist_true(self) -> None:
        """Nonexistent path should be rejected when must_exist=True."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("/nonexistent/path", must_exist=True)
        assert "does not exist" in str(exc_info.value).lower()

    def test_resolves_relative_path(self, tmp_path: Path) -> None:
        """Relative path should be resolved to absolute."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = validate_base_workspace(".", must_exist=False)
            assert result == tmp_path.resolve()
        finally:
            os.chdir(original_cwd)

    def test_error_includes_field_name(self) -> None:
        """Error message should include field name."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_base_workspace("", field_name="custom_workspace")
        assert "custom_workspace" in str(exc_info.value)


# =============================================================================
# Tests for validate_case_id
# =============================================================================


class TestValidateCaseId:
    """Tests for case_id validation."""

    def test_valid_simple_id(self) -> None:
        """Valid simple case ID should pass."""
        result = validate_case_id("case123")
        assert result == "case123"

    def test_valid_with_hyphen(self) -> None:
        """Valid case ID with hyphen should pass."""
        result = validate_case_id("case-001")
        assert result == "case-001"

    def test_valid_with_underscore(self) -> None:
        """Valid case ID with underscore should pass."""
        result = validate_case_id("case_001")
        assert result == "case_001"

    def test_strips_whitespace(self) -> None:
        """Case ID with whitespace should be stripped."""
        result = validate_case_id("  case123  ")
        assert result == "case123"

    def test_rejects_empty_string(self) -> None:
        """Empty string should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id("")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_rejects_whitespace_only(self) -> None:
        """Whitespace-only should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id("   ")
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_rejects_path_traversal(self) -> None:
        """Path traversal should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id("../../../etc")
        assert "path traversal" in str(exc_info.value).lower()

    def test_rejects_dot(self) -> None:
        """Dot should be rejected (not in safe pattern)."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id("case.123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_slash(self) -> None:
        """Slash should be rejected."""
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id("case/123")
        assert "invalid characters" in str(exc_info.value).lower()

    def test_rejects_shell_metacharacters(self) -> None:
        """Shell metacharacters should be rejected."""
        for char in ["$", "`", "|", ";"]:
            with pytest.raises(PathTraversalError) as exc_info:
                validate_case_id(f"case{char}123")
            assert "dangerous" in str(exc_info.value).lower()

    def test_rejects_exceeds_max_length(self) -> None:
        """Identifier exceeding max length should be rejected."""
        long_id = "c" * (MAX_IDENTIFIER_LENGTH + 1)
        with pytest.raises(PathTraversalError) as exc_info:
            validate_case_id(long_id)
        assert "maximum length" in str(exc_info.value).lower()


# =============================================================================
# Tests for PathTraversalError
# =============================================================================


class TestPathTraversalError:
    """Tests for PathTraversalError exception."""

    def test_error_attributes(self) -> None:
        """Error should have correct attributes."""
        error = PathTraversalError(
            message="test message",
            field_name="test_field",
            value="test_value",
            reason="test_reason",
        )
        assert error.field_name == "test_field"
        assert error.value == "test_value"
        assert error.reason == "test_reason"
        assert "test message" in str(error)

    def test_error_repr(self) -> None:
        """Error repr should include key attributes."""
        error = PathTraversalError(
            message="test message",
            field_name="test_field",
            value="test_value",
            reason="test_reason",
        )
        assert "test_field" in repr(error)
        assert "test_value" in repr(error)


# =============================================================================
# Integration tests
# =============================================================================


class TestPathValidatorsIntegration:
    """Integration tests for path validators."""

    def test_realistic_path_traversal_attacks(self) -> None:
        """Test common path traversal attack patterns."""
        attack_patterns = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//....//etc/passwd",
            "/etc/passwd",
            "\\\\ UNC \\\\path",
            "~/./.ssh/authorized_keys",
            "${HOME}/.ssh",
            "|/bin/bash",
            "; rm -rf /",
            "`whoami`",
            "$(cat /etc/passwd)",
        ]
        for pattern in attack_patterns:
            with pytest.raises(PathTraversalError):
                validate_workspace_fixture(pattern)
            with pytest.raises(PathTraversalError):
                validate_run_id(pattern)

    def test_valid_patterns_for_workspace_fixture(self) -> None:
        """Test valid patterns that should be accepted."""
        valid_patterns = [
            "workspace1",
            "test_workspace",
            "workspace-name",
            "WORKSPACE123",
            "a",
            "A",
            "0",
            "workspace_123-test",
        ]
        for pattern in valid_patterns:
            result = validate_workspace_fixture(pattern)
            assert result == pattern

    def test_valid_patterns_for_run_id(self) -> None:
        """Test valid patterns for run_id."""
        valid_patterns = [
            "run123",
            "test_run",
            "run-001",
            "RUN123",
            "a",
            "1",
            "run_123-test",
        ]
        for pattern in valid_patterns:
            result = validate_run_id(pattern)
            assert result == pattern


# =============================================================================
# Constants tests
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_max_identifier_length_positive(self) -> None:
        """MAX_IDENTIFIER_LENGTH should be positive."""
        assert MAX_IDENTIFIER_LENGTH > 0

    def test_max_identifier_length_reasonable(self) -> None:
        """MAX_IDENTIFIER_LENGTH should be reasonable."""
        assert 16 <= MAX_IDENTIFIER_LENGTH <= 1024

    def test_dangerous_chars_pattern_compiled(self) -> None:
        """DANGEROUS_PATH_CHARS should be a compiled pattern."""
        assert hasattr(DANGEROUS_PATH_CHARS, "search")
        assert hasattr(DANGEROUS_PATH_CHARS, "match")
