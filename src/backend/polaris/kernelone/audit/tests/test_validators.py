"""Tests for audit validators module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.kernelone.audit.contracts import KernelAuditEventType, KernelAuditRole
from polaris.kernelone.audit.validators import (
    SYSTEM_ROLE,
    derive_task_id,
    derive_trace_id,
    normalize_event_type,
    normalize_optional_mapping,
    normalize_role,
    normalize_workspace_path,
    require_valid_run_id,
    validate_run_id,
)


class TestValidateRunId:
    """Test suite for validate_run_id function."""

    def test_valid_run_id_simple(self) -> None:
        """Test valid simple run_id."""
        assert validate_run_id("run123") is True

    def test_valid_run_id_with_dots(self) -> None:
        """Test valid run_id with dots."""
        assert validate_run_id("run.123.456") is True

    def test_valid_run_id_with_underscores(self) -> None:
        """Test valid run_id with underscores."""
        assert validate_run_id("run_123_abc") is True

    def test_valid_run_id_with_hyphens(self) -> None:
        """Test valid run_id with hyphens."""
        assert validate_run_id("run-123-abc") is True

    def test_valid_run_id_with_colons(self) -> None:
        """Test valid run_id with colons."""
        assert validate_run_id("run:123:abc") is True

    def test_valid_run_id_minimum_length(self) -> None:
        """Test run_id with minimum valid length (3 chars)."""
        assert validate_run_id("abc") is True
        assert validate_run_id("a-b") is True
        assert validate_run_id("a.b") is True

    def test_invalid_empty_run_id(self) -> None:
        """Test invalid empty run_id."""
        assert validate_run_id("") is False
        assert validate_run_id("   ") is False

    def test_invalid_none_run_id(self) -> None:
        """Test invalid None run_id."""
        assert validate_run_id(None) is False

    def test_invalid_single_char(self) -> None:
        """Test invalid single character run_id (must be at least 3)."""
        assert validate_run_id("a") is False
        assert validate_run_id("ab") is False

    def test_invalid_starts_with_number(self) -> None:
        """Test run_id starting with number is actually valid (regex allows alphanumeric start)."""
        # The regex ^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$ allows starting with number
        assert validate_run_id("1run") is True
        assert validate_run_id("123abc") is True

    def test_invalid_special_chars(self) -> None:
        """Test invalid special characters."""
        assert validate_run_id("run@123") is False
        assert validate_run_id("run#123") is False
        assert validate_run_id("run$123") is False
        assert validate_run_id("run!123") is False

    def test_invalid_space(self) -> None:
        """Test run_id with spaces is invalid."""
        assert validate_run_id("run 123") is False

    def test_valid_long_run_id(self) -> None:
        """Test valid long run_id (up to 128 chars)."""
        long_id = "a" + "b" * 127
        assert validate_run_id(long_id) is True

    def test_invalid_too_long_run_id(self) -> None:
        """Test run_id exceeding maximum length."""
        too_long_id = "a" + "b" * 128
        assert validate_run_id(too_long_id) is False


class TestRequireValidRunId:
    """Test suite for require_valid_run_id function."""

    def test_valid_run_id_returns_token(self) -> None:
        """Test valid run_id is returned stripped."""
        result = require_valid_run_id("  run123  ")
        assert result == "run123"

    def test_empty_run_id_returns_empty(self) -> None:
        """Test that empty run_id is returned as-is (no raise for empty)."""
        # Empty string returns empty string, doesn't raise
        result = require_valid_run_id("")
        assert result == ""

    def test_whitespace_only_returns_empty(self) -> None:
        """Test that whitespace-only run_id is returned as empty string."""
        result = require_valid_run_id("   ")
        assert result == ""

    def test_invalid_run_id_raises(self) -> None:
        """Test invalid non-empty run_id raises ValueError."""
        with pytest.raises(ValueError, match="invalid run_id"):
            require_valid_run_id("invalid@run")

    def test_invalid_run_id_raises_with_none(self) -> None:
        """Test None run_id returns empty string (not raises)."""
        # None is converted to empty string
        result = require_valid_run_id(None)  # type: ignore[arg-type]
        assert result == ""

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        result = require_valid_run_id("  valid  ")
        assert result == "valid"

    def test_valid_run_id_preserves_case(self) -> None:
        """Test that case is preserved."""
        result = require_valid_run_id("Run123")
        assert result == "Run123"


class TestNormalizeWorkspacePath:
    """Test suite for normalize_workspace_path function."""

    def test_returns_absolute_path(self) -> None:
        """Test that absolute path is returned."""
        import os

        result = normalize_workspace_path("/tmp/test")
        assert os.path.isabs(result)

    def test_empty_defaults_to_cwd(self) -> None:
        """Test that empty input defaults to current working directory."""
        import os

        result = normalize_workspace_path("")
        assert result == os.getcwd()

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        result = normalize_workspace_path("  /tmp/test  ")
        assert result.endswith("test")

    def test_resolves_relative_path(self) -> None:
        """Test that relative path is resolved."""
        result = normalize_workspace_path("./test")
        assert "./" not in result


class TestNormalizeOptionalMapping:
    """Test suite for normalize_optional_mapping function."""

    def test_valid_dict(self) -> None:
        """Test normalization of valid dict."""
        result = normalize_optional_mapping({"key": "value", "num": 42})
        assert result == {"key": "value", "num": 42}

    def test_none_returns_empty_dict(self) -> None:
        """Test that None returns empty dict."""
        result = normalize_optional_mapping(None)
        assert result == {}

    def test_non_dict_returns_empty_dict(self) -> None:
        """Test that non-dict input returns empty dict."""
        assert normalize_optional_mapping("string") == {}
        assert normalize_optional_mapping([1, 2, 3]) == {}
        assert normalize_optional_mapping(123) == {}

    def test_converts_keys_to_strings(self) -> None:
        """Test that non-string keys are converted to strings."""
        result = normalize_optional_mapping({1: "one", "two": 2})
        assert "1" in result
        assert "two" in result


class TestDeriveTaskId:
    """Test suite for derive_task_id function."""

    def test_uses_run_id_when_provided(self) -> None:
        """Test task_id is derived from run_id when provided."""
        result = derive_task_id("my-run-123")
        assert result == "task-my-run-123"

    def test_derives_timestamp_when_no_run_id(self) -> None:
        """Test timestamp-based derivation when no run_id."""
        result = derive_task_id("")
        assert result.startswith("derived-")
        assert len(result) > 10

    def test_strips_run_id(self) -> None:
        """Test that run_id is stripped."""
        result = derive_task_id("  run-123  ")
        assert result == "task-run-123"

    def test_custom_timestamp(self) -> None:
        """Test with custom timestamp."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = derive_task_id("", now=custom_time)
        assert result == "derived-20240115103000"


class TestDeriveTraceId:
    """Test suite for derive_trace_id function."""

    def test_returns_hex_string(self) -> None:
        """Test that trace_id is a hex string."""
        result = derive_trace_id()
        assert all(c in "0123456789abcdef" for c in result)

    def test_length_is_16(self) -> None:
        """Test that trace_id is 16 characters."""
        result = derive_trace_id()
        assert len(result) == 16

    def test_unique_per_call(self) -> None:
        """Test that each call returns a unique ID."""
        ids = {derive_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestNormalizeEventType:
    """Test suite for normalize_event_type function."""

    def test_passes_enum_through(self) -> None:
        """Test that enum values pass through unchanged."""
        event_type = KernelAuditEventType.TASK_START
        result = normalize_event_type(event_type)
        assert result is event_type

    def test_converts_string_to_enum(self) -> None:
        """Test that string is converted to enum."""
        result = normalize_event_type("task_start")
        assert result == KernelAuditEventType.TASK_START

    def test_converts_llm_call_string(self) -> None:
        """Test LLM_CALL string conversion."""
        result = normalize_event_type("llm_call")
        assert result == KernelAuditEventType.LLM_CALL

    def test_handles_whitespace(self) -> None:
        """Test handling of whitespace."""
        result = normalize_event_type("  llm_call  ")
        assert result == KernelAuditEventType.LLM_CALL

    def test_invalid_string_raises(self) -> None:
        """Test that invalid string raises ValueError."""
        with pytest.raises(ValueError, match="not a valid KernelAuditEventType"):
            normalize_event_type("invalid_type")

    def test_empty_string_raises(self) -> None:
        """Test empty string raises ValueError."""
        with pytest.raises(ValueError, match="not a valid KernelAuditEventType"):
            normalize_event_type("")


class TestNormalizeRole:
    """Test suite for normalize_role function."""

    def test_passes_enum_value(self) -> None:
        """Test that enum values return their string value."""
        result = normalize_role(KernelAuditRole.SYSTEM)
        assert result == "system"

    def test_passes_string_through(self) -> None:
        """Test that strings pass through unchanged."""
        result = normalize_role("director")
        assert result == "director"

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        result = normalize_role("  director  ")
        assert result == "director"

    def test_empty_string(self) -> None:
        """Test empty string handling."""
        result = normalize_role("")
        assert result == ""

    def test_none_returns_empty(self) -> None:
        """Test that None returns empty string."""
        result = normalize_role(None)  # type: ignore[arg-type]
        assert result == ""


class TestSystemRole:
    """Test suite for SYSTEM_ROLE constant."""

    def test_is_system_string(self) -> None:
        """Test that SYSTEM_ROLE is 'system' string."""
        assert SYSTEM_ROLE == "system"
        assert isinstance(SYSTEM_ROLE, str)
