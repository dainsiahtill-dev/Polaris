"""Tests for polaris.infrastructure.accel.utils module."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from polaris.infrastructure.accel.utils import (
    get_logger,
    normalize_path_abs,
    normalize_path_str,
    utc_now,
    utc_now_iso,
)


class TestUtcNow:
    """Tests for utc_now and utc_now_iso functions."""

    def test_utc_now_returns_datetime(self) -> None:
        """utc_now should return a datetime object."""
        result = utc_now()
        assert isinstance(result, datetime)

    def test_utc_now_returns_utc_timezone(self) -> None:
        """utc_now should return datetime with UTC timezone."""
        result = utc_now()
        assert result.tzinfo == timezone.utc

    def test_utc_now_iso_returns_string(self) -> None:
        """utc_now_iso should return an ISO-8601 string."""
        result = utc_now_iso()
        assert isinstance(result, str)
        assert "T" in result  # ISO format contains T separator

    def test_utc_now_iso_contains_z_or_offset(self) -> None:
        """utc_now_iso should contain timezone info."""
        result = utc_now_iso()
        # Should end with +00:00 or Z
        assert result.endswith("+00:00") or result.endswith("Z+00:00")

    def test_utc_now_iso_parses_back(self) -> None:
        """utc_now_iso string should be parseable by datetime.fromisoformat."""
        result = utc_now_iso()
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


class TestNormalizePathStr:
    """Tests for normalize_path_str function."""

    def test_backslash_to_forward_slash(self) -> None:
        """Should convert backslashes to forward slashes."""
        result = normalize_path_str(r"foo\bar\file.txt")
        assert result == "foo/bar/file.txt"

    def test_strip_whitespace(self) -> None:
        """Should strip leading/trailing whitespace."""
        result = normalize_path_str("  path/to/file.py  ")
        assert result == "path/to/file.py"

    def test_remove_dot_slash_prefix(self) -> None:
        """Should remove ./ prefix."""
        result = normalize_path_str("./path/to/file.py")
        assert result == "path/to/file.py"

    def test_empty_string(self) -> None:
        """Should handle empty string."""
        result = normalize_path_str("")
        assert result == ""

    def test_none_input(self) -> None:
        """Should handle None input gracefully."""
        result = normalize_path_str(None)  # type: ignore
        assert result == ""

    def test_mixed_separators(self) -> None:
        """Should handle mixed separators."""
        result = normalize_path_str(r".\foo\bar/../baz")
        assert "\\" not in result


class TestNormalizePathAbs:
    """Tests for normalize_path_abs function."""

    def test_returns_absolute_path(self) -> None:
        """Should return an absolute path."""
        result = normalize_path_abs(Path("relative/path"))
        assert result.is_absolute()

    def test_resolves_relative_path(self) -> None:
        """Should resolve relative path to absolute."""
        result = normalize_path_abs(Path("."))
        assert result.is_absolute()

    def test_handles_string_input(self) -> None:
        """Should handle string input."""
        result = normalize_path_abs("relative/path")
        assert isinstance(result, Path)
        assert result.is_absolute()


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_logger_instance(self) -> None:
        """Should return a logger instance."""
        result = get_logger("test_module")
        assert isinstance(result, logging.Logger)

    def test_logger_name_contains_accel(self) -> None:
        """Logger name should contain 'accel'."""
        result = get_logger("test_module")
        assert "accel" in result.name

    def test_same_logger_for_same_name(self) -> None:
        """Same logger name should return same logger."""
        logger1 = get_logger("test_module")
        logger2 = get_logger("test_module")
        assert logger1 is logger2

    def test_different_loggers_for_different_names(self) -> None:
        """Different names should return different loggers."""
        logger1 = get_logger("module_a")
        logger2 = get_logger("module_b")
        assert logger1 is not logger2

    def test_logger_with_explicit_accel_prefix(self) -> None:
        """Logger with explicit accel prefix should not double it."""
        result = get_logger("accel.submodule")
        assert result.name == "accel.submodule"
