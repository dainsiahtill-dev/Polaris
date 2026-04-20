"""Tests for Sequential Engine

Tests the vNext sequential thinking kernel implementation.
"""

import pytest
from polaris.cells.roles.runtime.internal.sequential_engine import (
    RESERVED_KEYS,
    FailureClass,
    ReservedKeyViolationError,
    RetryHint,
    SeqEventType,
    SeqProgressDetector,
    SeqState,
    SequentialBudget,
    SequentialEngine,
    SequentialStateProxy,
    SequentialStats,
    StepDecision,
    StepResult,
    StepStatus,
    TerminationReason,
    create_sequential_budget,
    emit_seq_event,
    get_seq_emitter,
    should_enable_sequential,
)


class TestSequentialBudget:
    """Test SequentialBudget creation and configuration."""

    def test_default_budget(self):
        """Test default budget values."""
        budget = SequentialBudget()
        assert budget.max_steps == 12
        assert budget.max_tool_calls_total == 24
        assert budget.max_no_progress_steps == 3
        assert budget.max_wall_time_seconds == 120

    def test_custom_budget(self):
        """Test custom budget values."""
        budget = create_sequential_budget(
            max_steps=6,
            max_tool_calls_total=12,
            max_no_progress_steps=2,
        )
        assert budget.max_steps == 6
        assert budget.max_tool_calls_total == 12
        assert budget.max_no_progress_steps == 2


class TestSequentialStateProxy:
    """Test SequentialStateProxy write protection."""

    def test_normal_write(self):
        """Test normal state writes."""
        state = SeqState()
        proxy = SequentialStateProxy(state, fail_fast=True)

        proxy.write("step_index", 5)
        assert state.step_index == 5

    def test_reserved_key_blocked(self):
        """Test that reserved keys are blocked."""
        state = SeqState()
        proxy = SequentialStateProxy(state, fail_fast=True)

        # phase is a reserved key
        with pytest.raises(ReservedKeyViolationError):
            proxy.write("phase", "implement")

        # status is a reserved key
        with pytest.raises(ReservedKeyViolationError):
            proxy.write("status", "running")

    def test_reserved_key_violation_no_fail_fast(self):
        """Test reserved key handling when fail_fast is False."""
        state = SeqState()
        proxy = SequentialStateProxy(state, fail_fast=False, emit_violation=False)

        # Should not raise, but should not write either
        proxy.write("phase", "implement")

        # phase is not in RESERVED_KEYS but workflow_state is
        # Let's check that reserved key is blocked - the state should remain unchanged
        # Since SeqState doesn't have 'phase' attribute, it won't be written
        assert not hasattr(state, 'phase') or state.phase is None


class TestSeqProgressDetector:
    """Test progress detection."""

    def test_artifact_progress_write_tool(self):
        """Test Type-A: Artifact progress with write tool."""
        detector = SeqProgressDetector()

        tool_result = {
            "tool": "write_file",
            "success": True,
            "file_path": "/test.py",
        }

        decision = StepDecision(step_index=0, intent="write")
        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        assert progress is True
        assert "artifact_progress" in signals

    def test_no_progress(self):
        """Test when no progress is detected."""
        detector = SeqProgressDetector()

        tool_result = {"tool": "read_file", "success": True}
        decision = StepDecision(step_index=0, intent="read")

        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        assert progress is False
        assert len(signals) == 0


class TestTerminationMapping:
    """Test termination reason to action mapping."""

    def test_completed_mapping(self):
        """Test SEQ_COMPLETED maps to HANDOFF."""
        budget = SequentialBudget()
        engine = SequentialEngine(workspace="/test", budget=budget)

        # Initialize state
        engine._state = SeqState()
        engine._state.termination_reason = TerminationReason.SEQ_COMPLETED.value

        failure_class, retry_hint = engine._map_termination_to_action(
            TerminationReason.SEQ_COMPLETED.value
        )

        assert failure_class == FailureClass.SUCCESS
        assert retry_hint == RetryHint.HANDOFF

    def test_no_progress_mapping(self):
        """Test SEQ_NO_PROGRESS maps to STAGNATION."""
        failure_class, retry_hint = SequentialEngine(
            workspace="/test"
        )._map_termination_to_action(TerminationReason.SEQ_NO_PROGRESS.value)

        assert failure_class == FailureClass.RETRYABLE
        assert retry_hint == RetryHint.STAGNATION

    def test_budget_exhausted_mapping(self):
        """Test SEQ_BUDGET_EXHAUSTED maps to ESCALATE."""
        failure_class, retry_hint = SequentialEngine(
            workspace="/test"
        )._map_termination_to_action(TerminationReason.SEQ_BUDGET_EXHAUSTED.value)

        assert failure_class == FailureClass.RETRYABLE
        assert retry_hint == RetryHint.ESCALATE


class TestShouldEnableSequential:
    """Test role-based sequential enablement."""

    def test_director_enabled(self):
        """Test director is enabled by default."""
        assert should_enable_sequential("director") is True

    def test_adaptive_enabled(self):
        """Test adaptive is enabled by default."""
        assert should_enable_sequential("adaptive") is True

    def test_pm_disabled(self):
        """Test pm is disabled by default."""
        assert should_enable_sequential("pm") is False

    def test_custom_enabled_roles(self):
        """Test custom enabled roles."""
        assert should_enable_sequential("pm", enabled_roles=["pm", "qa"]) is True
        assert should_enable_sequential("qa", enabled_roles=["pm", "qa"]) is True
        assert should_enable_sequential("architect", enabled_roles=["pm", "qa"]) is False


class TestSeqEventEmitter:
    """Test sequential event emission."""

    def test_emit_and_get_events(self):
        """Test emitting and retrieving events."""
        emitter = get_seq_emitter()
        emitter._event_history.clear()  # Clear history

        emit_seq_event(
            event_type=SeqEventType.START,
            run_id="run-123",
            role="director",
            task_id="task-456",
            step_index=0,
            payload={"budget": {"max_steps": 12}},
        )

        events = emitter.get_events(run_id="run-123")
        assert len(events) == 1
        assert events[0].event_type == SeqEventType.START
        assert events[0].role == "director"


class TestSequentialEngine:
    """Test SequentialEngine core functionality."""

    def test_engine_initialization(self):
        """Test engine initializes correctly."""
        engine = SequentialEngine(workspace="/test")

        assert engine.workspace == "/test"
        assert engine.budget.max_steps == 12
        assert engine._state.status == "idle"

    def test_engine_with_custom_budget(self):
        """Test engine with custom budget."""
        budget = create_sequential_budget(max_steps=6)
        engine = SequentialEngine(workspace="/test", budget=budget)

        assert engine.budget.max_steps == 6

    def test_set_context(self):
        """Test setting execution context."""
        engine = SequentialEngine(workspace="/test")
        engine.set_context(role="director", run_id="run-123", task_id="task-456")

        assert engine._current_role == "director"
        assert engine._current_run_id == "run-123"
        assert engine._current_task_id == "task-456"

    @pytest.mark.asyncio
    async def test_execute_initializes_session(self):
        """Test that execute initializes session correctly."""
        engine = SequentialEngine(workspace="/test")

        # Execute with minimal setup
        await engine.execute(
            initial_message="Test message",
            profile=None,  # No profile, will skip LLM calls
        )

        # Check state was initialized
        assert engine._state.seq_session_id != ""
        assert engine._state.status in ("completed", "running")

    def test_get_state(self):
        """Test getting current state."""
        engine = SequentialEngine(workspace="/test")
        engine._state.step_index = 5
        engine._state.tool_calls_count = 10

        state = engine.get_state()

        assert state.step_index == 5
        assert state.tool_calls_count == 10


class TestStepDecisionAndResult:
    """Test step decision and result structures."""

    def test_step_decision_creation(self):
        """Test creating a step decision."""
        decision = StepDecision(
            step_index=0,
            intent="Implement feature X",
            planned_actions=["write_file", "test"],
            tool_plan=[{"tool": "write_file", "args": {}}],
            expected_progress_signal=["artifact_progress"],
            risk_flags=["file_exists"],
        )

        assert decision.step_index == 0
        assert decision.intent == "Implement feature X"
        assert len(decision.planned_actions) == 2

    def test_step_result_creation(self):
        """Test creating a step result."""
        result = StepResult(
            step_index=0,
            status=StepStatus.FINISHED,
            progress_detected=True,
            tool_result={"success": True},
        )

        assert result.step_index == 0
        assert result.status == StepStatus.FINISHED
        assert result.progress_detected is True


class TestSequentialStats:
    """Test sequential stats structure."""

    def test_stats_creation(self):
        """Test creating sequential stats."""
        stats = SequentialStats(
            steps=5,
            tool_calls=10,
            no_progress=0,
            termination_reason="seq_completed",
            budget_exhausted=False,
            failure_class="success",
            retry_hint="handoff",
        )

        assert stats.steps == 5
        assert stats.tool_calls == 10
        assert stats.termination_reason == "seq_completed"
        assert stats.failure_class == "success"
        assert stats.retry_hint == "handoff"


class TestReservedKeys:
    """Test reserved keys definition."""

    def test_reserved_keys_include_workflow_fields(self):
        """Verify reserved keys include workflow fields."""
        assert "phase" in RESERVED_KEYS
        assert "status" in RESERVED_KEYS
        assert "retry_count" in RESERVED_KEYS
        assert "max_retries" in RESERVED_KEYS
        assert "completed_phases" in RESERVED_KEYS
        assert "workflow_state" in RESERVED_KEYS


class TestProgressDetectionEdgeCases:
    """Test progress detection edge cases."""

    def test_validation_progress(self):
        """Test Type-B: Validation progress detection."""
        detector = SeqProgressDetector()

        tool_result = {
            "tests_passed_delta": 5,
        }
        decision = StepDecision(step_index=0, intent="test")

        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        assert progress is True
        assert "validation_progress" in signals

    def test_blocker_clarification(self):
        """Test Type-C: Blocker clarification detection."""
        detector = SeqProgressDetector()

        tool_result = {"success": True}
        decision = StepDecision(
            step_index=0,
            intent="identify blocker",
            planned_actions=["blocker: missing dependency"],
        )

        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        assert progress is True
        assert "blocker_clarified" in signals

    def test_info_incremental_disabled_by_default(self):
        """Test that Type-D is disabled by default."""
        detector = SeqProgressDetector(progress_info_incremental=False)

        tool_result = {"success": True}
        decision = StepDecision(
            step_index=0,
            intent="discover info",
            expected_progress_signal=["dependency_found"],
        )

        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        # Should not detect progress when Type-D is disabled
        assert progress is False

    def test_info_incremental_enabled(self):
        """Test that Type-D works when enabled."""
        detector = SeqProgressDetector(progress_info_incremental=True)

        tool_result = {"success": True}
        decision = StepDecision(
            step_index=0,
            intent="discover info",
            expected_progress_signal=["dependency_found"],
        )

        progress, signals = detector.detect_progress(tool_result, decision, SeqState())

        assert progress is True
        assert "info_incremental" in signals


class TestTerminationMappingComplete:
    """Test all termination reason mappings."""

    @pytest.mark.parametrize("reason,expected_class,expected_hint", [
        ("seq_completed", "success", "handoff"),
        ("seq_no_progress", "retryable", "stagnation"),
        ("seq_budget_exhausted", "retryable", "escalate"),
        ("seq_tool_fail_recoverable_exhausted", "retryable", "cooldown_retry"),
        ("seq_output_invalid_exhausted", "validation_fail", "manual_review"),
        ("seq_reserved_key_violation", "internal_bug", "alert"),
        ("seq_crash_orphan", "unknown", "audit_recover"),
        ("seq_error", "unknown", "escalate"),
    ])
    def test_all_termination_mappings(self, reason, expected_class, expected_hint):
        """Test all termination reason mappings."""
        failure_class, retry_hint = SequentialEngine(
            workspace="/test"
        )._map_termination_to_action(reason)

        assert failure_class.value == expected_class
        assert retry_hint.value == expected_hint


class TestSeqStatePersistence:
    """Test SeqState serialization."""

    def test_state_to_dict(self):
        """Test state can be serialized to dict."""
        state = SeqState(
            seq_session_id="test-123",
            step_index=5,
            tool_calls_count=10,
            status="running",
        )

        state_dict = state.to_dict()

        assert state_dict["seq_session_id"] == "test-123"
        assert state_dict["step_index"] == 5
        assert state_dict["tool_calls_count"] == 10
        assert state_dict["status"] == "running"
