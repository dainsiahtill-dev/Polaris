"""Tests for ExplorationBuilder and FuzzyMatchBuilder."""

from __future__ import annotations

from polaris.kernelone.tool_execution.suggestions.exploration import ExplorationBuilder
from polaris.kernelone.tool_execution.suggestions.fuzzy import FuzzyMatchBuilder, _check_python_syntax_errors


class TestExplorationBuilder:
    """Tests for ExplorationBuilder."""

    def test_should_apply_not_found_error(self) -> None:
        """Test should_apply returns True for 'not found' errors."""
        builder = ExplorationBuilder()
        assert builder.should_apply({"error": "File not found"}) is True
        assert builder.should_apply({"error": "does not exist"}) is True
        assert builder.should_apply({"error": "No such file"}) is True

    def test_should_apply_false_for_other_errors(self) -> None:
        """Test should_apply returns False for other errors."""
        builder = ExplorationBuilder()
        assert builder.should_apply({"error": "Permission denied"}) is False
        assert builder.should_apply({"error": "Timeout"}) is False

    def test_build_with_similar_file_suggestion(self) -> None:
        """Test build suggests similar filename when available."""
        builder = ExplorationBuilder()
        workspace_files = ["main.py", "test.py", "app.py"]
        error_result = {"error": "File not found: mai.py", "file": "mai.py"}

        result = builder.build(error_result, workspace_files=workspace_files)

        assert result is not None
        assert "Did you mean:" in result
        assert "main.py" in result

    def test_build_with_small_workspace_file_list(self) -> None:
        """Test build includes available files when workspace is small."""
        builder = ExplorationBuilder()
        workspace_files = ["a.py", "b.py", "c.py"]
        error_result = {"error": "File not found", "file": "missing.py"}

        result = builder.build(error_result, workspace_files=workspace_files)

        assert result is not None
        assert "Available files:" in result

    def test_build_with_workspace_dirs(self) -> None:
        """Test build includes directory suggestions."""
        builder = ExplorationBuilder()
        error_result = {"error": "File not found", "file": "missing.py"}
        workspace_dirs = ["src", "tests", "docs"]

        result = builder.build(error_result, workspace_dirs=workspace_dirs)

        assert result is not None
        assert "repo_tree()" in result or "glob" in result

    def test_build_without_workspace_context(self) -> None:
        """Test build handles missing workspace context gracefully."""
        builder = ExplorationBuilder()
        error_result = {"error": "File not found", "file": "missing.py"}

        result = builder.build(error_result)

        assert result is not None
        assert "not found" in result.lower()

    def test_find_similar_name_exact_match(self) -> None:
        """Test _find_similar_name returns match for exact match (ratio >= threshold)."""
        result = ExplorationBuilder._find_similar_name("test.py", ["test.py"])
        assert result == "test.py"

    def test_find_similar_name_no_match(self) -> None:
        """Test _find_similar_name returns None when similarity is low."""
        result = ExplorationBuilder._find_similar_name("xyz", ["abc", "def", "ghi"])
        assert result is None


class TestFuzzyMatchBuilder:
    """Tests for FuzzyMatchBuilder."""

    def test_should_apply_exact_no_matches(self) -> None:
        """Test should_apply returns True for 'no matches found'."""
        builder = FuzzyMatchBuilder()
        assert builder.should_apply({"error": "no matches found"}) is True
        assert builder.should_apply({"error": "no matches found."}) is True

    def test_should_apply_false_for_other_errors(self) -> None:
        """Test should_apply returns False for other errors."""
        builder = FuzzyMatchBuilder()
        assert builder.should_apply({"error": "permission denied"}) is False
        assert builder.should_apply({"error": "file not found"}) is False

    def test_build_with_missing_search_string(self) -> None:
        """Test build returns None when search string is missing."""
        builder = FuzzyMatchBuilder()
        result = builder.build({"error": "no matches found"})
        assert result is None

    def test_build_with_empty_content(self) -> None:
        """Test build handles empty file content."""
        builder = FuzzyMatchBuilder()
        error_result = {"error": "no matches found", "search": "some text", "content": ""}
        result = builder.build(error_result)
        assert result is not None
        assert "not found" in result.lower()

    def test_build_with_python_syntax_error(self) -> None:
        """Test build detects Python syntax errors like return0."""
        builder = FuzzyMatchBuilder()
        error_result = {"error": "no matches found", "search": "return0", "content": "def foo():\n    return None"}
        result = builder.build(error_result)
        assert result is not None
        assert "SYNTAX ERROR" in result or "return0" in result

    def test_build_finds_similar_line(self) -> None:
        """Test build finds similar line in file content."""
        builder = FuzzyMatchBuilder()
        error_result = {"error": "no matches found", "search": "def fooo():", "content": "def foo():\n    pass"}
        result = builder.build(error_result)
        assert result is not None
        assert "foo" in result.lower()


class TestCheckPythonSyntaxErrors:
    """Tests for _check_python_syntax_errors helper."""

    def test_detects_return0(self) -> None:
        """Test detection of return0 syntax error."""
        result = _check_python_syntax_errors("return0")
        assert result is not None
        assert "return0" in result.lower()

    def test_detects_return_none(self) -> None:
        """Test detection of returnNone syntax error."""
        result = _check_python_syntax_errors("returnNone")
        assert result is not None

    def test_detects_return_true(self) -> None:
        """Test detection of returnTrue syntax error."""
        result = _check_python_syntax_errors("returnTrue")
        assert result is not None

    def test_detects_return_false(self) -> None:
        """Test detection of returnFalse syntax error."""
        result = _check_python_syntax_errors("returnFalse")
        assert result is not None

    def test_returns_none_for_valid_code(self) -> None:
        """Test returns None for valid Python code."""
        result = _check_python_syntax_errors("return 0")
        assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        """Test returns None for empty string."""
        result = _check_python_syntax_errors("")
        assert result is None
