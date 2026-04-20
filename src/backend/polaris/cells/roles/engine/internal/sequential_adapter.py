"""Sequential Adapter - SequentialEngine compatibility shim.

The historical adapter previously delegated into ``roles.runtime``. That
cross-cell dependency is removed here so ``roles.engine`` remains self-contained
and only depends on its own public/base abstractions.
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
    """SequentialEngine compatibility shim.

    The old adapter wrapped a runtime-layer sequential executor. That coupling
    is no longer allowed in ``roles.engine``. This shim keeps the public
    strategy entrypoint available while executing as a simple single-shot
    engine backed by the injected LLM caller.
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
        """Execute the sequential compatibility path.

        The adapter keeps the sequential strategy name stable, but the actual
        execution now stays within the roles.engine boundary.
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
        """Sequential strategy is single-shot in this compatibility shim."""
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
    """Create a sequential compatibility shim with a bounded budget."""
    budget = EngineBudget(
        max_steps=max_steps,
        max_tool_calls_total=max_tool_calls,
        max_wall_time_seconds=max_time,
    )
    return SequentialEngineAdapter(workspace=workspace, budget=budget)
