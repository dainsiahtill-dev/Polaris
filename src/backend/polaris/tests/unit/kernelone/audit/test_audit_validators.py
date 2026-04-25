"""Tests for polaris.kernelone.audit.validators."""

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


class TestSystemRole:
    def test_value(self) -> None:
        assert SYSTEM_ROLE == "system"


class TestValidateRunId:
    def test_valid_ids(self) -> None:
        assert validate_run_id("run-123") is True
        assert validate_run_id("abc") is True
        assert validate_run_id("a.b.c") is True
        assert validate_run_id("a:b:c") is True
        assert validate_run_id("a" * 128) is True

    def test_invalid_ids(self) -> None:
        assert validate_run_id("") is False
        assert validate_run_id("  ") is False
        assert validate_run_id("ab") is False  # too short
        assert validate_run_id("a" * 129) is False  # too long
        assert validate_run_id("-start") is False  # must start with alnum

    def test_none(self) -> None:
        assert validate_run_id(None) is False  # type: ignore[arg-type]


class TestRequireValidRunId:
    def test_valid(self) -> None:
        assert require_valid_run_id("run-123") == "run-123"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid run_id"):
            require_valid_run_id("")
        with pytest.raises(ValueError, match="invalid run_id"):
            require_valid_run_id("ab")

    def test_invalid_with_message(self) -> None:
        with pytest.raises(ValueError, match="invalid run_id: xx"):
            require_valid_run_id("xx")


class TestNormalizeWorkspacePath:
    def test_empty_defaults_to_cwd(self) -> None:
        result = normalize_workspace_path("")
        assert result != ""

    def test_normal_path(self) -> None:
        result = normalize_workspace_path("/tmp")
        assert "tmp" in result


class TestNormalizeOptionalMapping:
    def test_none(self) -> None:
        assert normalize_optional_mapping(None) == {}

    def test_dict(self) -> None:
        assert normalize_optional_mapping({"a": 1}) == {"a": 1}

    def test_non_dict(self) -> None:
        assert normalize_optional_mapping("not a dict") == {}  # type: ignore[arg-type]


class TestDeriveTaskId:
    def test_with_run_id(self) -> None:
        assert derive_task_id("run-123") == "task-run-123"

    def test_without_run_id(self) -> None:
        result = derive_task_id("")
        assert result.startswith("derived-")

    def test_with_explicit_now(self) -> None:
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = derive_task_id("", now=now)
        assert result == "derived-20240101120000"


class TestDeriveTraceId:
    def test_format(self) -> None:
        result = derive_trace_id()
        assert len(result) == 16
        assert result.isalnum()

    def test_unique(self) -> None:
        ids = {derive_trace_id() for _ in range(10)}
        assert len(ids) == 10


class TestNormalizeEventType:
    def test_enum_passes(self) -> None:
        assert normalize_event_type(KernelAuditEventType.TASK_START) == KernelAuditEventType.TASK_START

    def test_string_converted(self) -> None:
        assert normalize_event_type("task_start") == KernelAuditEventType.TASK_START

    def test_empty_defaults(self) -> None:
        assert normalize_event_type("") == KernelAuditEventType.TASK_START


class TestNormalizeRole:
    def test_enum_converted(self) -> None:
        assert normalize_role(KernelAuditRole.SYSTEM) == "system"

    def test_string_passes(self) -> None:
        assert normalize_role("pm") == "pm"

    def test_empty(self) -> None:
        assert normalize_role("") == ""
