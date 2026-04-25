"""Unit tests for BaseRoleAdapter pure/static logic (no I/O, no filesystem).

Covers:
- _coerce_board_task_id
- _resolve_kernel_validation_enabled
- _resolve_kernel_retry_budget
- _status_to_trace_type
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter, _status_to_trace_type

# ---------------------------------------------------------------------------
# _coerce_board_task_id
# ---------------------------------------------------------------------------


class TestCoerceBoardTaskId:
    def test_none_returns_none(self) -> None:
        assert BaseRoleAdapter._coerce_board_task_id(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self) -> None:
        assert BaseRoleAdapter._coerce_board_task_id("") is None
        assert BaseRoleAdapter._coerce_board_task_id("   ") is None

    def test_plain_integer(self) -> None:
        assert BaseRoleAdapter._coerce_board_task_id(42) == 42  # type: ignore[arg-type]
        assert BaseRoleAdapter._coerce_board_task_id("42") == 42

    def test_task_prefix(self) -> None:
        assert BaseRoleAdapter._coerce_board_task_id("task-123") == 123
        assert BaseRoleAdapter._coerce_board_task_id("task-456-extra") == 456

    def test_non_matching_string_returns_none(self) -> None:
        assert BaseRoleAdapter._coerce_board_task_id("abc") is None
        assert BaseRoleAdapter._coerce_board_task_id("task-abc") is None


# ---------------------------------------------------------------------------
# _resolve_kernel_validation_enabled
# ---------------------------------------------------------------------------


class TestResolveKernelValidationEnabled:
    def test_default_false(self) -> None:
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is False

    def test_context_override_true(self) -> None:
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", {"validate_output": True}) is True

    def test_context_override_false(self) -> None:
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", {"validate_output": False}) is False

    def test_env_override_true(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_VALIDATE_OUTPUT", "true")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is True

    def test_env_override_false(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_VALIDATE_OUTPUT", "false")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is False

    def test_context_takes_precedence_over_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_VALIDATE_OUTPUT", "true")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", {"validate_output": False}) is False

    def test_different_roles(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_QA_VALIDATE_OUTPUT", "true")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("qa", None) is True
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is False


# ---------------------------------------------------------------------------
# _resolve_kernel_retry_budget
# ---------------------------------------------------------------------------


class TestResolveKernelRetryBudget:
    def test_default_director(self) -> None:
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 1

    def test_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "3")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 3

    def test_env_clamped_max(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "10")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 3

    def test_env_clamped_min(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "-1")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 0

    def test_invalid_env_uses_default(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "abc")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 1

    def test_different_roles(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_QA_KERNEL_MAX_RETRIES", "2")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("qa") == 2
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 1


# ---------------------------------------------------------------------------
# _status_to_trace_type
# ---------------------------------------------------------------------------


class TestStatusToTraceType:
    def test_start_variants(self) -> None:
        assert _status_to_trace_type("start") == "start"
        assert _status_to_trace_type("starting") == "start"

    def test_error_variants(self) -> None:
        assert _status_to_trace_type("error") == "error"
        assert _status_to_trace_type("failed") == "error"
        assert _status_to_trace_type("failure") == "error"

    def test_complete_variants(self) -> None:
        assert _status_to_trace_type("complete") == "complete"
        assert _status_to_trace_type("completed") == "complete"
        assert _status_to_trace_type("done") == "complete"
        assert _status_to_trace_type("success") == "complete"

    def test_default_step(self) -> None:
        assert _status_to_trace_type("running") == "step"
        assert _status_to_trace_type("paused") == "step"
        assert _status_to_trace_type("") == "step"
