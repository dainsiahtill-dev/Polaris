"""Cognitive-pipeline event triplets.

Bodies moved verbatim from the original
``polaris/kernelone/events/typed/schemas.py`` module to preserve behavior.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import EventBase, EventCategory, EventPayload

# =============================================================================
# Cognitive pipeline events
# =============================================================================


class ThinkingPhasePayload(EventPayload):
    """Payload for thinking phase event."""

    phase: str = Field(..., description="Thinking phase name")
    content: str = Field(default="", description="Thinking content")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence level")
    intent_type: str = Field(default="", description="Detected intent type")


class ThinkingPhaseEvent(EventBase):
    """Thinking phase event.

    Emitted during the thinking phase of cognitive processing.
    """

    event_name: Literal["thinking_phase"] = "thinking_phase"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ThinkingPhasePayload = Field(default_factory=ThinkingPhasePayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        phase: str,
        content: str = "",
        confidence: float = 0.5,
        intent_type: str = "",
        run_id: str = "",
        workspace: str = "",
    ) -> ThinkingPhaseEvent:
        """Factory method to create a ThinkingPhaseEvent."""
        return cls(
            payload=ThinkingPhasePayload(
                phase=phase,
                content=content,
                confidence=confidence,
                intent_type=intent_type,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ReflectionPayload(EventPayload):
    """Payload for reflection event."""

    reflection_type: str = Field(..., description="Type of reflection (pre|post|meta)")
    insights: list[str] = Field(default_factory=list, description="Reflection insights")
    knowledge_gaps: list[str] = Field(default_factory=list, description="Identified knowledge gaps")
    patterns_identified: list[str] = Field(default_factory=list, description="Patterns identified")


class ReflectionEvent(EventBase):
    """Reflection event.

    Emitted when the cognitive system performs self-reflection.
    """

    event_name: Literal["reflection"] = "reflection"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ReflectionPayload = Field(default_factory=ReflectionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        reflection_type: str,
        insights: list[str] | None = None,
        knowledge_gaps: list[str] | None = None,
        patterns_identified: list[str] | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> ReflectionEvent:
        """Factory method to create a ReflectionEvent."""
        return cls(
            payload=ReflectionPayload(
                reflection_type=reflection_type,
                insights=insights or [],
                knowledge_gaps=knowledge_gaps or [],
                patterns_identified=patterns_identified or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class EvolutionPayload(EventPayload):
    """Payload for evolution event."""

    trigger_type: str = Field(..., description="Trigger type for evolution")
    adaptation: str = Field(default="", description="Adaptation description")
    learning_recorded: bool = Field(default=False, description="Whether learning was recorded")


class EvolutionEvent(EventBase):
    """Evolution event.

    Emitted when the cognitive system evolves/adapts.
    """

    event_name: Literal["evolution"] = "evolution"
    category: EventCategory = EventCategory.COGNITIVE
    payload: EvolutionPayload = Field(default_factory=EvolutionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        trigger_type: str,
        adaptation: str = "",
        learning_recorded: bool = False,
        run_id: str = "",
        workspace: str = "",
    ) -> EvolutionEvent:
        """Factory method to create an EvolutionEvent."""
        return cls(
            payload=EvolutionPayload(
                trigger_type=trigger_type,
                adaptation=adaptation,
                learning_recorded=learning_recorded,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class BeliefChangePayload(EventPayload):
    """Payload for belief change event."""

    belief_key: str = Field(..., description="Belief identifier")
    old_value: float = Field(default=0.0, description="Previous belief value")
    new_value: float = Field(default=0.0, description="New belief value")
    reason: str = Field(default="", description="Reason for belief change")


class BeliefChangeEvent(EventBase):
    """Belief change event.

    Emitted when the cognitive system's beliefs are updated.
    """

    event_name: Literal["belief_change"] = "belief_change"
    category: EventCategory = EventCategory.COGNITIVE
    payload: BeliefChangePayload = Field(default_factory=BeliefChangePayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        belief_key: str,
        old_value: float = 0.0,
        new_value: float = 0.0,
        reason: str = "",
        run_id: str = "",
        workspace: str = "",
    ) -> BeliefChangeEvent:
        """Factory method to create a BeliefChangeEvent."""
        return cls(
            payload=BeliefChangePayload(
                belief_key=belief_key,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ConfidenceCalibrationPayload(EventPayload):
    """Payload for confidence calibration event."""

    original_confidence: float = Field(..., ge=0.0, le=1.0, description="Original confidence")
    calibrated_confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence")
    calibration_factor: float = Field(default=1.0, description="Calibration factor applied")


class ConfidenceCalibrationEvent(EventBase):
    """Confidence calibration event.

    Emitted when confidence scores are calibrated.
    """

    event_name: Literal["confidence_calibration"] = "confidence_calibration"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ConfidenceCalibrationPayload = Field(default_factory=ConfidenceCalibrationPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        original_confidence: float,
        calibrated_confidence: float,
        calibration_factor: float = 1.0,
        run_id: str = "",
        workspace: str = "",
    ) -> ConfidenceCalibrationEvent:
        """Factory method to create a ConfidenceCalibrationEvent."""
        return cls(
            payload=ConfidenceCalibrationPayload(
                original_confidence=original_confidence,
                calibrated_confidence=calibrated_confidence,
                calibration_factor=calibration_factor,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class PerceptionCompletedPayload(EventPayload):
    """Payload for perception completed event."""

    intent_type: str = Field(..., description="Detected intent type")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in detection")
    uncertainty_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Uncertainty score")


class PerceptionCompletedEvent(EventBase):
    """Perception completed event.

    Emitted when the perception layer completes processing.
    """

    event_name: Literal["perception_completed"] = "perception_completed"
    category: EventCategory = EventCategory.COGNITIVE
    payload: PerceptionCompletedPayload = Field(default_factory=PerceptionCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        intent_type: str,
        confidence: float,
        uncertainty_score: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> PerceptionCompletedEvent:
        """Factory method to create a PerceptionCompletedEvent."""
        return cls(
            payload=PerceptionCompletedPayload(
                intent_type=intent_type,
                confidence=confidence,
                uncertainty_score=uncertainty_score,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ReasoningCompletedPayload(EventPayload):
    """Payload for reasoning completed event."""

    reasoning_type: str = Field(..., description="Type of reasoning performed")
    conclusion: str = Field(default="", description="Reasoning conclusion")
    blockers: list[str] = Field(default_factory=list, description="Identified blockers")


class ReasoningCompletedEvent(EventBase):
    """Reasoning completed event.

    Emitted when the reasoning engine completes analysis.
    """

    event_name: Literal["reasoning_completed"] = "reasoning_completed"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ReasoningCompletedPayload = Field(default_factory=ReasoningCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        reasoning_type: str,
        conclusion: str = "",
        blockers: list[str] | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> ReasoningCompletedEvent:
        """Factory method to create a ReasoningCompletedEvent."""
        return cls(
            payload=ReasoningCompletedPayload(
                reasoning_type=reasoning_type,
                conclusion=conclusion,
                blockers=blockers or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class IntentDetectedPayload(EventPayload):
    """Payload for intent detected event."""

    intent_type: str = Field(..., description="Detected intent type")
    surface_intent: str = Field(default="", description="Surface level intent")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Detection confidence")


class IntentDetectedEvent(EventBase):
    """Intent detected event.

    Emitted when an intent is detected from user input.
    """

    event_name: Literal["intent_detected"] = "intent_detected"
    category: EventCategory = EventCategory.COGNITIVE
    payload: IntentDetectedPayload = Field(default_factory=IntentDetectedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        intent_type: str,
        surface_intent: str = "",
        confidence: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> IntentDetectedEvent:
        """Factory method to create an IntentDetectedEvent."""
        return cls(
            payload=IntentDetectedPayload(
                intent_type=intent_type,
                surface_intent=surface_intent,
                confidence=confidence,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class CriticalThinkingPayload(EventPayload):
    """Payload for critical thinking event."""

    analysis_type: str = Field(..., description="Type of critical analysis")
    findings: list[str] = Field(default_factory=list, description="Analysis findings")
    risk_level: str = Field(default="low", description="Assessed risk level")


class CriticalThinkingEvent(EventBase):
    """Critical thinking event.

    Emitted when critical thinking analysis is performed.
    """

    event_name: Literal["critical_thinking"] = "critical_thinking"
    category: EventCategory = EventCategory.COGNITIVE
    payload: CriticalThinkingPayload = Field(default_factory=CriticalThinkingPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        analysis_type: str,
        findings: list[str] | None = None,
        risk_level: str = "low",
        run_id: str = "",
        workspace: str = "",
    ) -> CriticalThinkingEvent:
        """Factory method to create a CriticalThinkingEvent."""
        return cls(
            payload=CriticalThinkingPayload(
                analysis_type=analysis_type,
                findings=findings or [],
                risk_level=risk_level,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class CautiousExecutionPayload(EventPayload):
    """Payload for cautious execution event."""

    execution_path: str = Field(..., description="Execution path taken")
    requires_confirmation: bool = Field(default=False, description="Whether confirmation is required")
    stakes_level: str = Field(default="low", description="Stakes level (low|medium|high)")


class CautiousExecutionEvent(EventBase):
    """Cautious execution event.

    Emitted when cautious execution policy is applied.
    """

    event_name: Literal["cautious_execution"] = "cautious_execution"
    category: EventCategory = EventCategory.COGNITIVE
    payload: CautiousExecutionPayload = Field(default_factory=CautiousExecutionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        execution_path: str,
        requires_confirmation: bool = False,
        stakes_level: str = "low",
        run_id: str = "",
        workspace: str = "",
    ) -> CautiousExecutionEvent:
        """Factory method to create a CautiousExecutionEvent."""
        return cls(
            payload=CautiousExecutionPayload(
                execution_path=execution_path,
                requires_confirmation=requires_confirmation,
                stakes_level=stakes_level,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ValueAlignmentPayload(EventPayload):
    """Payload for value alignment event."""

    action: str = Field(..., description="Action being evaluated")
    verdict: str = Field(..., description="Alignment verdict (APPROVED|REJECTED|PENDING)")
    conflicts: list[str] = Field(default_factory=list, description="Value conflicts identified")
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Alignment score")


class ValueAlignmentEvent(EventBase):
    """Value alignment event.

    Emitted when value alignment check is performed.
    """

    event_name: Literal["value_alignment"] = "value_alignment"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ValueAlignmentPayload = Field(default_factory=ValueAlignmentPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        action: str,
        verdict: str,
        conflicts: list[str] | None = None,
        overall_score: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> ValueAlignmentEvent:
        """Factory method to create a ValueAlignmentEvent."""
        return cls(
            payload=ValueAlignmentPayload(
                action=action,
                verdict=verdict,
                conflicts=conflicts or [],
                overall_score=overall_score,
            ),
            run_id=run_id,
            workspace=workspace,
        )
