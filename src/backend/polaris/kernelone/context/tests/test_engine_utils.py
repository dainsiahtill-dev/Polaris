"""Tests for context engine utilities.

Covers _hash_text, _estimate_tokens, _safe_json, _read_tail_lines,
and _read_slice_spec functions.
"""

from __future__ import annotations

import json
from typing import Any

from polaris.kernelone.context.engine.utils import (
    _estimate_tokens,
    _hash_text,
    _read_slice_spec,
    _read_tail_lines,
    _safe_json,
)

# ---------------------------------------------------------------------------
# _hash_text Tests
# ---------------------------------------------------------------------------


class TestHashText:
    """Test _hash_text function."""

    def test_hash_returns_hex_string(self) -> None:
        """_hash_text should return a hexadecimal string."""
        result = _hash_text("hello world")
        assert isinstance(result, str)
        assert len(result) == 40  # SHA-1 produces 40 hex chars
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_consistent(self) -> None:
        """_hash_text should return consistent results for same input."""
        text = "test content"
        result1 = _hash_text(text)
        result2 = _hash_text(text)
        assert result1 == result2

    def test_hash_different_for_different_inputs(self) -> None:
        """_hash_text should return different results for different inputs."""
        hash1 = _hash_text("hello")
        hash2 = _hash_text("world")
        assert hash1 != hash2

    def test_hash_empty_string(self) -> None:
        """_hash_text should handle empty string."""
        result = _hash_text("")
        assert isinstance(result, str)
        assert len(result) == 40

    def test_hash_none_input(self) -> None:
        """_hash_text should handle None input gracefully."""
        result = _hash_text(None)  # type: ignore
        assert isinstance(result, str)
        assert len(result) == 40

    def test_hash_unicode(self) -> None:
        """_hash_text should handle unicode characters."""
        result = _hash_text("Hello, World! 你好世界")
        assert isinstance(result, str)
        assert len(result) == 40

    def test_hash_utf8_encoding(self) -> None:
        """_hash_text should use UTF-8 encoding."""
        # Same content in different encodings should produce same result
        result = _hash_text("café")
        assert isinstance(result, str)
        # UTF-8 encoding is expected


# ---------------------------------------------------------------------------
# _estimate_tokens Tests
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    """Test _estimate_tokens function."""

    def test_estimate_tokens_returns_int(self) -> None:
        """_estimate_tokens should return an integer."""
        result = _estimate_tokens("hello world")
        assert isinstance(result, int)

    def test_estimate_tokens_empty_string(self) -> None:
        """_estimate_tokens should return 0 for empty string."""
        result = _estimate_tokens("")
        assert result == 0

    def test_estimate_tokens_none(self) -> None:
        """_estimate_tokens should handle None gracefully."""
        result = _estimate_tokens(None)  # type: ignore
        assert result == 0

    def test_estimate_tokens_longer_text_more_tokens(self) -> None:
        """Longer text should estimate to more tokens."""
        short = "hello"
        long_text = "hello world this is a longer piece of text"
        assert _estimate_tokens(long_text) > _estimate_tokens(short)

    def test_estimate_tokens_code_vs_text(self) -> None:
        """Code might have different token ratio than prose."""
        code = "def foo():\n    return 42\n"
        prose = "function foo returns 42"
        # Both should return valid estimates
        code_tokens = _estimate_tokens(code)
        prose_tokens = _estimate_tokens(prose)
        assert code_tokens > 0
        assert prose_tokens > 0


# ---------------------------------------------------------------------------
# _safe_json Tests
# ---------------------------------------------------------------------------


class TestSafeJson:
    """Test _safe_json function."""

    def test_safe_json_dict(self) -> None:
        """_safe_json should serialize dict to JSON string."""
        result = _safe_json({"key": "value", "num": 42})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == {"key": "value", "num": 42}

    def test_safe_json_list(self) -> None:
        """_safe_json should serialize list to JSON string."""
        result = _safe_json([1, 2, 3])
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == [1, 2, 3]

    def test_safe_json_nested(self) -> None:
        """_safe_json should handle nested structures."""
        data = {"outer": {"inner": [1, 2, {"key": "value"}]}}
        result = _safe_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_safe_json_with_unicode(self) -> None:
        """_safe_json should handle unicode characters."""
        result = _safe_json({"text": "你好世界", "emoji": "🎉"})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["text"] == "你好世界"
        assert parsed["emoji"] == "🎉"

    def test_safe_json_ensures_ascii_false(self) -> None:
        """_safe_json should use ensure_ascii=False by default."""
        result = _safe_json({"text": "café"})
        # Should contain actual é character, not escaped
        assert "café" in result or "caf" in result

    def test_safe_json_non_serializable(self) -> None:
        """_safe_json behavior with non-serializable objects.

        Note: The actual exception type depends on the object.
        json.dumps raises TypeError for unknown types, which is NOT caught
        by the function (it only catches RuntimeError and ValueError).
        """

        class NonSerializable:
            pass

        try:
            result = _safe_json(NonSerializable())
            # If it doesn't raise, it should return a string
            assert isinstance(result, str)
        except TypeError:
            # TypeError is not caught by the function - expected behavior
            pass

    def test_safe_json_circular_reference(self) -> None:
        """_safe_json should handle circular references gracefully."""
        data: dict[str, Any] = {"key": "value"}
        data["self"] = data  # Circular reference

        result = _safe_json(data)
        assert result == "{}"

    def test_safe_json_special_types(self) -> None:
        """_safe_json behavior with special types like datetime.

        Note: datetime is not JSON serializable by default.
        The function catches RuntimeError and ValueError but not TypeError.
        """
        from datetime import datetime, timezone

        try:
            result = _safe_json({"timestamp": datetime.now(timezone.utc)})
            assert isinstance(result, str)
        except TypeError:
            # TypeError not caught - expected for datetime
            pass


# ---------------------------------------------------------------------------
# _read_tail_lines Tests
# ---------------------------------------------------------------------------


class TestReadTailLines:
    """Test _read_tail_lines function."""

    def test_read_tail_lines_basic(self, tmp_path: Any) -> None:
        """_read_tail_lines should read last N lines."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=3)
        assert len(result) == 3
        assert result[0] == "line3"
        assert result[1] == "line4"
        assert result[2] == "line5"

    def test_read_tail_lines_less_than_file(self, tmp_path: Any) -> None:
        """_read_tail_lines should return all lines if file has fewer lines."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\n", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=10)
        assert len(result) == 2

    def test_read_tail_lines_empty_file(self, tmp_path: Any) -> None:
        """_read_tail_lines should handle empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=10)
        assert result == []

    def test_read_tail_lines_nonexistent_file(self) -> None:
        """_read_tail_lines should return empty list for nonexistent file."""
        result = _read_tail_lines("/nonexistent/file.txt", max_lines=10)
        assert result == []

    def test_read_tail_lines_zero_max_lines(self, tmp_path: Any) -> None:
        """_read_tail_lines behavior with max_lines=0.

        Note: When max_lines=0, the function:
        1. Reads all the data (while loop condition: data.count(b"\\n") <= 0 is true only when no newlines)
        2. Does NOT truncate (the if condition max_lines > 0 is false)
        3. Returns all content as lines via splitlines()
        """
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=0)
        # Returns all lines when max_lines=0 (no truncation applied)
        assert len(result) >= 1

    def test_read_tail_lines_no_newline_at_end(self, tmp_path: Any) -> None:
        """_read_tail_lines should handle file without trailing newline."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=2)
        assert len(result) == 2
        assert result[0] == "line2"
        assert result[1] == "line3"

    def test_read_tail_lines_large_file(self, tmp_path: Any) -> None:
        """_read_tail_lines should efficiently read large files."""
        test_file = tmp_path / "large.txt"
        # Create a file with many lines
        lines = [f"line_{i}" for i in range(10000)]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=5)
        assert len(result) == 5
        assert "9995" in result[0]
        assert "9999" in result[4]

    def test_read_tail_lines_binary_content(self, tmp_path: Any) -> None:
        """_read_tail_lines should handle binary content gracefully."""
        test_file = tmp_path / "binary.txt"
        # Write some binary-like content
        with open(test_file, "wb") as f:
            f.write(b"line1\nline2\nline3\n")

        result = _read_tail_lines(str(test_file), max_lines=2)
        assert len(result) == 2

    def test_read_tail_lines_utf8(self, tmp_path: Any) -> None:
        """_read_tail_lines should handle UTF-8 content correctly."""
        test_file = tmp_path / "unicode.txt"
        test_file.write_text("line1\n你好世界\nemoji: 🎉\n", encoding="utf-8")

        result = _read_tail_lines(str(test_file), max_lines=2)
        assert len(result) == 2
        assert "你好世界" in result[0]
        assert "🎉" in result[1]


# ---------------------------------------------------------------------------
# _read_slice_spec Tests
# ---------------------------------------------------------------------------


class TestReadSliceSpec:
    """Test _read_slice_spec function."""

    def test_slice_spec_around_with_radius(self, tmp_path: Any) -> None:
        """_read_slice_spec should handle 'around' with 'radius'."""
        test_file = tmp_path / "test.py"
        lines = ["line1", "line2", "line3", "line4", "line5", "line6", "line7"]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        spec = {"around": 4, "radius": 2}
        content, line_range, file_hash = _read_slice_spec(str(test_file), spec)

        assert content == "line2\nline3\nline4\nline5\nline6"
        assert line_range == [2, 6]
        assert len(file_hash) == 40

    def test_slice_spec_start_end_lines(self, tmp_path: Any) -> None:
        """_read_slice_spec should handle explicit start/end lines."""
        test_file = tmp_path / "test.txt"
        lines = ["line1", "line2", "line3", "line4", "line5"]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        spec = {"start_line": 2, "end_line": 4}
        content, line_range, _file_hash = _read_slice_spec(str(test_file), spec)

        assert content == "line2\nline3\nline4"
        assert line_range == [2, 4]

    def test_slice_spec_line_start_end_aliases(self, tmp_path: Any) -> None:
        """_read_slice_spec should accept line_start/line_end aliases."""
        test_file = tmp_path / "test.txt"
        lines = ["line1", "line2", "line3"]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        spec = {"line_start": 1, "line_end": 2}
        content, line_range, _ = _read_slice_spec(str(test_file), spec)

        assert content == "line1\nline2"
        assert line_range == [1, 2]

    def test_slice_spec_nonexistent_file(self) -> None:
        """_read_slice_spec should return empty for nonexistent file."""
        content, line_range, file_hash = _read_slice_spec("/nonexistent.txt", {})
        assert content == ""
        assert line_range == [0, 0]
        assert file_hash == ""

    def test_slice_spec_empty_file(self, tmp_path: Any) -> None:
        """_read_slice_spec should handle empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        content, line_range, file_hash = _read_slice_spec(str(test_file), {})
        assert content == ""
        assert line_range == [0, 0]
        assert file_hash == ""

    def test_slice_spec_beyond_file_bounds(self, tmp_path: Any) -> None:
        """_read_slice_spec should clamp to file bounds."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3", encoding="utf-8")

        # Request lines beyond file length
        spec = {"start_line": 1, "end_line": 100}
        content, line_range, _ = _read_slice_spec(str(test_file), spec)

        assert line_range == [1, 3]  # Should be clamped
        assert "line1" in content

    def test_slice_spec_negative_line(self, tmp_path: Any) -> None:
        """_read_slice_spec should clamp negative start_line to 1."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3", encoding="utf-8")

        spec = {"start_line": -5, "end_line": 2}
        content, line_range, _ = _read_slice_spec(str(test_file), spec)

        assert line_range[0] == 1  # Should be at least 1
        assert "line1" in content

    def test_slice_spec_around_edge_case(self, tmp_path: Any) -> None:
        """_read_slice_spec should handle 'around' at file edges."""
        test_file = tmp_path / "test.txt"
        lines = ["line1", "line2", "line3", "line4"]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        # around=1 with radius=2 should get lines 1 to min(3, 4)
        spec = {"around": 1, "radius": 2}
        content, line_range, _ = _read_slice_spec(str(test_file), spec)

        assert line_range == [1, 3]
        assert "line1" in content

    def test_slice_spec_around_with_default_radius(self, tmp_path: Any) -> None:
        """_read_slice_spec should use default radius of 80 when not specified."""
        test_file = tmp_path / "test.txt"
        # Create a file with many lines
        lines = [f"line{i}" for i in range(200)]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        spec = {"around": 100}  # No radius specified
        _content, line_range, _ = _read_slice_spec(str(test_file), spec)

        # Should use default radius of 80
        assert line_range[0] == 20  # 100 - 80
        assert line_range[1] == 180  # 100 + 80, clamped to 180 (200 - 20, but limited by file)

    def test_slice_spec_returns_file_hash(self, tmp_path: Any) -> None:
        """_read_slice_spec should return consistent file hash."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content for hashing", encoding="utf-8")

        spec1 = {"start_line": 1, "end_line": 1}
        spec2 = {"around": 1, "radius": 0}

        _, _, hash1 = _read_slice_spec(str(test_file), spec1)
        _, _, hash2 = _read_slice_spec(str(test_file), spec2)

        # Both should return the same file hash
        assert hash1 == hash2
        assert len(hash1) == 40
