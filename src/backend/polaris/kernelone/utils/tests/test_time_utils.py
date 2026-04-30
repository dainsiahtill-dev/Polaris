"""Tests for time_utils module."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from polaris.kernelone.utils.time_utils import (
    ISO_FORMAT_SUFFIX_Z,
    PROCESS_COMMAND_TIMEOUT_SECONDS,
    UTC_TZ_SUFFIX,
    _now,
    _utc_now,
    _utc_now_iso,
    _utc_now_str,
    utc_now,
    utc_now_iso,
    utc_now_iso_compact,
    utc_now_str,
)


class TestUtcNow:
    """Tests for utc_now function."""

    def test_returns_datetime(self) -> None:
        """Returns a datetime object."""
        result = utc_now()
        assert isinstance(result, datetime)

    def test_has_timezone(self) -> None:
        """Returned datetime has timezone info."""
        result = utc_now()
        assert result.tzinfo is not None

    def test_is_utc(self) -> None:
        """Returned datetime is in UTC."""
        result = utc_now()
        assert result.tzinfo == timezone.utc

    def test_is_recent(self) -> None:
        """Returned time is close to current time."""
        before = datetime.now(timezone.utc)
        result = utc_now()
        after = datetime.now(timezone.utc)
        assert before <= result <= after


class TestUtcNowIso:
    """Tests for utc_now_iso function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = utc_now_iso()
        assert isinstance(result, str)

    def test_contains_timezone(self) -> None:
        """Contains timezone offset."""
        result = utc_now_iso()
        assert UTC_TZ_SUFFIX in result or ISO_FORMAT_SUFFIX_Z in result

    def test_is_valid_iso_format(self) -> None:
        """Can be parsed back to datetime."""
        result = utc_now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None


class TestUtcNowStr:
    """Tests for utc_now_str function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = utc_now_str()
        assert isinstance(result, str)

    def test_ends_with_z(self) -> None:
        """Ends with Z suffix."""
        result = utc_now_str()
        assert result.endswith("Z")

    def test_no_plus_offset(self) -> None:
        """Does not contain +00:00."""
        result = utc_now_str()
        assert UTC_TZ_SUFFIX not in result

    def test_is_valid_iso_format(self) -> None:
        """Can be parsed back to datetime."""
        result = utc_now_str()
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert dt.tzinfo is not None


class TestUtcNowIsoCompact:
    """Tests for utc_now_iso_compact function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = utc_now_iso_compact()
        assert isinstance(result, str)

    def test_no_microseconds(self) -> None:
        """Does not contain microseconds."""
        result = utc_now_iso_compact()
        assert "." not in result

    def test_has_seconds_precision(self) -> None:
        """Has seconds precision."""
        result = utc_now_iso_compact()
        time_part = result.split("T")[1]
        # Remove timezone suffix if present
        time_part = time_part.replace("Z", "").replace("+00:00", "")
        parts = time_part.split(":")
        assert len(parts) == 3


class TestNow:
    """Tests for _now function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = _now()
        assert isinstance(result, str)

    def test_no_microseconds(self) -> None:
        """Does not contain microseconds."""
        result = _now()
        assert "." not in result

    def test_is_valid_iso_format(self) -> None:
        """Can be parsed back to datetime."""
        result = _now()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None
        assert dt.microsecond == 0


class TestConstants:
    """Tests for module constants."""

    def test_iso_format_suffix(self) -> None:
        """ISO_FORMAT_SUFFIX_Z is 'Z'."""
        assert ISO_FORMAT_SUFFIX_Z == "Z"

    def test_utc_tz_suffix(self) -> None:
        """UTC_TZ_SUFFIX is '+00:00'."""
        assert UTC_TZ_SUFFIX == "+00:00"

    def test_process_timeout(self) -> None:
        """PROCESS_COMMAND_TIMEOUT_SECONDS is 30."""
        assert PROCESS_COMMAND_TIMEOUT_SECONDS == 30


class TestBackwardCompatibility:
    """Tests for backward compatibility aliases."""

    def test_utc_now_alias(self) -> None:
        """_utc_now is alias for utc_now."""
        assert _utc_now is utc_now

    def test_utc_now_iso_alias(self) -> None:
        """_utc_now_iso is alias for utc_now_iso."""
        assert _utc_now_iso is utc_now_iso

    def test_utc_now_str_alias(self) -> None:
        """_utc_now_str is alias for utc_now_str."""
        assert _utc_now_str is utc_now_str


class TestEdgeCases:
    """Edge case tests."""

    @patch("polaris.kernelone.utils.time_utils.datetime")
    def test_mocked_time(self, mock_datetime: Any) -> None:
        """Functions work with mocked datetime."""
        mock_now = datetime(2026, 1, 15, 12, 30, 45, 123456, tzinfo=timezone.utc)
        mock_datetime.now.return_value = mock_now
        mock_datetime.now.side_effect = None

        result = utc_now_str()
        assert "2026-01-15T12:30:45" in result

    def test_all_functions_are_callable(self) -> None:
        """All public functions are callable without errors."""
        functions = [
            utc_now,
            utc_now_iso,
            utc_now_str,
            utc_now_iso_compact,
            _now,
        ]
        for func in functions:
            result = func()
            assert isinstance(result, (str, datetime))

    def test_monotonic_increasing(self) -> None:
        """Subsequent calls return non-decreasing times."""
        t1 = utc_now()
        t2 = utc_now()
        assert t2 >= t1
