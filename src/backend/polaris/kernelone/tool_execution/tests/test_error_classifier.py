"""Tests for ToolErrorClassifier."""

from __future__ import annotations

from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier


class TestToolErrorClassifier:
    """Tests for ToolErrorClassifier."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.classifier = ToolErrorClassifier()

    def test_classify_no_match_error(self) -> None:
        """Test classification of 'no matches found' error."""
        pattern = self.classifier.classify("precision_edit", "no matches found")

        assert pattern.tool_name == "precision_edit"
        assert pattern.error_type == "no_match"
        assert "no matches" in pattern.error_signature.lower()

    def test_classify_not_found_error(self) -> None:
        """Test classification of 'not found' error."""
        pattern = self.classifier.classify("read_file", "file not found at path /foo/bar.py")

        assert pattern.tool_name == "read_file"
        assert pattern.error_type == "not_found"

    def test_classify_permission_error(self) -> None:
        """Test classification of permission error."""
        pattern = self.classifier.classify("write_file", "permission denied")

        assert pattern.tool_name == "write_file"
        assert pattern.error_type == "permission"

    def test_classify_timeout_error(self) -> None:
        """Test classification of timeout error."""
        pattern = self.classifier.classify("execute_command", "operation timed out")

        assert pattern.tool_name == "execute_command"
        assert pattern.error_type == "timeout"

    def test_classify_exception(self) -> None:
        """Test classification of exception object."""
        try:
            raise ValueError("invalid literal for int()")
        except ValueError as e:
            pattern = self.classifier.classify("execute_command", e)

        assert pattern.tool_name == "execute_command"
        assert pattern.error_type == "invalid_arg"

    def test_similar_errors_same_type(self) -> None:
        """Test that similar errors get same error_type."""
        p1 = self.classifier.classify("edit", "no matches found at line 42")
        p2 = self.classifier.classify("edit", "no matches found at line 100")

        assert p1.error_type == p2.error_type == "no_match"

    def test_generalized_signature_removes_line_numbers(self) -> None:
        """Test that line numbers are removed from signature."""
        p1 = self.classifier.classify("edit", "error at line 42")
        p2 = self.classifier.classify("edit", "error at line 100")

        # error_signature should be similar but not identical due to different content
        assert p1.error_type == p2.error_type
        assert "line N" in p1.error_signature
        assert "line N" in p2.error_signature

    def test_cache_works(self) -> None:
        """Test that caching returns same object."""
        p1 = self.classifier.classify("edit", "test error message")
        p2 = self.classifier.classify("edit", "test error message")

        assert p1 is p2  # Same object due to caching

    def test_clear_cache(self) -> None:
        """Test cache clearing."""
        p1 = self.classifier.classify("edit", "test error message")
        self.classifier.clear_cache()
        p2 = self.classifier.classify("edit", "test error message")

        assert p1 is not p2  # Different object after cache clear

    def test_get_error_type_display_name(self) -> None:
        """Test display name generation."""
        assert self.classifier.get_error_type_display_name("no_match") == "搜索未命中"
        assert self.classifier.get_error_type_display_name("not_found") == "文件/资源未找到"
        assert self.classifier.get_error_type_display_name("unknown") == "未知错误"

    def test_unknown_error_type(self) -> None:
        """Test unknown error type classification."""
        pattern = self.classifier.classify("unknown_tool", "some random error")

        assert pattern.error_type == "unknown"
