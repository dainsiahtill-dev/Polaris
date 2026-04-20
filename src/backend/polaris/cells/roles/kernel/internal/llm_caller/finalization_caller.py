"""FinalizationCaller - 负责 LLM_ONCE 收口的 LLM 调用器。

基于现有 LLMInvoker 拆分出的语义明确调用器，强制在 finalization 阶段
不提供任何工具定义且 tool_choice=none，防止再次触发工具调用。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.interaction_contract import TurnIntent, infer_turn_intent

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

    from .invoker import LLMInvoker


class FinalizationCaller:
    """Semantic LLM caller for the single LLM_ONCE finalization step.

    Enforces:
    - tools = None
    - tool_choice = "none"
    - system_prompt is overridden to a synthesis-oriented prompt so the model
      switches cognitive phase from "exploration" to "summary".
    """

    _EXECUTION_HINT_PATTERNS = (
        r"落地",
        r"推进",
        r"开工",
        r"改",
        r"修复",
        r"实现",
        r"write",
        r"edit",
        r"fix",
        r"patch",
        r"implement",
    )

    def __init__(self, llm_invoker: LLMInvoker) -> None:
        self._invoker = llm_invoker

    async def call(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> dict[str, Any]:
        """Call LLM in finalization mode (no tools, tool_choice=none).

        Returns a dict compatible with TransactionKernel finalization mapping.
        """
        # Root-cause fix: The exploration-phase system prompt embedded in
        # prebuilt messages must be replaced with a synthesis prompt.
        # _prepare_llm_request skips injecting `system_prompt` when the
        # prebuilt message list already starts with a system message, so
        # overwriting the prebuilt list is the only reliable way.
        finalization_system_prompt = self._build_finalization_system_prompt(profile=profile, context=context)
        new_context = self._override_prebuilt_system_prompt(context, finalization_system_prompt)

        response = await self._invoker.call(
            profile=profile,
            system_prompt="",  # prebuilt already contains the system message
            context=new_context,
            response_model=None,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
        )
        if getattr(response, "error", None):
            raise RuntimeError(str(response.error))
        raw_tool_calls = getattr(response, "tool_calls", []) or []
        return {
            "content": response.content,
            "thinking": getattr(response, "thinking", None),
            "tool_calls": raw_tool_calls,
            "model": str(getattr(response, "model", "unknown") or "unknown"),
            "usage": dict(getattr(response, "metadata", {}) or {}),
        }

    def _override_prebuilt_system_prompt(self, context: ContextRequest, prompt: str) -> ContextRequest:
        """Replace the first system message in prebuilt messages with synthesis prompt."""
        override = dict(getattr(context, "context_override", None) or {})
        prebuilt = list(override.get("_transaction_kernel_prebuilt_messages", []))

        if prebuilt and str(prebuilt[0].get("role", "")).strip().lower() == "system":
            # Replace existing system message content
            prebuilt[0] = {**prebuilt[0], "content": prompt}
        else:
            # Prepend synthesis system message
            prebuilt = [{"role": "system", "content": prompt}, *prebuilt]

        override["_transaction_kernel_prebuilt_messages"] = prebuilt

        # Rebuild frozen ContextRequest with updated override
        return type(context)(
            message=getattr(context, "message", ""),
            history=getattr(context, "history", ()),
            task_id=getattr(context, "task_id", None),
            context_override=override,
        )

    def _build_finalization_system_prompt(self, *, profile: RoleProfile, context: ContextRequest) -> str:
        message = str(getattr(context, "message", "") or "").strip()
        override = dict(getattr(context, "context_override", None) or {})
        domain = str(override.get("domain") or "code").strip().lower() or "code"
        role_id = str(getattr(profile, "role_id", "director") or "director")
        intent = infer_turn_intent(role_id=role_id, message=message, domain=domain)
        execute_like = intent in {
            TurnIntent.EXECUTE,
            TurnIntent.PLAN,
            TurnIntent.DESIGN,
            TurnIntent.REVIEW,
        } or any(re.search(pattern, message, re.IGNORECASE) for pattern in self._EXECUTION_HINT_PATTERNS)

        objective_line = (
            "当前用户请求是推进/落地任务，必须给出执行型最终交付，禁止退回泛化技术总结。"
            if execute_like
            else "当前用户请求偏分析/解释，输出结构化结论与可执行建议。"
        )
        if execute_like:
            output_rules = (
                "- 若已完成修改，明确列出改动文件、关键变更和验证结果。\n"
                "- 若受约束无法完成，明确阻塞原因与下一步最小行动。"
            )
        else:
            output_rules = "- 围绕证据给出结论、风险与改进建议，不要请求额外输入。"

        return (
            "【阶段】FINAL ANSWER —— 所有信息已收集完毕，直接交付最终答案。\n"
            "【任务】基于已执行的工具结果，一次性完成用户请求。\n"
            "【优先级】当前用户请求 > 历史上下文（后者仅作参考，不得覆盖当前请求）。\n"
            f"{objective_line}\n"
            f"{output_rules}\n"
            "【输出要求】直接给出完整结论或交付物，无需任何额外探索、验证或文件读取。"
        )
