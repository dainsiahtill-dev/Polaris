"""Meta-Cognition Engine - Self-reflection and confidence calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from polaris.kernelone.llm.invocations import LLMInvoker


class ReflectIntent(TypedDict, total=False):
    """Intent context passed to reflect()."""

    graph_id: str
    intent_type: str
    surface_intent: str
    deep_intent: str


@dataclass(frozen=True, slots=True)
class MetaCognitionSnapshot:
    """Captures meta-cognitive state at a point in time."""

    # Non-default fields first
    knowledge_boundary_confidence: float  # 0.0-1.0
    reasoning_chain_summary: str
    output_confidence: float  # 0.0-1.0
    # Default fields after
    knowledge_domains: tuple[str, ...] = field(default_factory=tuple)
    knowledge_gaps: tuple[str, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    alternative_hypotheses: tuple[str, ...] = field(default_factory=tuple)
    uncertainty_sources: tuple[str, ...] = field(default_factory=tuple)
    corrections_made: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ConfidenceCalibrationRecord:
    """Record for confidence calibration tracking."""

    stated_confidence: float
    actual_outcome: float  # 0.0-1.0
    deviation: float  # stated - actual
    calibration_type: str  # well_calibrated | overconfident | underconfident
    sample_size: int
    trend: str  # improving | stable | degrading


@dataclass(frozen=True, slots=True)
class ReflectionOutput:
    """Three-level reflection output."""

    # Level 1: Task-Level (immediate task performance)
    task_level: dict[str, Any] = field(default_factory=dict)
    # Level 2: Pattern-Level (cross-task patterns)
    pattern_level: dict[str, Any] = field(default_factory=dict)
    # Level 3: Meta-Level (systematic biases)
    meta_level: dict[str, Any] = field(default_factory=dict)
    # Knowledge precipitation
    rules_learned: tuple[str, ...] = field(default_factory=tuple)
    boundaries_updated: tuple[str, ...] = field(default_factory=tuple)
    patterns_identified: tuple[str, ...] = field(default_factory=tuple)
    knowledge_gaps: tuple[str, ...] = field(default_factory=tuple)


class MetaCognitionEngine:
    """
    Implements meta-cognition: thinking about thinking.

    Three-level reflection:
    - Task-Level: Did we complete this task well?
    - Pattern-Level: What patterns do we see across tasks?
    - Meta-Level: What systematic biases exist?
    """

    def __init__(self, llm_invoker: LLMInvoker | None = None):
        self._calibration_history: list[ConfidenceCalibrationRecord] = []
        self._reflection_history: list[ReflectionOutput] = []
        self._llm = llm_invoker

    async def reflect_with_llm(
        self,
        task_result: dict[str, Any],
        intent: ReflectIntent | None,
    ) -> ReflectionOutput:
        """
        LLM-powered reflection.

        Uses the LLM to perform deeper reflection analysis when available.

        Falls back to rule-based reflection if no LLM is configured.
        """
        if self._llm is None:
            return await self.reflect(task_result, intent)

        reflection_prompt = self._build_reflection_prompt(task_result, intent)

        try:
            response = await self._llm.invoke(reflection_prompt)

            # Parse LLM response into reflection output
            return self._parse_reflection_from_llm(response, task_result, intent)

        except (RuntimeError, ValueError):
            # LLM failed, fall back to rule-based
            return await self.reflect(task_result, intent)

    def _build_reflection_prompt(
        self,
        task_result: dict[str, Any],
        intent: ReflectIntent | None,
    ) -> str:
        """Build prompt for LLM reflection."""
        task_desc = intent.get("intent_type", "unknown") if intent else "unknown"
        success = task_result.get("success", False)
        output = task_result.get("content", "")[:500]

        return f"""## Task: Meta-Cognitive Reflection

Reflect on the following task execution at three levels:

### Task-Level Analysis
Task: {task_desc}
Success: {success}
Output: {output}

### Questions to Answer:
1. Task-Level: Did we complete this task well? What went right/wrong?
2. Pattern-Level: What patterns do you see across similar tasks?
3. Meta-Level: What systematic biases or tendencies exist?

### Also identify:
- Rules learned (if any)
- Knowledge gaps discovered
- Patterns observed

Respond in a structured format with clear sections."""

    def _parse_reflection_from_llm(
        self,
        response: str,
        task_result: dict[str, Any],
        intent: ReflectIntent | None,
    ) -> ReflectionOutput:
        """Parse LLM response into ReflectionOutput."""
        # Parse task-level insights
        task_level = {
            "task_id": intent.get("graph_id", "unknown") if intent else "unknown",
            "completion_quality": 0.7 if task_result.get("success") else 0.4,
            "unexpected_events": (),
            "task_completion_quality": task_result.get("success", False),
            "llm_analysis": response[:500],
        }

        # Parse pattern and meta-level from response
        pattern_level = {"recurring_patterns": (), "methodology_adjustments": (), "better_approaches_identified": ()}
        meta_level = {
            "cognitive_biases_detected": (),
            "self_correction_effectiveness": 0.6,
            "learning_rate_indicator": "stable",
        }

        # Try to extract rules learned from response
        rules_learned: list[str] = []
        gaps: list[str] = []
        patterns: list[str] = []

        lines = response.strip().split("\n")
        for line in lines:
            line_lower = line.strip().lower()
            if "rule" in line_lower or "learned" in line_lower:
                rules_learned.append(line.strip())
            elif "gap" in line_lower or "missing" in line_lower:
                gaps.append(line.strip())
            elif "pattern" in line_lower:
                patterns.append(line.strip())

        reflection = ReflectionOutput(
            task_level=task_level,
            pattern_level=pattern_level,
            meta_level=meta_level,
            rules_learned=tuple(rules_learned),
            boundaries_updated=tuple(gaps),
            patterns_identified=tuple(patterns),
            knowledge_gaps=tuple(gaps),
        )

        self._reflection_history.append(reflection)
        return reflection

    async def assess_knowledge_boundary(
        self,
        domain: str,
        working_state: Any = None,
    ) -> tuple[float, tuple[str, ...]]:
        """
        Assess what we know vs don't know about a domain.

        Returns:
            (confidence_level, knowledge_gaps)
        """
        # Rule-based for v1.0
        known_domains = {
            "python": 0.9,
            "typescript": 0.7,
            "rust": 0.5,
            "go": 0.4,
            "javascript": 0.7,
            "java": 0.6,
            "sql": 0.6,
            "bash": 0.6,
            "shell": 0.6,
        }

        confidence = known_domains.get(domain.lower(), 0.3)
        gaps = []

        if confidence < 0.5:
            gaps.append(f"Limited knowledge of {domain}")
            gaps.append("Consider seeking documentation or examples")

        return confidence, tuple(gaps)

    async def audit_thought_process(
        self,
        reasoning_chain: Any,
        assumptions: tuple[str, ...],
    ) -> MetaCognitionSnapshot:
        """Audit the reasoning chain and produce meta-cognition snapshot."""
        # Identify knowledge gaps
        knowledge_gaps: list[str] = []
        for assumption in assumptions:
            if isinstance(assumption, dict) and assumption.get("confidence", 1.0) < 0.7:
                knowledge_gaps.append(f"Low confidence in: {assumption.get('text', 'unknown')}")

        # Summarize reasoning
        summary = f"Reasoned with {len(assumptions)} assumptions"

        # Generate alternatives
        alternatives: list[str] = [f"Alternative to: {a}" for a in assumptions[:2]]

        # Calculate output confidence
        if assumptions:
            output_conf = sum(a.get("confidence", 0.5) if isinstance(a, dict) else 0.5 for a in assumptions) / len(
                assumptions
            )
        else:
            output_conf = 0.5

        return MetaCognitionSnapshot(
            knowledge_boundary_confidence=output_conf,
            knowledge_domains=("software_engineering",),
            knowledge_gaps=tuple(knowledge_gaps),
            reasoning_chain_summary=summary,
            assumptions=assumptions,
            alternative_hypotheses=tuple(alternatives),
            output_confidence=output_conf,
            uncertainty_sources=("assumption_quality", "context_completeness"),
            corrections_made=(),
        )

    async def calibrate_confidence(
        self,
        stated: float,
        actual: float,
    ) -> ConfidenceCalibrationRecord:
        """Update confidence calibration based on actual outcome."""
        deviation = stated - actual

        if abs(deviation) < 0.1:
            cal_type = "well_calibrated"
        elif deviation > 0:
            cal_type = "overconfident"
        else:
            cal_type = "underconfident"

        record = ConfidenceCalibrationRecord(
            stated_confidence=stated,
            actual_outcome=actual,
            deviation=deviation,
            calibration_type=cal_type,
            sample_size=len(self._calibration_history) + 1,
            trend="stable",
        )

        self._calibration_history.append(record)

        # Keep last 50 records
        if len(self._calibration_history) > 50:
            self._calibration_history = self._calibration_history[-50:]

        return record

    async def reflect(
        self,
        task_result: dict[str, Any],
        intent: ReflectIntent | None,
    ) -> ReflectionOutput:
        """Generate three-level reflection on task execution."""
        # Level 1: Task-Level
        task_level = {
            "task_id": intent.get("graph_id", "unknown") if intent else "unknown",
            "completion_quality": task_result.get("quality", 0.5),
            "unexpected_events": task_result.get("unexpected", ()),
            "task_completion_quality": task_result.get("success", False),
        }

        # Level 2: Pattern-Level
        pattern_level = {
            "recurring_patterns": (),
            "methodology_adjustments": (),
            "better_approaches_identified": (),
        }

        # Level 3: Meta-Level
        meta_level = {
            "cognitive_biases_detected": (),
            "self_correction_effectiveness": 0.5,
            "learning_rate_indicator": "stable",
        }

        reflection = ReflectionOutput(
            task_level=task_level,
            pattern_level=pattern_level,
            meta_level=meta_level,
            rules_learned=(),
            boundaries_updated=(),
            patterns_identified=(),
            knowledge_gaps=(),
        )

        self._reflection_history.append(reflection)
        return reflection
