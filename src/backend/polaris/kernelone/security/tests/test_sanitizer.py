from __future__ import annotations

import pytest
from polaris.kernelone.security.sanitizer import InputSanitizer


@pytest.fixture
def sanitizer() -> InputSanitizer:
    return InputSanitizer()


class TestInputSanitizer:
    """Tests for InputSanitizer."""

    def test_sanitize_command_removes_shell_chars(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization removes dangerous characters."""
        result = sanitizer.sanitize_command("ls; rm -rf /")
        assert ";" not in result
        assert "|" not in result
        assert "$" not in result

    def test_sanitize_command_removes_pipes(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization removes pipes."""
        result = sanitizer.sanitize_command("cat /etc/passwd | grep root")
        assert "|" not in result

    def test_sanitize_command_removes_substitution(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization removes command substitution."""
        result = sanitizer.sanitize_command("$(whoami)")
        assert "$(" not in result

    def test_sanitize_command_removes_backticks(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization removes backticks."""
        result = sanitizer.sanitize_command("`id`")
        assert "`" not in result

    def test_sanitize_command_removes_path_traversal(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization removes path traversal."""
        result = sanitizer.sanitize_command("../../../etc/passwd")
        assert ".." not in result

    def test_sanitize_filename_removes_dangerous_chars(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization removes dangerous characters."""
        result = sanitizer.sanitize_filename('file<>:"/\\|?*.txt')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_sanitize_filename_blocks_reserved_names(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization blocks Windows reserved names."""
        result = sanitizer.sanitize_filename("CON")
        assert result.startswith("_")

    def test_sanitize_filename_prevents_absolute_paths(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization prevents absolute paths."""
        result = sanitizer.sanitize_filename("/etc/passwd")
        assert result.startswith("_")

    def test_sanitize_filename_truncates_long_names(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization truncates very long names."""
        long_name = "a" * 300 + ".txt"
        result = sanitizer.sanitize_filename(long_name)
        assert len(result) <= 255

    def test_validate_json_valid(self, sanitizer: InputSanitizer) -> None:
        """Test JSON validation accepts valid JSON."""
        assert sanitizer.validate_json('{"key": "value"}') is True

    def test_validate_json_invalid(self, sanitizer: InputSanitizer) -> None:
        """Test JSON validation rejects invalid JSON."""
        assert sanitizer.validate_json("{key: value}") is False

    def test_validate_json_empty(self, sanitizer: InputSanitizer) -> None:
        """Test JSON validation handles empty string."""
        assert sanitizer.validate_json("") is False

    def test_sanitize_command_non_string(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization handles non-string input."""
        result = sanitizer.sanitize_command(None)
        assert result == "None"

    def test_sanitize_command_non_ascii(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization handles non-ASCII characters."""
        result = sanitizer.sanitize_command("echo 日本語")
        assert "日本語" in result
        assert ";" not in result

    def test_sanitize_command_ultra_long_input(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization handles ultra-long input."""
        long_input = "ls " + "a" * 100000
        result = sanitizer.sanitize_command(long_input)
        assert len(result) == len(long_input)
        assert ";" not in result

    def test_sanitize_filename_non_ascii(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization handles non-ASCII characters."""
        result = sanitizer.sanitize_filename("文档.txt")
        assert "文档.txt" in result or "_" in result

    def test_sanitize_filename_emoji(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization handles emoji."""
        result = sanitizer.sanitize_filename("test😀file.txt")
        assert "test" in result

    def test_sanitize_filename_ultra_long_input(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization handles ultra-long input."""
        long_input = "a" * 300 + ".txt"
        result = sanitizer.sanitize_filename(long_input)
        assert len(result) <= 255

    def test_sanitize_command_empty_string(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization handles empty string."""
        result = sanitizer.sanitize_command("")
        assert result == ""

    def test_sanitize_filename_empty_string(self, sanitizer: InputSanitizer) -> None:
        """Test filename sanitization handles empty string."""
        result = sanitizer.sanitize_filename("")
        assert result == "_"

    def test_sanitize_command_control_characters(self, sanitizer: InputSanitizer) -> None:
        """Test command sanitization handles control characters."""
        result = sanitizer.sanitize_command("test\x00value")
        assert "test" in result
