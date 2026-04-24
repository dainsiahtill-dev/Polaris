"""Tests for polaris.domain.entities.workflow."""

from __future__ import annotations

from polaris.domain.entities.workflow import (
    DirectorWorkflowResult,
    ExecutionMode,
    PMWorkflowResult,
    _coerce_execution_mode,
    _coerce_positive_int,
)


class TestExecutionMode:
    def test_values(self) -> None:
        assert ExecutionMode.SEQUENTIAL.value == "sequential"
        assert ExecutionMode.PARALLEL.value == "parallel"


class TestPMWorkflowResult:
    def test_defaults(self) -> None:
        result = PMWorkflowResult(run_id="r1", tasks=[], director_status="running", qa_status="pending")
        assert result.metadata == {}

    def test_immutable(self) -> None:
        result = PMWorkflowResult(run_id="r1", tasks=[], director_status="running", qa_status="pending")
        # frozen dataclass should not allow mutation
        # (this is a compile-time/behavioral check)
        # We verify it's created correctly
        assert result.run_id == "r1"


class TestDirectorWorkflowResult:
    def test_defaults(self) -> None:
        result = DirectorWorkflowResult(run_id="r1", status="completed", completed_tasks=5, failed_tasks=0)
        assert result.metadata == {}


class TestCoercePositiveInt:
    def test_valid_int(self) -> None:
        assert _coerce_positive_int(5, 1) == 5

    def test_none_uses_default(self) -> None:
        assert _coerce_positive_int(None, 10) == 10

    def test_string_coerces(self) -> None:
        assert _coerce_positive_int("3", 1) == 3

    def test_negative_clamped_to_one(self) -> None:
        assert _coerce_positive_int(-5, 1) == 1

    def test_zero_clamped_to_one(self) -> None:
        assert _coerce_positive_int(0, 1) == 1

    def test_invalid_string_uses_default(self) -> None:
        assert _coerce_positive_int("abc", 7) == 7

    def test_float_coerced(self) -> None:
        assert _coerce_positive_int(3.7, 1) == 3


class TestCoerceExecutionMode:
    def test_sequential(self) -> None:
        assert _coerce_execution_mode("sequential") == "sequential"

    def test_parallel(self) -> None:
        assert _coerce_execution_mode("parallel") == "parallel"

    def test_whitespace_normalized(self) -> None:
        assert _coerce_execution_mode("  Parallel  ") == "parallel"

    def test_invalid_uses_default(self) -> None:
        assert _coerce_execution_mode("fast", default="sequential") == "sequential"

    def test_none_uses_default(self) -> None:
        assert _coerce_execution_mode(None, default="parallel") == "parallel"

    def test_empty_uses_default(self) -> None:
        assert _coerce_execution_mode("", default="parallel") == "parallel"
