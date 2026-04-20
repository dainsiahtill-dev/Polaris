"""Tests for EventSequenceValidator in turn_events.py.

EventSequenceValidator is the offline audit tool for TurnEvent temporal ordering.
It validates that a sequence of TurnPhaseEvents follows one of the predefined
legal patterns. This is critical for observability contract compliance.
"""

from polaris.cells.roles.kernel.public.turn_events import (
    EventSequenceValidator,
    TurnPhaseEvent,
)


class TestEventSequenceValidatorValidSequences:
    """Validate known-good event sequences."""

    def test_direct_answer_sequence(self) -> None:
        """decision_requested -> decision_completed -> completed."""
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "completed"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True
        assert validator.get_violations() == []

    def test_tool_batch_then_none_sequence(self) -> None:
        """decision -> tool_batch_started -> tool_batch_completed -> completed."""
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "completed",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True

    def test_tool_batch_then_llm_once_sequence(self) -> None:
        """Full sequence with finalization."""
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
            "finalization_completed",
            "completed",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True

    def test_workflow_handoff_after_decision(self) -> None:
        """decision -> workflow_handoff (no tools)."""
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "workflow_handoff"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True

    def test_workflow_handoff_after_tools(self) -> None:
        """decision -> tools -> workflow_handoff."""
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "workflow_handoff",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True


class TestEventSequenceValidatorPrefixMatching:
    """Validator must accept valid prefixes (incomplete turns)."""

    def test_prefix_of_direct_answer(self) -> None:
        validator = EventSequenceValidator()
        validator.add(TurnPhaseEvent.create(turn_id="t1", phase="decision_requested"))
        assert validator.is_valid() is True

    def test_prefix_of_tool_sequence(self) -> None:
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "tool_batch_started"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True

    def test_prefix_before_finalization(self) -> None:
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is True


class TestEventSequenceValidatorInvalidSequences:
    """Validate known-bad event sequences are rejected."""

    def test_completed_before_tool_batch_started(self) -> None:
        """completed cannot appear before tool_batch_started in a tool sequence."""
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "completed"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        # Now append tool_batch_started which makes it invalid
        validator.add(TurnPhaseEvent.create(turn_id="t1", phase="tool_batch_started"))
        assert validator.is_valid() is False

    def test_tool_batch_completed_without_started(self) -> None:
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "tool_batch_completed"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is False

    def test_finalization_completed_without_requested(self) -> None:
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_completed",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is False

    def test_duplicate_decision_requested(self) -> None:
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "decision_requested"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is False
        violations = validator.get_violations()
        assert any("Duplicate decision_requested" in v for v in violations)

    def test_duplicate_finalization_requested(self) -> None:
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
            "finalization_completed",
            "finalization_requested",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is False
        violations = validator.get_violations()
        assert any("Duplicate finalization_requested" in v for v in violations)

    def test_garbage_sequence(self) -> None:
        validator = EventSequenceValidator()
        for phase in ("tool_batch_started", "decision_requested", "completed"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        assert validator.is_valid() is False


class TestEventSequenceValidatorGetViolations:
    """Test the violation reporting granularity."""

    def test_empty_validator_no_violations(self) -> None:
        validator = EventSequenceValidator()
        assert validator.get_violations() == []

    def test_tool_completed_without_started_violation(self) -> None:
        validator = EventSequenceValidator()
        for phase in ("decision_requested", "decision_completed", "tool_batch_completed"):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        violations = validator.get_violations()
        assert any("tool_batch_completed without tool_batch_started" in v for v in violations)

    def test_finalization_completed_without_requested_violation(self) -> None:
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "decision_completed",
            "finalization_completed",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        violations = validator.get_violations()
        assert any("finalization_completed without finalization_requested" in v for v in violations)

    def test_multiple_violations_accumulated(self) -> None:
        validator = EventSequenceValidator()
        for phase in (
            "decision_requested",
            "tool_batch_completed",
            "finalization_completed",
        ):
            validator.add(TurnPhaseEvent.create(turn_id="t1", phase=phase))
        violations = validator.get_violations()
        assert len(violations) >= 2
