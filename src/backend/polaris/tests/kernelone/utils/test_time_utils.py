"""Tests for polaris.kernelone.utils.time_utils - edge cases and integration."""

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


class TestUtcNowBehavior:
    def test_returns_recent_utc_datetime(self) -> None:
        before = datetime.now(timezone.utc)
        result = utc_now()
        after = datetime.now(timezone.utc)
        assert result.tzinfo == timezone.utc
        assert before <= result <= after

    def test_not_naive(self) -> None:
        result = utc_now()
        assert result.tzinfo is not None


class TestUtcNowIsoBehavior:
    def test_contains_timezone_offset(self) -> None:
        result = utc_now_iso()
        assert UTC_TZ_SUFFIX in result or ISO_FORMAT_SUFFIX_Z in result

    def test_is_parseable(self) -> None:
        result = utc_now_iso()
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo == timezone.utc


class TestUtcNowStrBehavior:
    def test_ends_with_z(self) -> None:
        result = utc_now_str()
        assert result.endswith(ISO_FORMAT_SUFFIX_Z)

    def test_no_plus_offset(self) -> None:
        result = utc_now_str()
        assert UTC_TZ_SUFFIX not in result

    def test_is_parseable_after_replace(self) -> None:
        result = utc_now_str()
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo == timezone.utc


class TestUtcNowIsoCompactBehavior:
    def test_no_microseconds(self) -> None:
        result = utc_now_iso_compact()
        assert "." not in result

    def test_no_z_suffix(self) -> None:
        result = utc_now_iso_compact()
        assert not result.endswith("Z")

    def test_has_timezone_offset(self) -> None:
        result = utc_now_iso_compact()
        assert "+" in result or result.endswith("Z")


class TestNowBehavior:
    def test_returns_string(self) -> None:
        result = _now()
        assert isinstance(result, str)

    def test_no_microseconds(self) -> None:
        result = _now()
        assert "." not in result

    def test_timezone_present(self) -> None:
        result = _now()
        assert "+" in result or result.endswith("Z")


class TestConstantsValues:
    def test_iso_format_suffix_z(self) -> None:
        assert ISO_FORMAT_SUFFIX_Z == "Z"

    def test_utc_tz_suffix(self) -> None:
        assert UTC_TZ_SUFFIX == "+00:00"

    def test_process_command_timeout_seconds(self) -> None:
        assert isinstance(PROCESS_COMMAND_TIMEOUT_SECONDS, int)
        assert PROCESS_COMMAND_TIMEOUT_SECONDS > 0


class TestTimeOrdering:
    def test_all_timestamps_are_close(self) -> None:
        t1 = utc_now_iso()
        t2 = utc_now_str()
        t3 = utc_now_iso_compact()
        t4 = _now()
        # All should be strings
        assert all(isinstance(t, str) for t in (t1, t2, t3, t4))
        # All should contain the current year
        year = str(datetime.now(timezone.utc).year)
        assert all(year in t for t in (t1, t2, t3, t4))
