"""Tests for Cognitive Event Types.

This module tests the serialization, deserialization, and emission
of the 11 cognitive event types defined in schemas.py.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from polaris.kernelone.events.typed import (
    BeliefChangeEvent,
    CautiousExecutionEvent,
    ConfidenceCalibrationEvent,
    CriticalThinkingEvent,
    EvolutionEvent,
    IntentDetectedEvent,
    PerceptionCompletedEvent,
    ReasoningCompletedEvent,
    ReflectionEvent,
    ThinkingPhaseEvent,
    ValueAlignmentEvent,
    emit_event,
    subscribe,
)
from polaris.kernelone.events.typed.registry import (
    EventRegistry,
    reset_default_registry,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_registry() -> Generator[None, None, None]:
    """Reset the default registry before each test."""
    reset_default_registry()
    yield
    reset_default_registry()


@pytest.fixture
def registry() -> EventRegistry:
    """Create a fresh EventRegistry for testing."""
    return EventRegistry()


# =============================================================================
# Event Creation Tests
# =============================================================================


class TestThinkingPhaseEvent:
    """Tests for ThinkingPhaseEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = ThinkingPhaseEvent.create(
            phase="analysis",
            content="Analyzing the request",
            confidence=0.8,
            intent_type="create_file",
            run_id="run_123",
            workspace="/test",
        )

        assert event.event_name == "thinking_phase"
        assert event.payload.phase == "analysis"
        assert event.payload.content == "Analyzing the request"
        assert event.payload.confidence == 0.8
        assert event.payload.intent_type == "create_file"
        assert event.run_id == "run_123"
        assert event.workspace == "/test"

    def test_serialization(self) -> None:
        """Test event can be serialized to JSON."""
        event = ThinkingPhaseEvent.create(
            phase="synthesis",
            content="Synthesizing solution",
            confidence=0.9,
            intent_type="execute_command",
        )

        json_data = event.model_dump_json()
        assert "thinking_phase" in json_data
        assert "synthesis" in json_data

    def test_deserialization(self) -> None:
        """Test event can be deserialized from JSON."""
        event = ThinkingPhaseEvent.create(
            phase="evaluation",
            content="Evaluating options",
            confidence=0.7,
            intent_type="test",
        )

        json_data = event.model_dump_json()
        restored = ThinkingPhaseEvent.model_validate_json(json_data)

        assert restored.event_name == event.event_name
        assert restored.payload.phase == event.payload.phase
        assert restored.payload.confidence == event.payload.confidence


class TestReflectionEvent:
    """Tests for ReflectionEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = ReflectionEvent.create(
            reflection_type="meta_cognition",
            insights=["Pattern identified: file creation", "Risk detected: data loss"],
            knowledge_gaps=["Missing: user preferences", "Unknown: system constraints"],
            patterns_identified=["file_creation_pattern", "error_recovery_pattern"],
            run_id="run_456",
            workspace="/test",
        )

        assert event.event_name == "reflection"
        assert event.payload.reflection_type == "meta_cognition"
        assert len(event.payload.insights) == 2
        assert len(event.payload.knowledge_gaps) == 2
        assert len(event.payload.patterns_identified) == 2

    def test_empty_lists(self) -> None:
        """Test event with empty lists."""
        event = ReflectionEvent.create(
            reflection_type="post_mortem",
        )

        assert event.payload.insights == []
        assert event.payload.knowledge_gaps == []
        assert event.payload.patterns_identified == []


class TestEvolutionEvent:
    """Tests for EvolutionEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = EvolutionEvent.create(
            trigger_type="self_reflection",
            adaptation="Improved error handling",
            learning_recorded=True,
            run_id="run_789",
            workspace="/test",
        )

        assert event.event_name == "evolution"
        assert event.payload.trigger_type == "self_reflection"
        assert event.payload.adaptation == "Improved error handling"
        assert event.payload.learning_recorded is True


class TestBeliefChangeEvent:
    """Tests for BeliefChangeEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = BeliefChangeEvent.create(
            belief_key="user_prefers_explicit_confirmations",
            old_value=0.3,
            new_value=0.8,
            reason="User explicitly requested confirmations for delete operations",
            run_id="run_101",
            workspace="/test",
        )

        assert event.event_name == "belief_change"
        assert event.payload.belief_key == "user_prefers_explicit_confirmations"
        assert event.payload.old_value == 0.3
        assert event.payload.new_value == 0.8
        assert "delete operations" in event.payload.reason


class TestConfidenceCalibrationEvent:
    """Tests for ConfidenceCalibrationEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = ConfidenceCalibrationEvent.create(
            original_confidence=0.95,
            calibrated_confidence=0.7,
            calibration_factor=0.74,
            run_id="run_102",
            workspace="/test",
        )

        assert event.event_name == "confidence_calibration"
        assert event.payload.original_confidence == 0.95
        assert event.payload.calibrated_confidence == 0.7
        assert event.payload.calibration_factor == 0.74


class TestPerceptionCompletedEvent:
    """Tests for PerceptionCompletedEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = PerceptionCompletedEvent.create(
            intent_type="create_file",
            confidence=0.85,
            uncertainty_score=0.15,
            run_id="run_103",
            workspace="/test",
        )

        assert event.event_name == "perception_completed"
        assert event.payload.intent_type == "create_file"
        assert event.payload.confidence == 0.85
        assert event.payload.uncertainty_score == 0.15


class TestReasoningCompletedEvent:
    """Tests for ReasoningCompletedEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = ReasoningCompletedEvent.create(
            reasoning_type="six_questions",
            conclusion="Safe to proceed with file creation",
            blockers=[],
            run_id="run_104",
            workspace="/test",
        )

        assert event.event_name == "reasoning_completed"
        assert event.payload.reasoning_type == "six_questions"
        assert "file creation" in event.payload.conclusion
        assert event.payload.blockers == []

    def test_with_blockers(self) -> None:
        """Test event with identified blockers."""
        event = ReasoningCompletedEvent.create(
            reasoning_type="risk_analysis",
            conclusion="Proceed with caution",
            blockers=["No backup available", "Production environment"],
        )

        assert len(event.payload.blockers) == 2


class TestIntentDetectedEvent:
    """Tests for IntentDetectedEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = IntentDetectedEvent.create(
            intent_type="modify_file",
            surface_intent="edit_config",
            confidence=0.92,
            run_id="run_105",
            workspace="/test",
        )

        assert event.event_name == "intent_detected"
        assert event.payload.intent_type == "modify_file"
        assert event.payload.surface_intent == "edit_config"
        assert event.payload.confidence == 0.92


class TestCriticalThinkingEvent:
    """Tests for CriticalThinkingEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = CriticalThinkingEvent.create(
            analysis_type="risk_assessment",
            findings=["Low risk: read-only operation", "No data loss potential"],
            risk_level="low",
            run_id="run_106",
            workspace="/test",
        )

        assert event.event_name == "critical_thinking"
        assert event.payload.analysis_type == "risk_assessment"
        assert len(event.payload.findings) == 2
        assert event.payload.risk_level == "low"

    def test_high_risk(self) -> None:
        """Test event with high risk level."""
        event = CriticalThinkingEvent.create(
            analysis_type="impact_analysis",
            findings=["High impact: production data", "Irreversible operation"],
            risk_level="high",
        )

        assert event.payload.risk_level == "high"


class TestCautiousExecutionEvent:
    """Tests for CautiousExecutionEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = CautiousExecutionEvent.create(
            execution_path="SAFE_MODE",
            requires_confirmation=True,
            stakes_level="high",
            run_id="run_107",
            workspace="/test",
        )

        assert event.event_name == "cautious_execution"
        assert event.payload.execution_path == "SAFE_MODE"
        assert event.payload.requires_confirmation is True
        assert event.payload.stakes_level == "high"


class TestValueAlignmentEvent:
    """Tests for ValueAlignmentEvent."""

    def test_create(self) -> None:
        """Test factory method creates valid event."""
        event = ValueAlignmentEvent.create(
            action="Delete production database",
            verdict="REJECTED",
            conflicts=["Safety: potential data loss", "Ethics: irreversible action"],
            overall_score=0.2,
            run_id="run_108",
            workspace="/test",
        )

        assert event.event_name == "value_alignment"
        assert "database" in event.payload.action
        assert event.payload.verdict == "REJECTED"
        assert len(event.payload.conflicts) == 2
        assert event.payload.overall_score == 0.2

    def test_approved_verdict(self) -> None:
        """Test event with APPROVED verdict."""
        event = ValueAlignmentEvent.create(
            action="Read configuration file",
            verdict="APPROVED",
            conflicts=[],
            overall_score=0.95,
        )

        assert event.payload.verdict == "APPROVED"
        assert event.payload.conflicts == []


# =============================================================================
# Event Emission and Subscription Tests
# =============================================================================


class TestCognitiveEventEmission:
    """Tests for cognitive event emission and subscription."""

    @pytest.mark.asyncio
    async def test_emit_thinking_phase_event(self, registry: EventRegistry) -> None:
        """Test emitting and receiving a ThinkingPhaseEvent."""
        received_events: list[ThinkingPhaseEvent] = []

        async def handler(event: ThinkingPhaseEvent) -> None:
            received_events.append(event)

        registry.subscribe("thinking_phase", handler)  # type: ignore[arg-type] # narrow handler type for test

        event = ThinkingPhaseEvent.create(
            phase="analysis",
            content="Analyzing request",
            confidence=0.8,
        )
        await registry.emit(event)

        assert len(received_events) == 1
        assert received_events[0].payload.phase == "analysis"

    @pytest.mark.asyncio
    async def test_emit_reflection_event(self, registry: EventRegistry) -> None:
        """Test emitting and receiving a ReflectionEvent."""
        received_events: list[ReflectionEvent] = []

        async def handler(event: ReflectionEvent) -> None:
            received_events.append(event)

        registry.subscribe("reflection", handler)  # type: ignore[arg-type] # narrow handler type for test

        event = ReflectionEvent.create(
            reflection_type="meta_cognition",
            insights=["Pattern: file operations"],
        )
        await registry.emit(event)

        assert len(received_events) == 1
        assert "file operations" in received_events[0].payload.insights[0]

    @pytest.mark.asyncio
    async def test_emit_all_cognitive_events(self, registry: EventRegistry) -> None:
        """Test emitting all 11 cognitive event types."""
        received_count = 0

        async def count_handler(event: object) -> None:
            nonlocal received_count
            received_count += 1

        # Subscribe to all cognitive events using wildcard
        registry.subscribe("cognitive.*", count_handler)  # type: ignore[arg-type]
        registry.subscribe("thinking_phase", count_handler)  # type: ignore[arg-type]
        registry.subscribe("reflection", count_handler)  # type: ignore[arg-type]
        registry.subscribe("evolution", count_handler)  # type: ignore[arg-type]
        registry.subscribe("belief_change", count_handler)  # type: ignore[arg-type]
        registry.subscribe("confidence_calibration", count_handler)  # type: ignore[arg-type]
        registry.subscribe("perception_completed", count_handler)  # type: ignore[arg-type]
        registry.subscribe("reasoning_completed", count_handler)  # type: ignore[arg-type]
        registry.subscribe("intent_detected", count_handler)  # type: ignore[arg-type]
        registry.subscribe("critical_thinking", count_handler)  # type: ignore[arg-type]
        registry.subscribe("cautious_execution", count_handler)  # type: ignore[arg-type]
        registry.subscribe("value_alignment", count_handler)  # type: ignore[arg-type]

        # Emit all 11 cognitive event types
        events = [
            ThinkingPhaseEvent.create(phase="test"),
            ReflectionEvent.create(reflection_type="test"),
            EvolutionEvent.create(trigger_type="test"),
            BeliefChangeEvent.create(belief_key="test", old_value=0.0, new_value=1.0),
            ConfidenceCalibrationEvent.create(original_confidence=0.9, calibrated_confidence=0.7),
            PerceptionCompletedEvent.create(intent_type="test", confidence=0.8),
            ReasoningCompletedEvent.create(reasoning_type="test"),
            IntentDetectedEvent.create(intent_type="test", confidence=0.9),
            CriticalThinkingEvent.create(analysis_type="test"),
            CautiousExecutionEvent.create(execution_path="test"),
            ValueAlignmentEvent.create(action="test", verdict="APPROVED"),
        ]

        for event in events:
            await registry.emit(event)

        # Each event type should be received once
        # Note: wildcard subscription to "cognitive.*" won't match these exact names
        # so we subscribe to exact names as well
        assert received_count == 11

    @pytest.mark.asyncio
    async def test_emit_via_default_registry(self) -> None:
        """Test emitting via the default global registry."""
        received_events: list[ThinkingPhaseEvent] = []

        async def handler(event: ThinkingPhaseEvent) -> None:
            received_events.append(event)

        subscribe("thinking_phase", handler)  # type: ignore[arg-type] # narrow handler type for test

        event = ThinkingPhaseEvent.create(phase="default_registry_test")
        await emit_event(event)

        assert len(received_events) == 1
        assert received_events[0].payload.phase == "default_registry_test"


# =============================================================================
# Event Category Tests
# =============================================================================


class TestCognitiveEventCategory:
    """Tests for EventCategory.COGNITIVE."""

    def test_thinking_phase_event_category(self) -> None:
        """Test ThinkingPhaseEvent has COGNITIVE category."""
        event = ThinkingPhaseEvent.create(phase="test")
        assert event.category.value == "cognitive"

    def test_reflection_event_category(self) -> None:
        """Test ReflectionEvent has COGNITIVE category."""
        event = ReflectionEvent.create(reflection_type="test")
        assert event.category.value == "cognitive"

    def test_evolution_event_category(self) -> None:
        """Test EvolutionEvent has COGNITIVE category."""
        event = EvolutionEvent.create(trigger_type="test")
        assert event.category.value == "cognitive"

    def test_all_cognitive_events_have_correct_category(self) -> None:
        """Test all 11 cognitive events have COGNITIVE category."""
        events = [
            ThinkingPhaseEvent.create(phase="test"),
            ReflectionEvent.create(reflection_type="test"),
            EvolutionEvent.create(trigger_type="test"),
            BeliefChangeEvent.create(belief_key="test", old_value=0.0, new_value=1.0),
            ConfidenceCalibrationEvent.create(original_confidence=0.9, calibrated_confidence=0.7),
            PerceptionCompletedEvent.create(intent_type="test", confidence=0.8),
            ReasoningCompletedEvent.create(reasoning_type="test"),
            IntentDetectedEvent.create(intent_type="test", confidence=0.9),
            CriticalThinkingEvent.create(analysis_type="test"),
            CautiousExecutionEvent.create(execution_path="test"),
            ValueAlignmentEvent.create(action="test", verdict="APPROVED"),
        ]

        for event in events:
            assert event.category.value == "cognitive", f"{event.event_name} should have COGNITIVE category"
