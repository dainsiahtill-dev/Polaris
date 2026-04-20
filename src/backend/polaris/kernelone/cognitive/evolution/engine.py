"""Evolution Engine - Processes evolution triggers and manages belief lifecycle."""

from __future__ import annotations

from datetime import datetime

from polaris.kernelone.cognitive.evolution.belief_decay import (
    BeliefDecayEngine,
    DecayPolicy,
)
from polaris.kernelone.cognitive.evolution.bias_defense import (
    BiasDefenseEngine,
)
from polaris.kernelone.cognitive.evolution.knowledge_precipitation import (
    KnowledgePrecipitation,
)
from polaris.kernelone.cognitive.evolution.models import (
    Belief,
    BiasMetrics,
    EvolutionRecord,
    TriggerType,
)
from polaris.kernelone.cognitive.evolution.store import EvolutionStore
from polaris.kernelone.cognitive.reasoning.meta_cognition import ReflectionOutput


class EvolutionEngine:
    """
    Implements Continuous Evolution for Cognitive Life Form.

    Evolution Protocol:
    1. IDENTIFY - What knowledge was wrong/incorrect?
    2. CLASSIFY - What type of trigger is this?
    3. INTEGRATE - Update beliefs and create new knowledge
    4. VERIFY - Check consistency with existing knowledge
    5. MARK - Tag for follow-up if verification uncertain

    Anti-patterns (from protocol):
    - NEVER fossilize: No belief should be immutable
    - NEVER reject corrections: User corrections always trigger evaluation
    - NEVER hide errors: Mistakes are logged and tracked
    """

    def __init__(
        self,
        store: EvolutionStore,
        decay_policy: DecayPolicy | None = None,
    ) -> None:
        self._store = store
        self._belief_decay = BeliefDecayEngine(decay_policy)
        self._bias_metrics = BiasMetrics(
            confirmation_bias_exposure=0.0,
            overconfidence_exposure=0.0,
            availability_heuristic_exposure=0.0,
            anchoring_exposure=0.0,
            hindsight_bias_exposure=0.0,
            counter_evidence_seeks=0,
            assumption_challenges=0,
        )
        self._bias_defense = BiasDefenseEngine()
        self._knowledge_precipitation = KnowledgePrecipitation()

    async def process_trigger(
        self,
        trigger_type: TriggerType,
        content: str,
        previous_belief_id: str | None = None,
        previous_confidence: float | None = None,
        context: str = "",
    ) -> EvolutionRecord:
        """
        Process an evolution trigger event.

        This is the main entry point for the evolution system.
        """
        # Classify the trigger
        classification = self._classify_trigger(trigger_type, content)

        # Generate rationale
        rationale = self._generate_rationale(trigger_type, content, classification)

        # Record the evolution
        record = await self._store.record_evolution(
            trigger_type=trigger_type,
            content=content,
            previous_belief_id=previous_belief_id,
            previous_confidence=previous_confidence,
            context=context,
            rationale=rationale,
        )

        # Update bias metrics
        self._update_bias_metrics(trigger_type)

        return record

    def _classify_trigger(self, trigger_type: TriggerType, content: str) -> str:
        """Classify the trigger for tracking purposes."""
        classifications = {
            TriggerType.USER_CORRECTION: "direct_feedback",
            TriggerType.PREDICTION_MISMATCH: "outcome_mismatch",
            TriggerType.NEW_INFO: "information_acquisition",
            TriggerType.BETTER_METHOD: "approach_improvement",
            TriggerType.HYPOTHESIS_FALSIFIED: "theory_rejection",
            TriggerType.BIAS_DETECTED: "self_awareness",
            TriggerType.SELF_REFLECTION: "introspective_analysis",
        }
        return classifications.get(trigger_type, "unknown")

    def _generate_rationale(self, trigger_type: TriggerType, content: str, classification: str) -> str:
        """Generate human-readable rationale for the evolution."""
        templates = {
            TriggerType.USER_CORRECTION: "User provided correction: {content}",
            TriggerType.PREDICTION_MISMATCH: "Prediction did not match outcome: {content}",
            TriggerType.NEW_INFO: "New information acquired: {content}",
            TriggerType.BETTER_METHOD: "Better approach identified: {content}",
            TriggerType.HYPOTHESIS_FALSIFIED: "Hypothesis proven wrong: {content}",
            TriggerType.BIAS_DETECTED: "Cognitive bias identified: {content}",
            TriggerType.SELF_REFLECTION: "Self-reflection triggered: {content}",
        }

        template = templates.get(trigger_type, "Evolution event: {content}")
        return template.format(content=content[:100])

    def _update_bias_metrics(self, trigger_type: TriggerType) -> None:
        """Update bias exposure metrics based on trigger type."""
        m = self._bias_metrics
        if trigger_type == TriggerType.BIAS_DETECTED:
            self._bias_metrics = BiasMetrics(
                confirmation_bias_exposure=m.confirmation_bias_exposure + 0.1,
                overconfidence_exposure=m.overconfidence_exposure,
                availability_heuristic_exposure=m.availability_heuristic_exposure,
                anchoring_exposure=m.anchoring_exposure,
                hindsight_bias_exposure=m.hindsight_bias_exposure,
                counter_evidence_seeks=m.counter_evidence_seeks,
                assumption_challenges=m.assumption_challenges,
            )
        elif trigger_type == TriggerType.PREDICTION_MISMATCH:
            self._bias_metrics = BiasMetrics(
                confirmation_bias_exposure=m.confirmation_bias_exposure,
                overconfidence_exposure=m.overconfidence_exposure + 0.1,
                availability_heuristic_exposure=m.availability_heuristic_exposure,
                anchoring_exposure=m.anchoring_exposure,
                hindsight_bias_exposure=m.hindsight_bias_exposure,
                counter_evidence_seeks=m.counter_evidence_seeks,
                assumption_challenges=m.assumption_challenges,
            )
        elif trigger_type == TriggerType.SELF_REFLECTION:
            self._bias_metrics = BiasMetrics(
                confirmation_bias_exposure=m.confirmation_bias_exposure,
                overconfidence_exposure=m.overconfidence_exposure,
                availability_heuristic_exposure=m.availability_heuristic_exposure,
                anchoring_exposure=m.anchoring_exposure,
                hindsight_bias_exposure=m.hindsight_bias_exposure + 0.05,
                counter_evidence_seeks=m.counter_evidence_seeks,
                assumption_challenges=m.assumption_challenges,
            )

    async def evolve_from_reflection(
        self,
        reflection: ReflectionOutput,
    ) -> tuple[EvolutionRecord, ...]:
        """
        Process evolution from reflection output.

        Called after MetaCognitionEngine.reflect()
        Integrates with KnowledgePrecipitation and BiasDefense for full cognitive evolution.
        """
        records = []

        # Step 1: Use KnowledgePrecipitation to distill learning from reflection
        task_result = {
            "intent_type": "reflection",
            "success": len(reflection.rules_learned) > 0,
            "output": str(reflection.patterns_identified),
            "error_message": str(reflection.knowledge_gaps),
        }
        precipitated = self._knowledge_precipitation.precipitate(
            task_result=task_result,
            reflection_output=reflection,
        )

        # Step 2: Detect biases in reasoning from reflection
        reasoning_content = " ".join(reflection.rules_learned) + " ".join(
            str(p) for p in reflection.patterns_identified
        )
        bias_result = self._bias_defense.detect_bias(reasoning_content, context={"source": "reflection"})
        bias_context = f"biases_detected={bias_result.biases_detected}" if bias_result.biases_detected else ""

        # Step 3: Process precipitated knowledge and detected biases
        for rule in precipitated.rules_learned:
            # Apply bias mitigation if biases were detected
            content = rule
            if bias_result.biases_detected:
                content = self._bias_defense.apply_mitigation(rule, bias_result.biases_detected)
                record = await self.process_trigger(
                    trigger_type=TriggerType.BIAS_DETECTED,
                    content=content,
                    context=f"reflection_output {bias_context}",
                )
            else:
                record = await self.process_trigger(
                    trigger_type=TriggerType.SELF_REFLECTION,
                    content=rule,
                    context="reflection_output",
                )
            records.append(record)

        # Step 4: Process knowledge gaps
        for gap in precipitated.knowledge_gaps:
            record = await self.process_trigger(
                trigger_type=TriggerType.NEW_INFO,
                content=f"Knowledge gap identified: {gap}",
                context="reflection_output",
            )
            records.append(record)

        # Step 5: Process boundaries if updated
        for boundary in precipitated.boundaries_updated:
            record = await self.process_trigger(
                trigger_type=TriggerType.BETTER_METHOD,
                content=f"Boundary refined: {boundary}",
                context="reflection_output",
            )
            records.append(record)

        return tuple(records)

    async def detect_repeated_mistakes(self, limit: int = 20) -> tuple[str, ...]:
        """
        Detect if the same patterns of mistakes are recurring.

        Returns tuple of pattern descriptions if recurrence detected.
        """
        recent = await self._store.get_recent_evolution(limit)

        # Group by trigger type
        trigger_counts: dict[str, int] = {}
        for record in recent:
            trigger_str = record.trigger_type.value
            trigger_counts[trigger_str] = trigger_counts.get(trigger_str, 0) + 1

        # Detect recurrence (same trigger type 3+ times)
        repeated = []
        for trigger_type, count in trigger_counts.items():
            if count >= 3:
                repeated.append(f"Recurring {trigger_type}: {count} occurrences")

        return tuple(repeated)

    async def apply_belief_decay(self, now: datetime | None = None) -> list[Belief]:
        """Apply time-based decay to all beliefs in the store.

        Args:
            now: Override for current time (useful in tests).

        Returns:
            List of decayed belief snapshots.
        """
        beliefs = await self._store.query_beliefs(limit=10_000)
        if not beliefs:
            return []
        decayed = self._belief_decay.apply_decay(beliefs, now=now)
        # Write back the decayed confidence values
        for original, updated in zip(beliefs, decayed, strict=True):
            if original.confidence != updated.confidence:
                await self._store.update_belief(
                    updated.belief_id,
                    new_confidence=updated.confidence,
                    rationale="Automatic time-based belief decay",
                )
        return decayed

    async def evolve_and_decay(
        self,
        trigger_type: TriggerType,
        content: str,
        previous_belief_id: str | None = None,
        previous_confidence: float | None = None,
        context: str = "",
    ) -> tuple[EvolutionRecord, list[Belief]]:
        """Convenience method: process a trigger then apply decay.

        Returns:
            Tuple of (evolution_record, decayed_beliefs).
        """
        record = await self.process_trigger(
            trigger_type=trigger_type,
            content=content,
            previous_belief_id=previous_belief_id,
            previous_confidence=previous_confidence,
            context=context,
        )
        decayed = await self.apply_belief_decay()
        return record, decayed

    def get_bias_metrics(self) -> BiasMetrics:
        """Get current bias exposure metrics."""
        return self._bias_metrics
