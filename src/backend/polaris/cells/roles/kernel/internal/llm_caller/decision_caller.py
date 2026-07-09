"""DecisionCaller - 负责产出唯一一次 TurnDecision 的 LLM 调用器。

基于现有 LLMInvoker 拆分出的语义明确调用器，强制在决策阶段暴露工具
并允许 tool_choice=auto。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_facts_from_sources,
    project_native_tool_call_facts_to_metadata,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import native_tool_calls_from_response

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
            metadata = dict(getattr(response, "metadata", {}) or {})
            metadata.setdefault("error_message", str(response.error))
            error_category = str(getattr(response, "error_category", "") or "").strip()
            if error_category:
                metadata.setdefault("error_category", error_category)
            exc = RuntimeError(str(response.error))
            vars(exc)["llm_response_metadata"] = metadata
            vars(exc)["llm_response_model"] = str(metadata.get("model") or "unknown")
            vars(exc)["llm_response_error_category"] = error_category
            raise exc
        native_tool_calls = native_tool_calls_from_response(response)
        metadata = dict(getattr(response, "metadata", {}) or {})
        native_facts = native_tool_call_facts_from_sources(metadata, native_tool_calls)
        project_native_tool_call_facts_to_metadata(
            metadata,
            native_facts,
            project_decision_caller_count=True,
        )
        metadata["decision_caller_tool_call_provider"] = str(
            getattr(response, "tool_call_provider", "") or metadata.get("tool_call_provider") or "auto"
        )
        metadata.setdefault("tool_call_provider", metadata["decision_caller_tool_call_provider"])
        return {
            "content": response.content,
            "thinking": getattr(response, "thinking", None),
            "tool_calls": native_tool_calls,
            "native_tool_calls": native_tool_calls,
            "model": str(getattr(response, "model", "unknown") or "unknown"),
            "usage": metadata,
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
