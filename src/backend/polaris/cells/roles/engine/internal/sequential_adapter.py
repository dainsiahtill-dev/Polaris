"""Sequential Adapter - single-shot sequential strategy.

This strategy keeps sequential execution inside the ``roles.engine`` boundary.
It provides the low-complexity fallback path used by ``HybridEngine`` without
delegating into an external execution facade.
"""

from __future__ import annotations

import logging
import time

from .base import (
    BaseEngine,
    EngineBudget,
    EngineContext,
    EngineResult,
    EngineStatus,
    EngineStrategy,
    StepResult,
)

logger = logging.getLogger(__name__)


class SequentialEngineAdapter(BaseEngine):
    """Single-shot sequential engine strategy.

    The engine performs one LLM-backed step and records the result using the
    shared ``BaseEngine`` accounting primitives. It is intentionally small so
    callers can rely on it as the deterministic fallback strategy when more
    complex ReAct/plan-solve/tree-of-thought engines are not selected.
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
    ) -> None:
        super().__init__(workspace, budget)

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.SEQUENTIAL

    async def execute(
        self,
        context: EngineContext,
        initial_message: str = "",
    ) -> EngineResult:
        """Execute the sequential single-shot path.

        The implementation is O(1) in engine steps and makes one LLM call.
        Memory use is O(n) in the final response length stored in the step
        observation and final result.
        """
        self._status = EngineStatus.RUNNING
        self._start_time = time.time()

        task = str(initial_message or context.task or "").strip()
        try:
            response = await self._call_llm(
                context,
                task,
                max_tokens=self.budget.max_steps * 250,
            )
            final_answer = str(response or "").strip() or task or "Sequential execution completed"
            success = bool(str(response or "").strip() or task)
            if final_answer:
                self._steps.append(
                    StepResult(
                        step_index=0,
                        status=EngineStatus.COMPLETED if success else EngineStatus.FAILED,
                        action="llm_call",
                        action_input={"role": context.role},
                        observation=final_answer,
                        progress_detected=success,
                    )
                )
            self._status = EngineStatus.COMPLETED if success else EngineStatus.FAILED
            return self._create_result(
                success=success,
                final_answer=final_answer,
                termination_reason="completed" if success else "error",
            )
        except (RuntimeError, ValueError) as exc:
            logger.exception("SequentialEngine adapter error")
            self._status = EngineStatus.FAILED
            return self._create_result(
                success=False,
                final_answer=f"执行错误: {exc}",
                error=str(exc),
                termination_reason="error",
            )

    async def step(self, context: EngineContext) -> StepResult:
        """Return the current no-op step state for this single-shot strategy."""
        return StepResult(
            step_index=self._current_step,
            status=EngineStatus.IDLE,
        )

    def can_continue(self) -> bool:
        """Continue while the current budget allows it."""
        return self._check_budget()


def create_sequential_adapter(
    workspace: str = "",
    max_steps: int = 12,
    max_tool_calls: int = 24,
    max_time: int = 120,
) -> SequentialEngineAdapter:
    """Create a sequential engine adapter with a bounded budget."""
    budget = EngineBudget(
        max_steps=max_steps,
        max_tool_calls_total=max_tool_calls,
        max_wall_time_seconds=max_time,
    )
    return SequentialEngineAdapter(workspace=workspace, budget=budget)
