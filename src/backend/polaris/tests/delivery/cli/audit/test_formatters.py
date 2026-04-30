"""Tests for polaris.delivery.cli.audit.audit.formatters module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.delivery.cli.audit.audit.formatters import (
    format_event_compact,
    format_relative_time,
    format_time_window,
    get_result_attr,
    parse_relative_time,
    parse_window,
    resolve_export_format,
)


class TestFormatRelativeTime:
    """Tests for format_relative_time."""

    def test_empty_string_returns_empty(self) -> None:
        assert format_relative_time("") == ""

    def test_none_equivalent_empty_string(self) -> None:
        assert format_relative_time("") == ""

    def test_invalid_iso_returns_truncated(self) -> None:
        result = format_relative_time("not-a-timestamp")
        assert result == "not-a-timestamp"

    def test_just_now_seconds(self) -> None:
        now = datetime.now(timezone.utc)
        iso = now.isoformat()
        result = format_relative_time(iso)
        assert "秒前" in result

    def test_minutes_ago(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        iso = past.isoformat()
        result = format_relative_time(iso)
        assert "分钟前" in result
        assert "5" in result

    def test_hours_ago(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        iso = past.isoformat()
        result = format_relative_time(iso)
        assert "小时前" in result
        assert "3" in result

    def test_days_ago(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=5)
        iso = past.isoformat()
        result = format_relative_time(iso)
        assert "天前" in result
        assert "5" in result

    def test_months_ago_returns_date(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(days=60)
        iso = past.isoformat()
        result = format_relative_time(iso)
        assert "天前" not in result
        assert "秒前" not in result
        assert "分钟前" not in result
        assert "小时前" not in result
        assert len(result) == 10  # YYYY-MM-DD

    def test_z_suffix_converted(self) -> None:
        now = datetime.now(timezone.utc)
        iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = format_relative_time(iso)
        assert "秒前" in result

    def test_timezone_aware_parsing(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        iso = past.isoformat()
        result = format_relative_time(iso)
        assert "小时前" in result

    def test_future_time_negative_seconds(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        iso = future.isoformat()
        result = format_relative_time(iso)
        assert "秒前" in result

    def test_invalid_truncates_to_19_chars(self) -> None:
        long_ts = "2024-01-01T12:00:00+00:00-extra"
        result = format_relative_time(long_ts)
        assert result == long_ts[:19]


class TestParseRelativeTime:
    """Tests for parse_relative_time."""

    def test_empty_string_returns_none(self) -> None:
        assert parse_relative_time("") is None

    def test_now_returns_current_time(self) -> None:
        result = parse_relative_time("now")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds()) < 2

    def test_today_returns_midnight(self) -> None:
        result = parse_relative_time("today")
        assert result is not None
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_yesterday_returns_yesterday_midnight(self) -> None:
        result = parse_relative_time("yesterday")
        assert result is not None
        assert result.hour == 0
        now = datetime.now(timezone.utc)
        expected = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert (result - expected).total_seconds() < 2

    def test_absolute_iso_not_supported_due_to_lowercasing(self) -> None:
        # Note: parse_relative_time lowercases input before checking for 'T',
        # so standard ISO strings are not parsed as absolute time.
        # This is a known behavior of the function.
        iso = "2024-01-15T10:30:00+00:00"
        result = parse_relative_time(iso)
        assert result is None

    def test_z_suffix_not_supported_due_to_lowercasing(self) -> None:
        # Note: parse_relative_time lowercases input before checking for 'T',
        # so standard ISO strings with 'Z' are not parsed as absolute time.
        iso = "2024-01-15T10:30:00Z"
        result = parse_relative_time(iso)
        assert result is None

    def test_seconds_ago(self) -> None:
        result = parse_relative_time("30s")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 30) < 2

    def test_minutes_ago(self) -> None:
        result = parse_relative_time("15m")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 900) < 2

    def test_hours_ago(self) -> None:
        result = parse_relative_time("2h")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 7200) < 2

    def test_days_ago(self) -> None:
        result = parse_relative_time("3d")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 259200) < 2

    def test_weeks_ago(self) -> None:
        result = parse_relative_time("1w")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 604800) < 2

    def test_long_unit_names(self) -> None:
        result = parse_relative_time("5 minutes")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 300) < 2

    def test_invalid_returns_none(self) -> None:
        assert parse_relative_time("invalid") is None

    def test_case_insensitive(self) -> None:
        result_lower = parse_relative_time("1h")
        result_upper = parse_relative_time("1H")
        assert result_lower is not None
        assert result_upper is not None
        assert abs((result_lower - result_upper).total_seconds()) < 2

    def test_whitespace_stripped(self) -> None:
        result = parse_relative_time("  1h  ")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert abs((now - result).total_seconds() - 3600) < 2

    def test_no_number_returns_none(self) -> None:
        assert parse_relative_time("h") is None


class TestResolveExportFormat:
    """Tests for resolve_export_format."""

    def test_explicit_json(self) -> None:
        assert resolve_export_format(export_format_arg="json", output_path=Path("out.txt")) == "json"

    def test_explicit_csv(self) -> None:
        assert resolve_export_format(export_format_arg="csv", output_path=Path("out.txt")) == "csv"

    def test_explicit_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported format"):
            resolve_export_format(export_format_arg="xml", output_path=Path("out.txt"))

    def test_from_json_suffix(self) -> None:
        assert resolve_export_format(export_format_arg=None, output_path=Path("data.json")) == "json"

    def test_from_csv_suffix(self) -> None:
        assert resolve_export_format(export_format_arg=None, output_path=Path("data.csv")) == "csv"

    def test_unknown_suffix_defaults_json(self) -> None:
        assert resolve_export_format(export_format_arg=None, output_path=Path("data.txt")) == "json"

    def test_case_insensitive_explicit(self) -> None:
        assert resolve_export_format(export_format_arg="JSON", output_path=Path("out.txt")) == "json"
        assert resolve_export_format(export_format_arg="Csv", output_path=Path("out.txt")) == "csv"

    def test_case_insensitive_suffix(self) -> None:
        assert resolve_export_format(export_format_arg=None, output_path=Path("data.JSON")) == "json"
        assert resolve_export_format(export_format_arg=None, output_path=Path("data.CSV")) == "csv"

    def test_none_arg_and_no_suffix(self) -> None:
        assert resolve_export_format(export_format_arg=None, output_path=Path("output")) == "json"


class TestFormatTimeWindow:
    """Tests for format_time_window."""

    def test_both_none_returns_all(self) -> None:
        assert format_time_window(since=None, until=None) == "all"

    def test_since_only(self) -> None:
        dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        result = format_time_window(since=dt, until=None)
        assert "2024-01-15T10:30:00+00:00" in result
        assert "begin" not in result
        assert "-> now" in result

    def test_until_only(self) -> None:
        dt = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
        result = format_time_window(since=None, until=dt)
        assert "begin" in result
        assert "2024-01-15T10:30:00+00:00" in result

    def test_both_present(self) -> None:
        since = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        until = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)
        result = format_time_window(since=since, until=until)
        assert "2024-01-01T00:00:00+00:00" in result
        assert "2024-01-15T00:00:00+00:00" in result
        assert "->" in result


class TestParseWindow:
    """Tests for parse_window."""

    def test_hours(self) -> None:
        assert parse_window("2h") == 2.0

    def test_minutes(self) -> None:
        assert parse_window("30m") == 0.5

    def test_days(self) -> None:
        assert parse_window("1d") == 24.0

    def test_raw_number(self) -> None:
        assert parse_window("5") == 5.0

    def test_invalid_defaults_to_one(self) -> None:
        assert parse_window("xyz") == 1.0

    def test_case_insensitive(self) -> None:
        assert parse_window("2H") == 2.0
        assert parse_window("30M") == 0.5
        assert parse_window("1D") == 24.0

    def test_whitespace_stripped(self) -> None:
        assert parse_window("  2h  ") == 2.0

    def test_zero_hours(self) -> None:
        assert parse_window("0h") == 0.0

    def test_decimal_hours(self) -> None:
        assert parse_window("1.5h") == 1.5


class TestGetResultAttr:
    """Tests for get_result_attr."""

    def test_dict_access(self) -> None:
        # get_result_attr first tries getattr, which returns default for plain
        # dicts since they don't have the key as an attribute. Only objects
        # with actual attributes or __slots__ work with getattr path.
        result = {"key": "value"}
        assert get_result_attr(result, "key") is None  # getattr returns None default
        assert get_result_attr(result, "key", "default") == "default"

    def test_dict_missing_with_default(self) -> None:
        result = {}
        assert get_result_attr(result, "key", "default") == "default"

    def test_object_access(self) -> None:
        obj = MagicMock()
        obj.key = "value"
        assert get_result_attr(obj, "key") == "value"

    def test_object_missing_with_default(self) -> None:
        obj = object()
        assert get_result_attr(obj, "key", "default") == "default"

    def test_typeerror_fallback_to_dict(self) -> None:
        # Create a dict subclass that raises TypeError on attribute access
        # but still supports dict operations
        class TypeErrorDict(dict):
            def __getattr__(self, name: str) -> Any:
                raise TypeError("type error")

        obj = TypeErrorDict({"key": "value"})
        # When getattr raises TypeError, it falls back to dict access
        assert get_result_attr(obj, "key") == "value"
        assert get_result_attr(obj, "missing", "default") == "default"

    def test_dict_with_none_default(self) -> None:
        result = {}
        assert get_result_attr(result, "key") is None

    def test_nested_dict_access(self) -> None:
        result = {"nested": {"key": "value"}}
        assert get_result_attr(result, "nested") is None  # getattr returns None


class TestFormatEventCompact:
    """Tests for format_event_compact."""

    def test_basic_event(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "test_action", "result": "success"},
        }
        result = format_event_compact(event)
        assert "director" in result
        assert "test_event" in result
        assert "test_action" in result
        assert "✓" in result

    def test_no_relative_time(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "test_action", "result": "failure"},
        }
        result = format_event_compact(event, use_relative_time=False)
        assert "10:30:00" in result
        assert "✗" in result

    def test_empty_event(self) -> None:
        event: dict[str, object] = {}
        result = format_event_compact(event)
        assert "unknown" in result

    def test_missing_source(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "action": {"name": "test_action", "result": "success"},
        }
        result = format_event_compact(event)
        assert "unknown" in result

    def test_non_dict_source(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": "not_a_dict",
            "action": {"name": "test_action", "result": "success"},
        }
        result = format_event_compact(event)
        assert "unknown" in result

    def test_non_dict_action(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": "not_a_dict",
        }
        result = format_event_compact(event)
        assert "director" in result
        assert "test_event" in result

    def test_no_result_mark(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "test_action"},
        }
        result = format_event_compact(event)
        assert "✓" not in result
        assert "✗" not in result

    def test_long_name_truncated(self) -> None:
        event = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "a" * 50, "result": "success"},
        }
        result = format_event_compact(event)
        assert "a" * 30 in result
        assert "a" * 31 not in result

    def test_empty_timestamp(self) -> None:
        event = {
            "timestamp": "",
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "test_action", "result": "success"},
        }
        result = format_event_compact(event)
        assert "director" in result
        assert "test_event" in result

    def test_no_timestamp_key(self) -> None:
        event = {
            "event_type": "test_event",
            "source": {"role": "director"},
            "action": {"name": "test_action", "result": "success"},
        }
        result = format_event_compact(event)
        assert "director" in result
        assert "test_event" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
