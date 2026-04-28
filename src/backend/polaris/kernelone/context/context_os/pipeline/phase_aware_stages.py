"""Phase-Aware Pipeline Stages: enhanced stages for ContextOS 3.0.

This module provides phase-aware versions of pipeline stages that use
TaskPhaseDetector and PhaseAwareBudgetPlanner for dynamic budget allocation.

Key Design Principle:
    "Budget is not a number, but a strategy object."
    Different phases need different context compositions.

Usage:
    # Use phase-aware budget planner
    phase_planner = PhaseAwareBudgetPlannerStage(policy, resolved_context_window)
    budget_out = phase_planner.process(patcher_out, canon_out)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.helpers import _estimate_tokens
from polaris.kernelone.context.context_os.models_v2 import (
    BudgetPlanV2 as BudgetPlan,
)
from polaris.kernelone.context.context_os.phase_budget_planner import (
    PHASE_BUDGET_PROFILES,
    BudgetProfile,
    PhaseAwareBudgetPlan,
    PhaseAwareBudgetPlanner,
)
from polaris.kernelone.context.context_os.phase_detection import (
    PhaseDetectionResult,
    TaskPhase,
    TaskPhaseDetector,
)

from .contracts import (
    BudgetPlannerOutput,
    CanonicalizerOutput,
    StatePatcherOutput,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

logger = logging.getLogger(__name__)


class PhaseAwareBudgetPlannerStage:
    """Stage 4 (Enhanced): Compute token budgets with phase-aware dynamic allocation.

    This stage replaces the fixed-ratio BudgetPlanner with a phase-aware
    version that adapts budget allocation based on detected task phase.

    Phases:
        INTAKE:        High contract ratio (understand requirements)
        PLANNING:      High retrieval ratio (research)
        EXPLORATION:   High tool ratio (exploration)
        IMPLEMENTATION: High output ratio (code generation)
        VERIFICATION:  High evidence ratio (test results)
        DEBUGGING:     High code_context ratio (error logs)
        REVIEW:        Balanced (comprehensive review)
    """

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        resolved_context_window: int,
        enable_phase_detection: bool = True,
    ) -> None:
        self._policy = policy
        self._resolved_context_window = resolved_context_window
        self._enable_phase_detection = enable_phase_detection
        self._phase_detector = TaskPhaseDetector() if enable_phase_detection else None
        self._budget_planner = PhaseAwareBudgetPlanner(
            resolved_context_window=resolved_context_window,
        )

    def process(
        self,
        patcher_out: StatePatcherOutput,
        canon_out: CanonicalizerOutput,
    ) -> tuple[BudgetPlannerOutput, PhaseDetectionResult | None]:
        """Compute phase-aware budget plan.

        Args:
            patcher_out: StatePatcher output with WorkingState
            canon_out: Canonicalizer output with transcript and artifacts

        Returns:
            Tuple of (BudgetPlannerOutput, PhaseDetectionResult or None)
        """
        transcript = canon_out.transcript
        artifacts = canon_out.artifacts
        working_state = patcher_out.working_state

        # Detect phase if enabled
        phase_result = None
        detected_phase = TaskPhase.INTAKE

        if self._enable_phase_detection and self._phase_detector is not None:
            phase_result = self._phase_detector.detect_phase(
                working_state=working_state,
                recent_events=transcript[-10:] if transcript else (),
            )
            detected_phase = phase_result.phase
            logger.info(
                "Phase detected: %s (confidence=%.2f, reason=%s)",
                detected_phase.value,
                phase_result.confidence,
                phase_result.reason,
            )

        # Calculate token counts
        transcript_tokens = sum(_estimate_tokens(item.content) for item in transcript)
        artifact_tokens = sum(min(item.token_count, 128) for item in artifacts)

        # Get phase-aware budget plan
        phase_plan = self._budget_planner.plan_budget(
            phase=detected_phase,
            transcript_tokens=transcript_tokens,
            artifact_tokens=artifact_tokens,
            p95_tool_result_tokens=int(self._policy.p95_tool_result_tokens),
            planned_retrieval_tokens=int(self._policy.planned_retrieval_tokens),
        )

        # Convert to standard BudgetPlan for compatibility
        budget_plan = BudgetPlan(
            model_context_window=phase_plan.model_context_window,
            output_reserve=phase_plan.output_reserve,
            tool_reserve=phase_plan.tool_reserve,
            safety_margin=phase_plan.safety_margin,
            input_budget=phase_plan.input_budget,
            retrieval_budget=phase_plan.retrieval_budget,
            soft_limit=phase_plan.soft_limit,
            hard_limit=phase_plan.hard_limit,
            emergency_limit=phase_plan.emergency_limit,
            current_input_tokens=phase_plan.current_input_tokens,
            expected_next_input_tokens=phase_plan.expected_next_input_tokens,
            p95_tool_result_tokens=phase_plan.p95_tool_result_tokens,
            planned_retrieval_tokens=phase_plan.planned_retrieval_tokens,
            validation_error=phase_plan.validation_error,
        )

        # Validate invariants
        budget_plan.validate_invariants()

        logger.debug(
            "Phase-aware budget: phase=%s window=%d input=%d soft=%d hard=%d",
            detected_phase.value,
            phase_plan.model_context_window,
            phase_plan.input_budget,
            phase_plan.soft_limit,
            phase_plan.hard_limit,
        )

        return BudgetPlannerOutput(budget_plan=budget_plan), phase_result

    @property
    def current_phase(self) -> TaskPhase:
        """Get current detected phase."""
        if self._phase_detector is not None:
            return self._phase_detector.current_phase
        return TaskPhase.INTAKE

    @property
    def phase_turn_count(self) -> int:
        """Get number of turns in current phase."""
        if self._phase_detector is not None:
            return self._phase_detector.phase_turn_count
        return 0
