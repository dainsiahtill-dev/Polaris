"""DecisionCaller - 负责产出唯一一次 TurnDecision 的 LLM 调用器。

基于现有 LLMInvoker 拆分出的语义明确调用器，强制在决策阶段暴露工具
并允许 tool_choice=auto。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

    from .invoker import LLMInvoker


class DecisionCaller:
    """Semantic LLM caller whose sole responsibility is to produce one TurnDecision."""

    def __init__(self, llm_invoker: LLMInvoker) -> None:
        self._invoker = llm_invoker

    async def call(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        tool_definitions: list[dict[str, Any]] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> dict[str, Any]:
        """Call LLM in decision mode (tools exposed, tool_choice=auto).

        Returns a dict compatible with TransactionKernel RawLLMResponse mapping.
        """
        response = await self._invoker.call(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            response_model=None,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
        )
        if getattr(response, "error", None):
            raise RuntimeError(str(response.error))
        return {
            "content": response.content,
            "thinking": getattr(response, "thinking", None),
            "tool_calls": getattr(response, "tool_calls", []) or [],
            "model": str(getattr(response, "model", "unknown") or "unknown"),
            "usage": dict(getattr(response, "metadata", {}) or {}),
        }

    async def call_stream(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        tool_definitions: list[dict[str, Any]] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> Any:
        """Stream decision request (delegates to LLMInvoker.call_stream)."""
        return self._invoker.call_stream(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
            event_emitter=event_emitter,
        )
