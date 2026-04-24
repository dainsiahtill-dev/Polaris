"""Tests for polaris.kernelone.utils.time_utils."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.kernelone.utils.time_utils import (
    ISO_FORMAT_SUFFIX_Z,
    PROCESS_COMMAND_TIMEOUT_SECONDS,
    UTC_TZ_SUFFIX,
    _now,
    utc_now,
    utc_now_iso,
    utc_now_iso_compact,
    utc_now_str,
)


class TestUtcNow:
    def test_returns_utc_datetime(self) -> None:
        result = utc_now()
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc


class TestUtcNowIso:
    def test_returns_iso_string(self) -> None:
        result = utc_now_iso()
        assert isinstance(result, str)
        assert UTC_TZ_SUFFIX in result


class TestUtcNowStr:
    def test_returns_string_with_z_suffix(self) -> None:
        result = utc_now_str()
        assert isinstance(result, str)
        assert ISO_FORMAT_SUFFIX_Z in result
        assert UTC_TZ_SUFFIX not in result


class TestUtcNowIsoCompact:
    def test_returns_seconds_precision(self) -> None:
        result = utc_now_iso_compact()
        assert isinstance(result, str)
        assert "." not in result


class TestNow:
    def test_returns_iso_string_no_microseconds(self) -> None:
        result = _now()
        assert isinstance(result, str)
        assert "." not in result


class TestConstants:
    def test_iso_format_suffix_z(self) -> None:
        assert ISO_FORMAT_SUFFIX_Z == "Z"

    def test_utc_tz_suffix(self) -> None:
        assert UTC_TZ_SUFFIX == "+00:00"

    def test_process_command_timeout(self) -> None:
        assert PROCESS_COMMAND_TIMEOUT_SECONDS == 30
