"""Phase-Aware Budget Planner: dynamic budget allocation based on task phase.

This module implements ContextOS 3.0 Phase 2: Phase-Aware Budgeting.
Instead of fixed ratios, budget allocation adapts to the detected task phase.

Key Design Principle:
    "Budget is not a number, but a strategy object."
    Different phases need different context compositions.

Budget Profiles:
    INTAKE:        High contract ratio (understand requirements)
    PLANNING:      High retrieval ratio (research)
    EXPLORATION:   High tool ratio (exploration)
    IMPLEMENTATION: High output ratio (code generation)
    VERIFICATION:  High evidence ratio (test results)
    DEBUGGING:     High code_context ratio (error logs)
    REVIEW:        Balanced (comprehensive review)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.context.context_os.phase_detection import TaskPhase

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    """Budget allocation ratios for a specific task phase."""

    # Reserve ratios (as fraction of total context window)
    reserve_output_ratio: float = 0.18
    reserve_tool_ratio: float = 0.10
    safety_margin_ratio: float = 0.05

    # Content allocation ratios (as fraction of input_budget)
    contract_ratio: float = 0.15
    active_window_ratio: float = 0.45
    retrieved_memory_ratio: float = 0.10
    code_context_ratio: float = 0.10
    evidence_ratio: float = 0.10
    scratchpad_ratio: float = 0.10

    # Minimum reserves (absolute tokens)
    output_reserve_min: int = 1024
    tool_reserve_min: int = 512
    safety_margin_min: int = 2048

    def to_dict(self) -> dict[str, float]:
        return {
            "reserve_output_ratio": self.reserve_output_ratio,
            "reserve_tool_ratio": self.reserve_tool_ratio,
            "safety_margin_ratio": self.safety_margin_ratio,
            "contract_ratio": self.contract_ratio,
            "active_window_ratio": self.active_window_ratio,
            "retrieved_memory_ratio": self.retrieved_memory_ratio,
            "code_context_ratio": self.code_context_ratio,
            "evidence_ratio": self.evidence_ratio,
            "scratchpad_ratio": self.scratchpad_ratio,
        }


# Pre-defined budget profiles per phase
PHASE_BUDGET_PROFILES: dict[TaskPhase, BudgetProfile] = {
    TaskPhase.INTAKE: BudgetProfile(
        reserve_output_ratio=0.12,
        reserve_tool_ratio=0.08,
        contract_ratio=0.25,  # High contract ratio for understanding requirements
        active_window_ratio=0.35,
        retrieved_memory_ratio=0.15,
        code_context_ratio=0.05,
        evidence_ratio=0.10,
        scratchpad_ratio=0.10,
    ),
    TaskPhase.PLANNING: BudgetProfile(
        reserve_output_ratio=0.18,
        reserve_tool_ratio=0.06,
        contract_ratio=0.20,
        active_window_ratio=0.30,
        retrieved_memory_ratio=0.20,  # High retrieval for research
        code_context_ratio=0.10,
        evidence_ratio=0.10,
        scratchpad_ratio=0.10,
    ),
    TaskPhase.EXPLORATION: BudgetProfile(
        reserve_output_ratio=0.12,
        reserve_tool_ratio=0.15,  # High tool budget for exploration
        contract_ratio=0.10,
        active_window_ratio=0.40,
        retrieved_memory_ratio=0.10,
        code_context_ratio=0.15,
        evidence_ratio=0.05,
        scratchpad_ratio=0.10,
    ),
    TaskPhase.IMPLEMENTATION: BudgetProfile(
        reserve_output_ratio=0.20,  # High output for code generation
        reserve_tool_ratio=0.12,
        contract_ratio=0.15,
        active_window_ratio=0.35,
        retrieved_memory_ratio=0.08,
        code_context_ratio=0.20,  # High code context
        evidence_ratio=0.12,
        scratchpad_ratio=0.10,
    ),
    TaskPhase.VERIFICATION: BudgetProfile(
        reserve_output_ratio=0.15,
        reserve_tool_ratio=0.10,
        contract_ratio=0.15,
        active_window_ratio=0.30,
        retrieved_memory_ratio=0.10,
        code_context_ratio=0.10,
        evidence_ratio=0.25,  # High evidence for test results
        scratchpad_ratio=0.10,
    ),
    TaskPhase.DEBUGGING: BudgetProfile(
        reserve_output_ratio=0.12,
        reserve_tool_ratio=0.08,
        contract_ratio=0.10,
        active_window_ratio=0.35,
        retrieved_memory_ratio=0.05,
        code_context_ratio=0.25,  # High code context for error logs
        evidence_ratio=0.20,  # High evidence for debugging
        scratchpad_ratio=0.05,
    ),
    TaskPhase.REVIEW: BudgetProfile(
        reserve_output_ratio=0.18,
        reserve_tool_ratio=0.06,
        contract_ratio=0.15,
        active_window_ratio=0.35,
        retrieved_memory_ratio=0.15,
        code_context_ratio=0.10,
        evidence_ratio=0.15,
        scratchpad_ratio=0.10,
    ),
}


@dataclass(frozen=True, slots=True)
class PhaseAwareBudgetPlan:
    """Budget plan with phase-aware dynamic allocation."""

    # Detected phase
    phase: TaskPhase
    phase_profile: BudgetProfile

    # Standard budget fields
    model_context_window: int = 128000
    output_reserve: int = 0
    tool_reserve: int = 0
    safety_margin: int = 0
    input_budget: int = 0
    retrieval_budget: int = 0
    soft_limit: int = 0
    hard_limit: int = 0
    emergency_limit: int = 0
    current_input_tokens: int = 0
    expected_next_input_tokens: int = 0
    p95_tool_result_tokens: int = 2048
    planned_retrieval_tokens: int = 1536
    validation_error: str = ""

    # Phase-specific budget allocations
    contract_budget: int = 0
    active_window_budget: int = 0
    retrieved_memory_budget: int = 0
    code_context_budget: int = 0
    evidence_budget: int = 0
    scratchpad_budget: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "phase_profile": self.phase_profile.to_dict(),
            "model_context_window": self.model_context_window,
            "output_reserve": self.output_reserve,
            "tool_reserve": self.tool_reserve,
            "safety_margin": self.safety_margin,
            "input_budget": self.input_budget,
            "retrieval_budget": self.retrieval_budget,
            "soft_limit": self.soft_limit,
            "hard_limit": self.hard_limit,
            "emergency_limit": self.emergency_limit,
            "current_input_tokens": self.current_input_tokens,
            "expected_next_input_tokens": self.expected_next_input_tokens,
            "contract_budget": self.contract_budget,
            "active_window_budget": self.active_window_budget,
            "retrieved_memory_budget": self.retrieved_memory_budget,
            "code_context_budget": self.code_context_budget,
            "evidence_budget": self.evidence_budget,
            "scratchpad_budget": self.scratchpad_budget,
        }


class PhaseAwareBudgetPlanner:
    """Budget planner with phase-aware dynamic allocation.

    Instead of fixed ratios, this planner adapts budget allocation
    based on the detected task phase.
    """

    def __init__(
        self,
        resolved_context_window: int = 128000,
        default_profile: BudgetProfile | None = None,
    ) -> None:
        self._resolved_context_window = resolved_context_window
        self._default_profile = default_profile or BudgetProfile()

    def plan_budget(
        self,
        phase: TaskPhase,
        transcript_tokens: int = 0,
        artifact_tokens: int = 0,
        p95_tool_result_tokens: int = 2048,
        planned_retrieval_tokens: int = 1536,
    ) -> PhaseAwareBudgetPlan:
        """Plan budget allocation based on detected phase.

        Args:
            phase: Detected task phase
            transcript_tokens: Current transcript token count
            artifact_tokens: Current artifact token count
            p95_tool_result_tokens: P95 tool result token estimate
            planned_retrieval_tokens: Planned retrieval token estimate

        Returns:
            PhaseAwareBudgetPlan with phase-specific allocations
        """
        # Get profile for phase
        profile = PHASE_BUDGET_PROFILES.get(phase, self._default_profile)

        # Calculate standard reserves
        window = max(4096, self._resolved_context_window)
        output_reserve = max(
            profile.output_reserve_min,
            int(window * profile.reserve_output_ratio),
        )
        tool_reserve = max(
            profile.tool_reserve_min,
            int(window * profile.reserve_tool_ratio),
        )
        safety_margin = max(
            profile.safety_margin_min,
            int(window * profile.safety_margin_ratio),
        )

        # Calculate input budget
        input_budget = max(1024, window - output_reserve - tool_reserve - safety_margin)
        retrieval_budget = min(
            max(256, int(input_budget * 0.12)),
            max(256, planned_retrieval_tokens),
        )

        # Calculate current and expected tokens
        current_input_tokens = transcript_tokens + artifact_tokens
        expected_next_input_tokens = current_input_tokens + p95_tool_result_tokens + retrieval_budget + output_reserve

        # Calculate phase-specific budgets
        contract_budget = int(input_budget * profile.contract_ratio)
        active_window_budget = int(input_budget * profile.active_window_ratio)
        retrieved_memory_budget = int(input_budget * profile.retrieved_memory_ratio)
        code_context_budget = int(input_budget * profile.code_context_ratio)
        evidence_budget = int(input_budget * profile.evidence_ratio)
        scratchpad_budget = int(input_budget * profile.scratchpad_ratio)

        # Calculate limits
        soft_limit = max(512, int(input_budget * 0.55))
        hard_limit = max(768, int(input_budget * 0.72))
        emergency_limit = max(1024, int(input_budget * 0.85))

        # Validate invariants
        validation_error = ""
        if expected_next_input_tokens > window:
            overrun = expected_next_input_tokens - window
            validation_error = (
                f"BudgetPlan invariant violated: expected_next_input_tokens "
                f"({expected_next_input_tokens}) exceeds model_context_window "
                f"({window}) by {overrun} tokens"
            )

        plan = PhaseAwareBudgetPlan(
            phase=phase,
            phase_profile=profile,
            model_context_window=window,
            output_reserve=output_reserve,
            tool_reserve=tool_reserve,
            safety_margin=safety_margin,
            input_budget=input_budget,
            retrieval_budget=retrieval_budget,
            soft_limit=soft_limit,
            hard_limit=hard_limit,
            emergency_limit=emergency_limit,
            current_input_tokens=current_input_tokens,
            expected_next_input_tokens=expected_next_input_tokens,
            p95_tool_result_tokens=p95_tool_result_tokens,
            planned_retrieval_tokens=planned_retrieval_tokens,
            validation_error=validation_error,
            contract_budget=contract_budget,
            active_window_budget=active_window_budget,
            retrieved_memory_budget=retrieved_memory_budget,
            code_context_budget=code_context_budget,
            evidence_budget=evidence_budget,
            scratchpad_budget=scratchpad_budget,
        )

        logger.debug(
            "Phase-aware budget: phase=%s window=%d input=%d soft=%d hard=%d "
            "contract=%d active_window=%d code_context=%d evidence=%d",
            phase.value,
            window,
            input_budget,
            soft_limit,
            hard_limit,
            contract_budget,
            active_window_budget,
            code_context_budget,
            evidence_budget,
        )

        return plan
