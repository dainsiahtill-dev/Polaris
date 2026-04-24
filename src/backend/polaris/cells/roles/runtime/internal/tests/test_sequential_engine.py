"""Tests for sequential_engine module."""

from __future__ import annotations

import pytest

from polaris.cells.roles.runtime.internal.sequential_engine import (
    FailureClass,
    RetryHint,
    SeqEventType,
    SeqProgressDetector,
    SequentialBudget,
    SequentialStateProxy,
    SequentialStats,
    SeqState,
    StepDecision,
    StepResult,
    StepStatus,
    TerminationReason,
    create_sequential_budget,
    should_enable_sequential,
    RESERVED_KEYS,
    WRITE_TOOL_NAMES,
    DEFAULT_BUDGET_CONFIG,
)


class TestEnums:
    """Tests for termination and step status enums."""

    def test_termination_reason_values(self):
        assert TerminationReason.SEQ_COMPLETED.value == "seq_completed"
        assert TerminationReason.SEQ_NO_PROGRESS.value == "seq_no_progress"
        assert TerminationReason.SEQ_BUDGET_EXHAUSTED.value == "seq_budget_exhausted"
        assert (
            TerminationReason.SEQ_TOOL_FAIL_RECOVERABLE_EXHAUSTED.value
            == "seq_tool_fail_recoverable_exhausted"
        )
        assert TerminationReason.SEQ_OUTPUT_INVALID_EXHAUSTED.value == "seq_output_invalid_exhausted"
        assert TerminationReason.SEQ_RESERVED_KEY_VIOLATION.value == "seq_reserved_key_violation"
        assert TerminationReason.SEQ_CRASH_ORPHAN.value == "seq_crash_orphan"
        assert TerminationReason.SEQ_ERROR.value == "seq_error"

    def test_failure_class_values(self):
        assert FailureClass.SUCCESS.value == "success"
        assert FailureClass.RETRYABLE.value == "retryable"
        assert FailureClass.VALIDATION_FAIL.value == "validation_fail"
        assert FailureClass.INTERNAL_BUG.value == "internal_bug"
        assert FailureClass.UNKNOWN.value == "unknown"

    def test_retry_hint_values(self):
        assert RetryHint.HANDOFF.value == "handoff"
        assert RetryHint.STAGNATION.value == "stagnation"
        assert RetryHint.ESCALATE.value == "escalate"
        assert RetryHint.COOLDOWN_RETRY.value == "cooldown_retry"
        assert RetryHint.MANUAL_REVIEW.value == "manual_review"
        assert RetryHint.ALERT.value == "alert"
        assert RetryHint.AUDIT_RECOVER.value == "audit_recover"

    def test_step_status_values(self):
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.STARTED.value == "started"
        assert StepStatus.TOOL_INVOKED.value == "tool_invoked"
        assert StepStatus.TOOL_COMPLETED.value == "tool_completed"
        assert StepStatus.FINISHING.value == "finishing"
        assert StepStatus.FINISHED.value == "finished"


class TestSequentialBudget:
    """Tests for SequentialBudget dataclass."""

    def test_default_values(self):
        budget = SequentialBudget()
        assert budget.max_steps == 12
        assert budget.max_tool_calls_total == 24
        assert budget.max_no_progress_steps == 3
        assert budget.max_wall_time_seconds == 120
        assert budget.max_same_error_fingerprint == 2
        assert budget.progress_info_incremental is False
        assert budget.idempotency_check is True

    def test_custom_values(self):
        budget = SequentialBudget(
            max_steps=20,
            max_tool_calls_total=50,
            max_no_progress_steps=5,
            max_wall_time_seconds=300,
        )
        assert budget.max_steps == 20
        assert budget.max_tool_calls_total == 50
        assert budget.max_no_progress_steps == 5
        assert budget.max_wall_time_seconds == 300


class TestSequentialStats:
    """Tests for SequentialStats dataclass."""

    def test_default_values(self):
        stats = SequentialStats()
        assert stats.steps == 0
        assert stats.tool_calls == 0
        assert stats.no_progress == 0
        assert stats.termination_reason == ""
        assert stats.budget_exhausted is False
        assert stats.failure_class == ""
        assert stats.retry_hint == ""
        assert stats.error_fingerprints == {}
        assert stats.tool_outcomes == {}

    def test_with_values(self):
        stats = SequentialStats(
            steps=5,
            tool_calls=10,
            no_progress=1,
            termination_reason="seq_completed",
            budget_exhausted=False,
        )
        assert stats.steps == 5
        assert stats.tool_calls == 10


class TestSeqState:
    """Tests for SeqState dataclass."""

    def test_default_values(self):
        state = SeqState()
        assert state.seq_session_id == ""
        assert state.outer_attempt_id == ""
        assert state.step_index == 0
        assert state.tool_calls_count == 0
        assert state.no_progress_count == 0
        assert state.wall_time_elapsed == 0.0
        assert state.start_time is None
        assert state.status == "idle"
        assert state.steps == []
        assert state.tool_outcomes == {}
        assert state.error_fingerprints == {}
        assert state.last_error is None
        assert state.termination_reason is None

    def test_to_dict(self):
        state = SeqState(seq_session_id="sess-1", step_index=5, status="running")
        d = state.to_dict()
        assert d["seq_session_id"] == "sess-1"
        assert d["step_index"] == 5
        assert d["status"] == "running"


class TestStepDecision:
    """Tests for StepDecision dataclass."""

    def test_creation(self):
        decision = StepDecision(
            step_index=0,
            intent="analyze code",
            planned_actions=["read", "modify"],
            tool_plan=[{"tool": "read_file", "args": {}}],
        )
        assert decision.step_index == 0
        assert decision.intent == "analyze code"
        assert len(decision.planned_actions) == 2


class TestStepResult:
    """Tests for StepResult dataclass."""

    def test_creation(self):
        result = StepResult(
            step_index=1,
            status=StepStatus.FINISHED,
            tool_result={"tool": "read_file", "success": True},
            progress_detected=True,
        )
        assert result.step_index == 1
        assert result.status == StepStatus.FINISHED
        assert result.progress_detected is True


class TestConstants:
    """Tests for module constants."""

    def test_reserved_keys_contains_expected(self):
        assert "phase" in RESERVED_KEYS
        assert "status" in RESERVED_KEYS
        assert "retry_count" in RESERVED_KEYS
        assert "max_retries" in RESERVED_KEYS
        assert "completed_phases" in RESERVED_KEYS
        assert "workflow_state" in RESERVED_KEYS
        assert "task_id" in RESERVED_KEYS
        assert "run_id" in RESERVED_KEYS

    def test_default_budget_config(self):
        assert DEFAULT_BUDGET_CONFIG["max_steps"] == 12
        assert DEFAULT_BUDGET_CONFIG["max_tool_calls_total"] == 24
        assert DEFAULT_BUDGET_CONFIG["max_no_progress_steps"] == 3
        assert DEFAULT_BUDGET_CONFIG["max_wall_time_seconds"] == 120

    def test_write_tool_names(self):
        assert "write_file" in WRITE_TOOL_NAMES
        assert "search_replace" in WRITE_TOOL_NAMES
        assert "edit_file" in WRITE_TOOL_NAMES


class TestSequentialStateProxy:
    """Tests for SequentialStateProxy write protection."""

    def test_write_allowed_key(self):
        state = SeqState()
        proxy = SequentialStateProxy(state, emit_violation=False, fail_fast=False)
        proxy.write("custom_field", "value")
        assert getattr(state, "custom_field", None) == "value"

    def test_write_reserved_key_no_fail_fast(self):
        state = SeqState()
        proxy = SequentialStateProxy(state, emit_violation=False, fail_fast=False)
        proxy.write("phase", "testing")
        # Should not raise, key just blocked
        assert state.phase != "testing"

    def test_write_reserved_key_fail_fast(self):
        state = SeqState()
        proxy = SequentialStateProxy(state, emit_violation=False, fail_fast=True)
        with pytest.raises(Exception):
            proxy.write("status", "running")

    def test_get_state(self):
        state = SeqState(step_index=3)
        proxy = SequentialStateProxy(state)
        retrieved = proxy.get_state()
        assert retrieved.step_index == 3

    def test_set_context(self):
        state = SeqState()
        proxy = SequentialStateProxy(state)
        proxy.set_context(run_id="run-1", role="director", task_id="task-1")
        assert proxy._run_id == "run-1"
        assert proxy._role == "director"
        assert proxy._task_id == "task-1"


class TestSeqProgressDetector:
    """Tests for SeqProgressDetector."""

    def test_default_initialization(self):
        detector = SeqProgressDetector()
        assert detector.progress_info_incremental is False
        assert detector.max_no_progress_steps == 3

    def test_detect_progress_no_tool_result(self):
        detector = SeqProgressDetector()
        decision = StepDecision(step_index=0, intent="test", planned_actions=[])
        state = SeqState()

        progress, signals = detector.detect_progress(None, decision, state)
        assert progress is False
        assert signals == []

    def test_detect_progress_with_write_tool_success(self):
        detector = SeqProgressDetector()
        decision = StepDecision(step_index=0, intent="write", planned_actions=[])
        state = SeqState()

        tool_result = {"tool": "write_file", "success": True, "changed_files_count": 1}
        progress, signals = detector.detect_progress(tool_result, decision, state)
        assert progress is True
        assert "artifact_progress" in signals

    def test_detect_progress_with_failed_write_tool(self):
        detector = SeqProgressDetector()
        decision = StepDecision(step_index=0, intent="write", planned_actions=[])
        state = SeqState()

        tool_result = {"tool": "write_file", "success": False}
        progress, signals = detector.detect_progress(tool_result, decision, state)
        assert progress is False

    def test_detect_progress_with_tests_passed_delta(self):
        detector = SeqProgressDetector()
        decision = StepDecision(step_index=0, intent="test", planned_actions=[])
        state = SeqState()

        tool_result = {"tool": "pytest", "tests_passed_delta": 3}
        progress, signals = detector.detect_progress(tool_result, decision, state)
        assert progress is True
        assert "validation_progress" in signals

    def test_detect_progress_with_blocker_in_actions(self):
        detector = SeqProgressDetector()
        decision = StepDecision(
            step_index=0, intent="analyze", planned_actions=["identify blocker: file missing"]
        )
        state = SeqState()

        progress, signals = detector.detect_progress(None, decision, state)
        assert progress is True
        assert "blocker_clarified" in signals

    def test_detect_progress_info_incremental(self):
        detector = SeqProgressDetector(progress_info_incremental=True)
        decision = StepDecision(
            step_index=0,
            intent="search",
            planned_actions=[],
            expected_progress_signal=["dependency_found"],
        )
        state = SeqState()

        progress, signals = detector.detect_progress(None, decision, state)
        assert progress is True
        assert "info_incremental" in signals


class TestCreateSequentialBudget:
    """Tests for create_sequential_budget factory function."""

    def test_default_values(self):
        budget = create_sequential_budget()
        assert budget.max_steps == 12
        assert budget.max_tool_calls_total == 24

    def test_custom_max_steps(self):
        budget = create_sequential_budget(max_steps=30)
        assert budget.max_steps == 30

    def test_partial_override(self):
        budget = create_sequential_budget(max_no_progress_steps=5)
        assert budget.max_no_progress_steps == 5
        assert budget.max_steps == 12  # Default preserved

    def test_all_overrides(self):
        budget = create_sequential_budget(
            max_steps=20,
            max_tool_calls_total=40,
            max_no_progress_steps=5,
            max_wall_time_seconds=300,
            progress_info_incremental=True,
            idempotency_check=False,
        )
        assert budget.max_steps == 20
        assert budget.max_tool_calls_total == 40
        assert budget.max_no_progress_steps == 5
        assert budget.max_wall_time_seconds == 300
        assert budget.progress_info_incremental is True
        assert budget.idempotency_check is False


class TestShouldEnableSequential:
    """Tests for should_enable_sequential utility function."""

    def test_director_enabled_by_default(self):
        assert should_enable_sequential("director") is True
        assert should_enable_sequential("Director") is True
        assert should_enable_sequential("DIRECTOR") is True

    def test_adaptive_enabled_by_default(self):
        assert should_enable_sequential("adaptive") is True

    def test_pm_not_enabled_by_default(self):
        assert should_enable_sequential("pm") is False
        assert should_enable_sequential("PM") is False

    def test_architect_not_enabled_by_default(self):
        assert should_enable_sequential("architect") is False

    def test_custom_enabled_roles(self):
        assert should_enable_sequential("pm", enabled_roles=["pm", "qa"]) is True
        assert should_enable_sequential("qa", enabled_roles=["pm", "qa"]) is True
        assert should_enable_sequential("director", enabled_roles=["pm", "qa"]) is False

    def test_case_insensitive(self):
        assert should_enable_sequential("DIRECTOR") is True
        assert should_enable_sequential("Director") is True


class TestSeqEventType:
    """Tests for SeqEventType constants."""

    def test_event_type_values(self):
        assert SeqEventType.START == "seq.start"
        assert SeqEventType.STEP == "seq.step"
        assert SeqEventType.PROGRESS == "seq.progress"
        assert SeqEventType.NO_PROGRESS == "seq.no_progress"
        assert SeqEventType.TERMINATION == "seq.termination"
        assert SeqEventType.RESERVED_KEY_VIOLATION == "seq.reserved_key_violation"
        assert SeqEventType.ERROR == "seq.error"