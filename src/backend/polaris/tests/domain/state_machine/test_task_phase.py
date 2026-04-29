"""Tests for polaris.domain.state_machine.task_phase.

Covers:
- TaskPhase enum values and semantics
- PhaseContext, PhaseResult, PhaseTransition dataclasses
- TaskStateMachine state transitions, validation, business logic
"""

from __future__ import annotations

from datetime import datetime

import pytest
from polaris.domain.state_machine.task_phase import (
    _TERMINAL_PHASES,
    PhaseContext,
    PhaseResult,
    PhaseTransition,
    TaskPhase,
    TaskStateMachine,
)
from polaris.kernelone.errors import InvalidStateTransitionError

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def machine() -> TaskStateMachine:
    """Return a fresh TaskStateMachine in PENDING state."""
    return TaskStateMachine(task_id="task-1")


@pytest.fixture
def machine_with_context() -> TaskStateMachine:
    """Return a TaskStateMachine with a custom context."""
    ctx = PhaseContext(
        task_id="task-2",
        workspace="/tmp/ws",
        plan="do things",
        max_build_rounds=3,
    )
    return TaskStateMachine(task_id="task-2", initial_context=ctx)


# =============================================================================
# TaskPhase Enum
# =============================================================================


class TestTaskPhase:
    def test_members_exist(self) -> None:
        assert TaskPhase.PENDING == "pending"
        assert TaskPhase.PLANNING == "planning"
        assert TaskPhase.VALIDATION == "validation"
        assert TaskPhase.EXECUTION == "execution"
        assert TaskPhase.VERIFICATION == "verification"
        assert TaskPhase.COMPLETED == "completed"
        assert TaskPhase.FAILED == "failed"
        assert TaskPhase.ROLLED_BACK == "rolled_back"

    def test_is_strenum(self) -> None:
        assert isinstance(TaskPhase.PENDING, str)
        assert TaskPhase.PENDING.value == "pending"

    def test_comparison(self) -> None:
        assert TaskPhase.PENDING == "pending"
        assert TaskPhase.PENDING != "planning"

    def test_terminal_phases_set(self) -> None:
        assert TaskPhase.COMPLETED in _TERMINAL_PHASES
        assert TaskPhase.FAILED in _TERMINAL_PHASES
        assert TaskPhase.ROLLED_BACK in _TERMINAL_PHASES
        assert TaskPhase.PENDING not in _TERMINAL_PHASES
        assert len(_TERMINAL_PHASES) == 3


# =============================================================================
# PhaseContext
# =============================================================================


class TestPhaseContext:
    def test_defaults(self) -> None:
        ctx = PhaseContext(task_id="t1", workspace=".")
        assert ctx.task_id == "t1"
        assert ctx.workspace == "."
        assert ctx.plan == ""
        assert ctx.blueprint == {}
        assert ctx.policy_check_result == {}
        assert ctx.snapshot_path is None
        assert ctx.changed_files == []
        assert ctx.verification_result == {}
        assert ctx.build_round == 0
        assert ctx.max_build_rounds == 4
        assert ctx.stall_count == 0
        assert ctx.previous_missing_targets == []
        assert ctx.previous_unresolved_imports == []
        assert ctx.metadata == {}

    def test_custom_values(self) -> None:
        ctx = PhaseContext(
            task_id="t1",
            workspace="/ws",
            plan="plan",
            blueprint={"key": "val"},
            build_round=2,
            stall_count=1,
        )
        assert ctx.plan == "plan"
        assert ctx.blueprint == {"key": "val"}
        assert ctx.build_round == 2
        assert ctx.stall_count == 1


# =============================================================================
# PhaseResult
# =============================================================================


class TestPhaseResult:
    def test_defaults(self) -> None:
        result = PhaseResult(success=True, phase=TaskPhase.PLANNING)
        assert result.success is True
        assert result.phase == TaskPhase.PLANNING
        assert result.message == ""
        assert result.error_code is None
        assert result.can_retry is False
        assert result.should_rollback is False
        assert result.context_updates == {}
        assert result.next_phase is None
        assert result.artifacts == {}

    def test_full_construction(self) -> None:
        result = PhaseResult(
            success=False,
            phase=TaskPhase.VERIFICATION,
            message="oops",
            error_code="E1",
            can_retry=True,
            should_rollback=True,
            context_updates={"k": "v"},
            next_phase=TaskPhase.EXECUTION,
            artifacts={"log": "data"},
        )
        assert result.message == "oops"
        assert result.error_code == "E1"
        assert result.can_retry is True
        assert result.should_rollback is True
        assert result.context_updates == {"k": "v"}
        assert result.next_phase == TaskPhase.EXECUTION


# =============================================================================
# PhaseTransition
# =============================================================================


class TestPhaseTransition:
    def test_defaults(self) -> None:
        pt = PhaseTransition(
            from_phase=TaskPhase.PENDING,
            to_phase=TaskPhase.PLANNING,
        )
        assert pt.from_phase == TaskPhase.PENDING
        assert pt.to_phase == TaskPhase.PLANNING
        assert pt.success is True
        assert pt.message == ""
        assert isinstance(pt.timestamp, datetime)

    def test_custom_values(self) -> None:
        pt = PhaseTransition(
            from_phase=TaskPhase.EXECUTION,
            to_phase=TaskPhase.FAILED,
            success=False,
            message="error",
        )
        assert pt.success is False
        assert pt.message == "error"


# =============================================================================
# TaskStateMachine Initialization
# =============================================================================


class TestTaskStateMachineInit:
    def test_default_init(self, machine: TaskStateMachine) -> None:
        assert machine.task_id == "task-1"
        assert machine.current_phase == TaskPhase.PENDING
        assert machine.context.task_id == "task-1"
        assert machine.context.workspace == "."
        assert machine.transitions == []
        assert machine.phase_results == {}
        assert machine._phase_start_time is None

    def test_init_with_context(self, machine_with_context: TaskStateMachine) -> None:
        assert machine_with_context.task_id == "task-2"
        assert machine_with_context.context.workspace == "/tmp/ws"
        assert machine_with_context.context.plan == "do things"

    def test_transitions_dict_complete(self) -> None:
        # Verify every phase has an entry
        all_phases = set(TaskPhase)
        transition_keys = set(TaskStateMachine.TRANSITIONS.keys())
        assert all_phases == transition_keys

    def test_terminal_phases_have_no_outgoing(self) -> None:
        assert TaskStateMachine.TRANSITIONS[TaskPhase.COMPLETED] == []

    def test_failed_can_restart(self) -> None:
        assert TaskPhase.PLANNING in TaskStateMachine.TRANSITIONS[TaskPhase.FAILED]

    def test_rolled_back_can_restart(self) -> None:
        assert TaskPhase.PLANNING in TaskStateMachine.TRANSITIONS[TaskPhase.ROLLED_BACK]


# =============================================================================
# current_state property
# =============================================================================


class TestCurrentState:
    def test_returns_taskphase(self, machine: TaskStateMachine) -> None:
        assert machine.current_state == TaskPhase.PENDING

    def test_after_transition(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        assert machine.current_state == TaskPhase.PLANNING


# =============================================================================
# can_transition_to
# =============================================================================


class TestCanTransitionTo:
    def test_pending_to_planning(self, machine: TaskStateMachine) -> None:
        assert machine.can_transition_to(TaskPhase.PLANNING) is True

    def test_pending_to_execution_invalid(self, machine: TaskStateMachine) -> None:
        assert machine.can_transition_to(TaskPhase.EXECUTION) is False

    def test_planning_to_validation(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        assert machine.can_transition_to(TaskPhase.VALIDATION) is True

    def test_planning_to_failed(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        assert machine.can_transition_to(TaskPhase.FAILED) is True

    def test_execution_to_rolled_back(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.VALIDATION)
        machine.transition_to(TaskPhase.EXECUTION)
        assert machine.can_transition_to(TaskPhase.ROLLED_BACK) is True

    def test_verification_to_execution_retry(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        assert machine.can_transition_to(TaskPhase.EXECUTION) is True

    def test_completed_no_transitions(self, machine: TaskStateMachine) -> None:
        _advance_to_completed(machine)
        assert machine.can_transition_to(TaskPhase.PLANNING) is False
        assert machine.can_transition_to(TaskPhase.FAILED) is False

    def test_self_transition_invalid(self, machine: TaskStateMachine) -> None:
        assert machine.can_transition_to(TaskPhase.PENDING) is False


# =============================================================================
# transition_to
# =============================================================================


class TestTransitionTo:
    def test_valid_transition(self, machine: TaskStateMachine) -> None:
        assert machine.transition_to(TaskPhase.PLANNING) is True
        assert machine.current_phase == TaskPhase.PLANNING

    def test_records_transition(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING, message="starting")
        assert len(machine.transitions) == 1
        assert machine.transitions[0].from_phase == TaskPhase.PENDING
        assert machine.transitions[0].to_phase == TaskPhase.PLANNING
        assert machine.transitions[0].message == "starting"

    def test_sets_phase_start_time(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        assert machine._phase_start_time is not None
        assert isinstance(machine._phase_start_time, datetime)

    def test_invalid_transition_raises(self, machine: TaskStateMachine) -> None:
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            machine.transition_to(TaskPhase.COMPLETED)
        assert "Invalid state transition" in str(exc_info.value)
        assert exc_info.value.current_state == str(TaskPhase.PENDING)
        assert exc_info.value.target_state == str(TaskPhase.COMPLETED)
        assert "PLANNING" in exc_info.value.allowed_transitions

    def test_force_invalid_transition(self, machine: TaskStateMachine) -> None:
        # Force should bypass validation
        assert machine.transition_to(TaskPhase.COMPLETED, force=True) is True
        assert machine.current_phase == TaskPhase.COMPLETED

    def test_multiple_transitions(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.VALIDATION)
        machine.transition_to(TaskPhase.EXECUTION)
        assert len(machine.transitions) == 3

    def test_force_records_transition_too(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.COMPLETED, force=True)
        assert len(machine.transitions) == 1


# =============================================================================
# is_terminal
# =============================================================================


class TestIsTerminal:
    def test_pending_not_terminal(self, machine: TaskStateMachine) -> None:
        assert machine.is_terminal() is False

    def test_completed_terminal(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.VALIDATION)
        machine.transition_to(TaskPhase.EXECUTION)
        machine.transition_to(TaskPhase.VERIFICATION)
        machine.transition_to(TaskPhase.COMPLETED)
        assert machine.is_terminal() is True

    def test_failed_terminal(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.FAILED)
        assert machine.is_terminal() is True

    def test_rolled_back_terminal(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.VALIDATION)
        machine.transition_to(TaskPhase.EXECUTION)
        machine.transition_to(TaskPhase.ROLLED_BACK)
        assert machine.is_terminal() is True


# =============================================================================
# record_phase_result
# =============================================================================


class TestRecordPhaseResult:
    def test_records_result(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(success=True, phase=TaskPhase.PLANNING)
        machine.record_phase_result(result)
        assert machine.phase_results[TaskPhase.PLANNING] == result

    def test_applies_context_updates(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(
            success=True,
            phase=TaskPhase.PLANNING,
            context_updates={"plan": "new plan", "build_round": 1},
        )
        machine.record_phase_result(result)
        assert machine.context.plan == "new plan"
        assert machine.context.build_round == 1

    def test_ignores_unknown_context_keys(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(
            success=True,
            phase=TaskPhase.PLANNING,
            context_updates={"nonexistent_key": 42},
        )
        machine.record_phase_result(result)
        assert not hasattr(machine.context, "nonexistent_key")

    def test_auto_transition_on_success(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(
            success=True,
            phase=TaskPhase.PLANNING,
            next_phase=TaskPhase.VALIDATION,
        )
        machine.record_phase_result(result)
        assert machine.current_phase == TaskPhase.VALIDATION
        assert len(machine.transitions) == 2  # planning + auto transition

    def test_no_auto_transition_on_failure(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(
            success=False,
            phase=TaskPhase.PLANNING,
            next_phase=TaskPhase.FAILED,
        )
        machine.record_phase_result(result)
        # next_phase is ignored because success is False
        assert machine.current_phase == TaskPhase.PLANNING

    def test_no_auto_transition_without_next_phase(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        result = PhaseResult(success=True, phase=TaskPhase.PLANNING)
        machine.record_phase_result(result)
        assert machine.current_phase == TaskPhase.PLANNING


# =============================================================================
# get_phase_duration
# =============================================================================


class TestGetPhaseDuration:
    def test_before_any_transition(self, machine: TaskStateMachine) -> None:
        assert machine.get_phase_duration() is None

    def test_after_transition(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        duration = machine.get_phase_duration()
        assert duration is not None
        assert duration >= 0.0


# =============================================================================
# get_trajectory
# =============================================================================


class TestGetTrajectory:
    def test_empty_trajectory(self, machine: TaskStateMachine) -> None:
        assert machine.get_trajectory() == []

    def test_single_transition(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING, message="go")
        traj = machine.get_trajectory()
        assert len(traj) == 1
        assert traj[0]["from"] == "PENDING"
        assert traj[0]["to"] == "PLANNING"
        assert traj[0]["success"] is True
        assert traj[0]["message"] == "go"
        assert "timestamp" in traj[0]

    def test_multiple_transitions(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.VALIDATION)
        traj = machine.get_trajectory()
        assert len(traj) == 2
        assert traj[1]["from"] == "PLANNING"
        assert traj[1]["to"] == "VALIDATION"

    def test_failed_transition_included(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.FAILED, message="boom")
        traj = machine.get_trajectory()
        # transition_to() does not accept success kwarg; defaults to True
        assert traj[1]["success"] is True
        assert traj[1]["message"] == "boom"


# =============================================================================
# check_stall
# =============================================================================


class TestCheckStall:
    def test_first_call_with_default_baseline(self, machine: TaskStateMachine) -> None:
        # PhaseContext defaults previous_* to [], so first call compares against empty
        assert machine.check_stall(["a"], ["b"]) is False
        # Context should be updated for next comparison
        assert machine.context.previous_missing_targets == ["a"]
        assert machine.context.previous_unresolved_imports == ["b"]

    def test_first_call_with_empty_lists(self, machine: TaskStateMachine) -> None:
        assert machine.check_stall([], []) is False
        assert machine.context.previous_missing_targets == []
        assert machine.context.previous_unresolved_imports == []

    def test_first_call_with_none_like_behavior(self, machine: TaskStateMachine) -> None:
        # previous_missing_targets defaults to [] (falsy), so first call
        assert machine.check_stall([], []) is False

    def test_improvement_not_stalled(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a", "b"], ["x", "y"])
        assert machine.check_stall(["a"], ["x", "y"]) is False
        assert machine.context.stall_count == 0

    def test_no_improvement_increments_stall_count(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a", "b"], ["x"])
        assert machine.context.stall_count == 1
        assert machine.check_stall(["a", "b"], ["x"]) is True  # stall_count=2

    def test_stall_threshold_reached(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a"], ["x"])
        assert machine.context.stall_count == 1
        assert machine.check_stall(["a"], ["x"]) is True  # stall_count=2, threshold

    def test_stall_count_resets_on_improvement(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a", "b"], ["x"])
        machine.check_stall(["a", "b"], ["x"])  # stall_count=1
        machine.check_stall(["a"], ["x"])  # improved, should reset
        assert machine.context.stall_count == 0

    def test_one_side_improves_not_stalled(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a", "b"], ["x", "y"])
        assert machine.check_stall(["a", "b"], ["x"]) is False
        assert machine.context.stall_count == 0

    def test_both_worsen_counts_as_stall(self, machine: TaskStateMachine) -> None:
        machine.check_stall(["a"], ["x"])
        assert machine.check_stall(["a", "b"], ["x", "y"]) is True  # stall_count=2
        assert machine.context.stall_count == 2


# =============================================================================
# should_retry
# =============================================================================


class TestShouldRetry:
    def test_not_in_verification(self, machine: TaskStateMachine) -> None:
        assert machine.should_retry() is False

    def test_no_result(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        assert machine.should_retry() is False

    def test_success_no_retry(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        result = PhaseResult(success=True, phase=TaskPhase.VERIFICATION)
        machine.record_phase_result(result)
        assert machine.should_retry() is False

    def test_failure_can_retry(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        result = PhaseResult(
            success=False,
            phase=TaskPhase.VERIFICATION,
            can_retry=True,
            context_updates={"missing_targets": ["a"], "unresolved_imports": []},
        )
        machine.record_phase_result(result)
        assert machine.should_retry() is True

    def test_failure_cannot_retry(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        result = PhaseResult(
            success=False,
            phase=TaskPhase.VERIFICATION,
            can_retry=False,
            context_updates={},
        )
        machine.record_phase_result(result)
        assert machine.should_retry() is False

    def test_exhausted_build_rounds(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        machine.context.build_round = machine.context.max_build_rounds
        result = PhaseResult(
            success=False,
            phase=TaskPhase.VERIFICATION,
            can_retry=True,
            context_updates={},
        )
        machine.record_phase_result(result)
        assert machine.should_retry() is False

    def test_stalled_prevents_retry(self, machine: TaskStateMachine) -> None:
        _advance_to_verification(machine)
        machine.context.build_round = 1
        # First stall baseline
        machine.check_stall(["a"], ["b"])
        machine.check_stall(["a"], ["b"])  # stall_count=1
        result = PhaseResult(
            success=False,
            phase=TaskPhase.VERIFICATION,
            can_retry=True,
            context_updates={"missing_targets": ["a"], "unresolved_imports": ["b"]},
        )
        machine.record_phase_result(result)
        # Now should_retry calls check_stall again which hits threshold
        assert machine.should_retry() is False


# =============================================================================
# to_dict
# =============================================================================


class TestToDict:
    def test_basic(self, machine: TaskStateMachine) -> None:
        d = machine.to_dict()
        assert d["task_id"] == "task-1"
        assert d["current_phase"] == "PENDING"
        assert d["is_terminal"] is False
        assert "context" in d
        assert "trajectory" in d

    def test_context_in_dict(self, machine: TaskStateMachine) -> None:
        d = machine.to_dict()
        ctx = d["context"]
        assert ctx["task_id"] == "task-1"
        assert ctx["workspace"] == "."
        assert ctx["build_round"] == 0
        assert ctx["stall_count"] == 0
        assert ctx["changed_files"] == []

    def test_after_transition(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        d = machine.to_dict()
        assert d["current_phase"] == "PLANNING"
        assert len(d["trajectory"]) == 1
        assert d["is_terminal"] is False

    def test_terminal_in_dict(self, machine: TaskStateMachine) -> None:
        machine.transition_to(TaskPhase.PLANNING)
        machine.transition_to(TaskPhase.FAILED)
        d = machine.to_dict()
        assert d["is_terminal"] is True


# =============================================================================
# Helpers
# =============================================================================


def _advance_to_verification(machine: TaskStateMachine) -> None:
    machine.transition_to(TaskPhase.PLANNING)
    machine.transition_to(TaskPhase.VALIDATION)
    machine.transition_to(TaskPhase.EXECUTION)
    machine.transition_to(TaskPhase.VERIFICATION)


def _advance_to_completed(machine: TaskStateMachine) -> None:
    _advance_to_verification(machine)
    machine.transition_to(TaskPhase.COMPLETED)
