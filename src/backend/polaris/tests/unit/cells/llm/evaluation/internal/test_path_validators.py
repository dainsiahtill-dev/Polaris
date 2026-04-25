"""Unit tests for polaris.cells.llm.evaluation.internal.path_validators."""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.path_validators import (
    DANGEROUS_PATH_CHARS,
    MAX_IDENTIFIER_LENGTH,
    SAFE_IDENTIFIER_PATTERN,
    PathTraversalError,
    validate_base_workspace,
    validate_case_id,
    validate_run_id,
    validate_workspace_fixture,
)


class TestPathTraversalError:
    """Tests for PathTraversalError exception."""

    def test_attributes(self) -> None:
        exc = PathTraversalError(
            message="bad path",
            field_name="workspace_fixture",
            value="../etc",
            reason="contains traversal",
        )
        assert exc.field_name == "workspace_fixture"
        assert exc.value == "../etc"
        assert exc.reason == "contains traversal"
        assert "bad path" in str(exc)

    def test_repr(self) -> None:
        exc = PathTraversalError(
            message="bad path",
            field_name="workspace_fixture",
            value="../etc",
            reason="contains traversal",
        )
        assert "PathTraversalError" in repr(exc)
        assert "workspace_fixture" in repr(exc)


class TestValidateWorkspaceFixture:
    """Tests for validate_workspace_fixture."""

    def test_valid_fixture(self) -> None:
        assert validate_workspace_fixture("my_fixture") == "my_fixture"
        assert validate_workspace_fixture("safe_workspace_123") == "safe_workspace_123"

    def test_empty_returns_empty(self) -> None:
        assert validate_workspace_fixture("") == ""
        assert validate_workspace_fixture("   ") == ""

    def test_too_long(self) -> None:
        with pytest.raises(PathTraversalError, match="exceeds maximum length"):
            validate_workspace_fixture("a" * (MAX_IDENTIFIER_LENGTH + 1))

    def test_dangerous_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="dangerous characters"):
            validate_workspace_fixture("foo$bar")

    def test_traversal_pattern(self) -> None:
        with pytest.raises(PathTraversalError, match="path traversal pattern"):
            validate_workspace_fixture("foo../bar")

    def test_invalid_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="invalid characters"):
            validate_workspace_fixture("foo bar")

    def test_custom_field_name(self) -> None:
        with pytest.raises(PathTraversalError) as exc_info:
            validate_workspace_fixture("foo bar", field_name="custom")
        assert exc_info.value.field_name == "custom"


class TestValidateRunId:
    """Tests for validate_run_id."""

    def test_valid_run_id(self) -> None:
        assert validate_run_id("abc123") == "abc123"
        assert validate_run_id("run-001") == "run-001"

    def test_empty_run_id(self) -> None:
        with pytest.raises(PathTraversalError, match="cannot be empty"):
            validate_run_id("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(PathTraversalError, match="cannot be empty after normalization"):
            validate_run_id("   ")

    def test_too_long(self) -> None:
        with pytest.raises(PathTraversalError, match="exceeds maximum length"):
            validate_run_id("a" * (MAX_IDENTIFIER_LENGTH + 1))

    def test_dangerous_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="dangerous characters"):
            validate_run_id("run$id")

    def test_traversal_pattern(self) -> None:
        with pytest.raises(PathTraversalError, match="path traversal pattern"):
            validate_run_id("run../id")

    def test_invalid_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="invalid characters"):
            validate_run_id("run id")


class TestValidateBaseWorkspace:
    """Tests for validate_base_workspace."""

    def test_valid_path(self, tmp_path) -> None:
        result = validate_base_workspace(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_empty_path(self) -> None:
        with pytest.raises(PathTraversalError, match="cannot be empty"):
            validate_base_workspace("")

    def test_traversal_pattern(self) -> None:
        with pytest.raises(PathTraversalError, match="path traversal pattern"):
            validate_base_workspace("/tmp/../etc", must_exist=False)

    def test_dangerous_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="dangerous characters"):
            validate_base_workspace("/tmp/foo$bar", must_exist=False)

    def test_nonexistent_path(self) -> None:
        with pytest.raises(PathTraversalError, match="does not exist"):
            validate_base_workspace("/nonexistent/path/xyz")

    def test_not_a_directory(self, tmp_path) -> None:
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello")
        with pytest.raises(PathTraversalError, match="is not a directory"):
            validate_base_workspace(str(file_path))

    def test_must_exist_false(self) -> None:
        result = validate_base_workspace("/nonexistent/path/xyz", must_exist=False)
        assert isinstance(result, type(result))

    def test_must_be_dir_false(self, tmp_path) -> None:
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello")
        result = validate_base_workspace(str(file_path), must_be_dir=False)
        assert result == file_path.resolve()


class TestValidateCaseId:
    """Tests for validate_case_id."""

    def test_valid_case_id(self) -> None:
        assert validate_case_id("case_1") == "case_1"
        assert validate_case_id("test-case-123") == "test-case-123"

    def test_empty_case_id(self) -> None:
        with pytest.raises(PathTraversalError, match="cannot be empty"):
            validate_case_id("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(PathTraversalError, match="cannot be empty after normalization"):
            validate_case_id("   ")

    def test_too_long(self) -> None:
        with pytest.raises(PathTraversalError, match="exceeds maximum length"):
            validate_case_id("a" * (MAX_IDENTIFIER_LENGTH + 1))

    def test_dangerous_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="dangerous characters"):
            validate_case_id("case$id")

    def test_traversal_pattern(self) -> None:
        with pytest.raises(PathTraversalError, match="path traversal pattern"):
            validate_case_id("case../id")

    def test_invalid_chars(self) -> None:
        with pytest.raises(PathTraversalError, match="invalid characters"):
            validate_case_id("case id")


class TestConstants:
    """Tests for module-level constants."""

    def test_safe_identifier_pattern(self) -> None:
        assert SAFE_IDENTIFIER_PATTERN.match("abc123")
        assert SAFE_IDENTIFIER_PATTERN.match("test-case_123")
        assert not SAFE_IDENTIFIER_PATTERN.match("test case")
        assert not SAFE_IDENTIFIER_PATTERN.match("test/case")

    def test_dangerous_path_chars(self) -> None:
        assert DANGEROUS_PATH_CHARS.search("foo$bar")
        assert DANGEROUS_PATH_CHARS.search("foo`bar")
        assert not DANGEROUS_PATH_CHARS.search("foobar")

    def test_max_identifier_length(self) -> None:
        assert MAX_IDENTIFIER_LENGTH == 128
