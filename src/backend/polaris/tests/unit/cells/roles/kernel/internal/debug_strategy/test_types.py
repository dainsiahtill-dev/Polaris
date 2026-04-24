"""Tests for polaris.cells.roles.kernel.internal.debug_strategy.types."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
    DefenseLayer,
    ErrorCategory,
)


class TestDebugPhase:
    def test_members(self) -> None:
        assert DebugPhase.ROOT_CAUSE_INVESTIGATION.name == "ROOT_CAUSE_INVESTIGATION"
        assert DebugPhase.PATTERN_ANALYSIS.name == "PATTERN_ANALYSIS"
        assert DebugPhase.HYPOTHESIS_TESTING.name == "HYPOTHESIS_TESTING"
        assert DebugPhase.IMPLEMENTATION.name == "IMPLEMENTATION"


class TestDebugStrategy:
    def test_values(self) -> None:
        assert DebugStrategy.TRACE_BACKWARD.value == "trace_backward"
        assert DebugStrategy.PATTERN_MATCH.value == "pattern_match"
        assert DebugStrategy.BINARY_SEARCH.value == "binary_search"
        assert DebugStrategy.CONDITIONAL_WAIT.value == "conditional_wait"
        assert DebugStrategy.DEFENSE_IN_DEPTH.value == "defense_in_depth"


class TestDefenseLayer:
    def test_members(self) -> None:
        assert DefenseLayer.INPUT_VALIDATION.name == "INPUT_VALIDATION"
        assert DefenseLayer.PRECONDITION_CHECK.name == "PRECONDITION_CHECK"
        assert DefenseLayer.INVARIANT_ASSERTION.name == "INVARIANT_ASSERTION"
        assert DefenseLayer.POSTCONDITION_VERIFY.name == "POSTCONDITION_VERIFY"


class TestErrorCategory:
    def test_values(self) -> None:
        assert ErrorCategory.SYNTAX_ERROR.value == "syntax_error"
        assert ErrorCategory.RUNTIME_ERROR.value == "runtime_error"
        assert ErrorCategory.LOGIC_ERROR.value == "logic_error"
        assert ErrorCategory.TIMING_ERROR.value == "timing_error"
        assert ErrorCategory.RESOURCE_ERROR.value == "resource_error"
        assert ErrorCategory.PERMISSION_ERROR.value == "permission_error"
        assert ErrorCategory.NETWORK_ERROR.value == "network_error"
        assert ErrorCategory.UNKNOWN_ERROR.value == "unknown_error"
