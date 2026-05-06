# ruff: noqa: E402
"""Tests for polaris.domain.entities.workflow module.

Covers:
- ExecutionMode enum
- PMWorkflowResult and DirectorWorkflowResult dataclasses
- Coercion helpers (_coerce_positive_int, _coerce_execution_mode)
- Immutability and state integrity
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.entities.workflow import (
    DirectorWorkflowResult,
    ExecutionMode,
    PMWorkflowResult,
    _coerce_execution_mode,
    _coerce_positive_int,
)

# =============================================================================
# ExecutionMode
# =============================================================================


class TestExecutionMode:
    def test_sequential_value(self) -> None:
        assert ExecutionMode.SEQUENTIAL.value == "sequential"

    def test_parallel_value(self) -> None:
        assert ExecutionMode.PARALLEL.value == "parallel"

    def test_is_str_enum(self) -> None:
        assert isinstance(ExecutionMode.SEQUENTIAL, str)
        assert ExecutionMode.SEQUENTIAL == "sequential"

    def test_from_string_valid(self) -> None:
        assert ExecutionMode("sequential") == ExecutionMode.SEQUENTIAL
        assert ExecutionMode("parallel") == ExecutionMode.PARALLEL

    def test_from_string_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            ExecutionMode("invalid")

    def test_membership(self) -> None:
        assert "sequential" in {e.value for e in ExecutionMode}
        assert "parallel" in {e.value for e in ExecutionMode}


# =============================================================================
# PMWorkflowResult
# =============================================================================


class TestPMWorkflowResult:
    def test_basic_construction(self) -> None:
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[{"id": 1}],
            director_status="running",
            qa_status="pending",
        )
        assert result.run_id == "run-1"
        assert result.tasks == [{"id": 1}]

    def test_default_metadata(self) -> None:
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[],
            director_status="idle",
            qa_status="idle",
        )
        assert result.metadata == {}

    def test_custom_metadata(self) -> None:
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[],
            director_status="idle",
            qa_status="idle",
            metadata={"version": "2.0"},
        )
        assert result.metadata["version"] == "2.0"

    def test_immutability(self) -> None:
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[],
            director_status="idle",
            qa_status="idle",
        )
        with pytest.raises(AttributeError):
            result.run_id = "run-2"

    def test_tasks_can_be_any_type(self) -> None:
        result = PMWorkflowResult(
            run_id="run-1",
            tasks=[1, "two", {"three": 3}],
            director_status="idle",
            qa_status="idle",
        )
        assert len(result.tasks) == 3

    def test_repr_contains_run_id(self) -> None:
        result = PMWorkflowResult(
            run_id="run-abc",
            tasks=[],
            director_status="idle",
            qa_status="idle",
        )
        assert "run-abc" in repr(result)


# =============================================================================
# DirectorWorkflowResult
# =============================================================================


class TestDirectorWorkflowResult:
    def test_basic_construction(self) -> None:
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=5,
            failed_tasks=1,
        )
        assert result.run_id == "run-1"
        assert result.completed_tasks == 5
        assert result.failed_tasks == 1

    def test_default_metadata(self) -> None:
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=0,
            failed_tasks=0,
        )
        assert result.metadata == {}

    def test_zero_tasks(self) -> None:
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=0,
            failed_tasks=0,
        )
        assert result.completed_tasks == 0

    def test_immutability(self) -> None:
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=1,
            failed_tasks=0,
        )
        with pytest.raises(AttributeError):
            result.status = "failed"

    def test_negative_task_counts_allowed(self) -> None:
        # Dataclass does not enforce positivity; coercion helper does
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=-1,
            failed_tasks=-2,
        )
        assert result.completed_tasks == -1

    def test_repr_contains_status(self) -> None:
        result = DirectorWorkflowResult(
            run_id="run-1",
            status="completed",
            completed_tasks=1,
            failed_tasks=0,
        )
        assert "completed" in repr(result)


# =============================================================================
# _coerce_positive_int
# =============================================================================


class TestCoercePositiveInt:
    def test_none_returns_default(self) -> None:
        assert _coerce_positive_int(None, 5) == 5

    def test_positive_int(self) -> None:
        assert _coerce_positive_int(10, 5) == 10

    def test_zero_returns_one(self) -> None:
        assert _coerce_positive_int(0, 5) == 1

    def test_negative_returns_one(self) -> None:
        assert _coerce_positive_int(-5, 5) == 1

    def test_string_number(self) -> None:
        assert _coerce_positive_int("42", 5) == 42

    def test_invalid_string_returns_default(self) -> None:
        assert _coerce_positive_int("abc", 7) == 7

    def test_float_coerced_to_int(self) -> None:
        assert _coerce_positive_int(3.7, 5) == 3

    def test_float_zero_returns_one(self) -> None:
        assert _coerce_positive_int(0.0, 5) == 1

    def test_list_returns_default(self) -> None:
        assert _coerce_positive_int([1, 2], 5) == 5

    def test_dict_returns_default(self) -> None:
        assert _coerce_positive_int({"a": 1}, 5) == 5

    def test_default_zero_becomes_one(self) -> None:
        assert _coerce_positive_int(None, 0) == 1

    def test_large_value(self) -> None:
        assert _coerce_positive_int(1_000_000, 1) == 1_000_000

    def test_boolean_true(self) -> None:
        assert _coerce_positive_int(True, 5) == 1

    def test_boolean_false(self) -> None:
        assert _coerce_positive_int(False, 5) == 1


# =============================================================================
# _coerce_execution_mode
# =============================================================================


class TestCoerceExecutionMode:
    def test_none_returns_default(self) -> None:
        assert _coerce_execution_mode(None) == "parallel"

    def test_empty_string_returns_default(self) -> None:
        assert _coerce_execution_mode("") == "parallel"

    def test_sequential_lowercase(self) -> None:
        assert _coerce_execution_mode("sequential") == "sequential"

    def test_serial_lowercase(self) -> None:
        assert _coerce_execution_mode("serial") == "serial"

    def test_parallel_lowercase(self) -> None:
        assert _coerce_execution_mode("parallel") == "parallel"

    def test_sequential_mixed_case(self) -> None:
        assert _coerce_execution_mode("Sequential") == "sequential"

    def test_parallel_uppercase(self) -> None:
        assert _coerce_execution_mode("PARALLEL") == "parallel"

    def test_invalid_returns_default(self) -> None:
        assert _coerce_execution_mode("fast") == "parallel"

    def test_invalid_returns_custom_default(self) -> None:
        assert _coerce_execution_mode("fast", default="sequential") == "sequential"

    def test_whitespace_trimmed(self) -> None:
        assert _coerce_execution_mode("  sequential  ") == "sequential"

    def test_number_returns_default(self) -> None:
        assert _coerce_execution_mode(123) == "parallel"

    def test_enum_value(self) -> None:
        # Passing the enum member directly may not coerce correctly in all Python
        # versions because str(enum_member) behaviour varies; coerce via .value.
        assert _coerce_execution_mode(ExecutionMode.SEQUENTIAL.value) == "sequential"

    def test_serial_enum_value(self) -> None:
        assert _coerce_execution_mode(ExecutionMode.SERIAL.value) == "serial"

    def test_string_parallel_with_spaces(self) -> None:
        assert _coerce_execution_mode(" parallel ") == "parallel"

    def test_partial_match_not_allowed(self) -> None:
        assert _coerce_execution_mode("seq") == "parallel"
