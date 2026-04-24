"""Tests for polaris.infrastructure.accel.utils."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from polaris.infrastructure.accel.utils import (
    get_logger,
    normalize_path_abs,
    normalize_path_str,
    utc_now,
    utc_now_iso,
)


class TestUtcNowIso:
    def test_returns_iso_string(self) -> None:
        result = utc_now_iso()
        assert isinstance(result, str)
        assert "T" in result


class TestUtcNow:
    def test_returns_utc_datetime(self) -> None:
        result = utc_now()
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc


class TestNormalizePathStr:
    def test_backslash_to_slash(self) -> None:
        assert normalize_path_str("foo\\bar") == "foo/bar"

    def test_strips_whitespace(self) -> None:
        assert normalize_path_str("  foo  ") == "foo"

    def test_drops_dot_slash(self) -> None:
        assert normalize_path_str("./foo") == "foo"

    def test_none_returns_empty(self) -> None:
        assert normalize_path_str("") == ""


class TestNormalizePathAbs:
    def test_returns_absolute(self) -> None:
        result = normalize_path_abs(Path("foo"))
        assert result.is_absolute()


class TestGetLogger:
    def test_returns_logger(self) -> None:
        logger = get_logger("test")
        assert logger.name == "accel.test"
        assert len(logger.handlers) > 0
