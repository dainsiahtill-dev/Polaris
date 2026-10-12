"""FinalizationCaller - 负责 LLM_ONCE 收口的 LLM 调用器。

基于现有 LLMInvoker 拆分出的语义明确调用器，强制在 finalization 阶段
不提供任何工具定义且 tool_choice=none，防止再次触发工具调用。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import is_dataclass, replace
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.interaction_contract import TurnIntent, infer_turn_intent
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import native_tool_calls_from_response

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

    from .invoker import LLMInvoker


class FinalizationCaller:
    """Semantic LLM caller for the single LLM_ONCE finalization step.

    Enforces:
    - tools = None
    - tool_choice = "none"
    - role identity and caller-owned output contracts survive finalization
    - synthesis instructions report settled evidence without assuming delivery
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
        # Prebuilt messages bypass normal system-prompt injection. Append the
        # phase constraint to their role contract instead of replacing it.
        finalization_system_prompt = self._build_finalization_system_prompt(profile=profile, context=context)
        override = dict(getattr(context, "context_override", None) or {})
        prebuilt = list(override.get("_transaction_kernel_prebuilt_messages") or [])
        has_role_system = any(
            isinstance(message, Mapping)
            and str(message.get("role", "")).strip().lower() == "system"
            and str(message.get("content") or "").strip()
            for message in prebuilt
        )
        if not has_role_system and system_prompt:
            finalization_system_prompt = f"{system_prompt}\n\n{finalization_system_prompt}"
        new_context = self._override_prebuilt_system_prompt(context, finalization_system_prompt)

        response = await self._invoker.call(
            profile=profile,
            system_prompt=finalization_system_prompt,
            context=new_context,
            response_model=None,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
        )
        if getattr(response, "error", None):
            raise RuntimeError(str(response.error))
        raw_tool_calls = native_tool_calls_from_response(response)
        return {
            "content": response.content,
            "thinking": getattr(response, "thinking", None),
            "tool_calls": raw_tool_calls,
            "native_tool_calls": raw_tool_calls,
            "model": str(getattr(response, "model", "unknown") or "unknown"),
            "usage": dict(getattr(response, "metadata", {}) or {}),
        }

    def _override_prebuilt_system_prompt(self, context: ContextRequest, prompt: str) -> ContextRequest:
        """Preserve role contracts and add a tools-disabled synthesis constraint."""
        override = dict(getattr(context, "context_override", None) or {})
        prebuilt = list(override.get("_transaction_kernel_prebuilt_messages") or [])

        if prebuilt:
            system_index = next(
                (
                    index
                    for index, message in enumerate(prebuilt)
                    if isinstance(message, Mapping)
                    and str(message.get("role", "")).strip().lower() == "system"
                    and str(message.get("content") or "").strip()
                ),
                None,
            )
            if system_index is not None:
                original_system = dict(prebuilt.pop(system_index))
                original_system["content"] = f"{original_system['content']}\n\n{prompt}"
                prebuilt.insert(0, original_system)
            else:
                prebuilt.insert(0, {"role": "system", "content": prompt})
            override["_transaction_kernel_prebuilt_messages"] = prebuilt
        else:
            # Keep the normal gateway path so history and the latest user
            # request are projected, rather than manufacturing system-only
            # prebuilt messages that suppress that path.
            override.pop("_transaction_kernel_prebuilt_messages", None)

        override["_transaction_kernel_forced_tool_definitions"] = []
        override["_transaction_kernel_forced_tool_choice"] = "none"

        # ContextRequest carries snapshot, strategy, run, and event bindings;
        # reconstructing only its oldest fields silently drops those bindings.
        if is_dataclass(context) and not isinstance(context, type):
            return replace(context, context_override=override)
        # Compatibility for existing duck-typed caller/test contexts.
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
            "当前用户请求是推进/落地任务，按当前角色职责报告实际进度与剩余工作。"
            if execute_like
            else "当前用户请求偏分析/解释，按当前角色与输出契约给出证据支持的结论。"
        )
        if execute_like:
            output_rules = (
                "- 仅依据已确认的工具/提交收据说明实际改动，验证结果必须对应真实验证证据。\n"
                "- 证据不足、验证失败或任务未完成时，保留阻塞原因与下一步最小行动。"
            )
        else:
            output_rules = "- 围绕现有证据给出结论、风险与建议；证据不足时明确缺口。"

        return (
            "【阶段】FINAL ANSWER —— 本回合工具阶段已结束，仅综合当前可见证据。\n"
            "【角色与契约】保留原角色身份、职责边界及调用方输出契约；本阶段指令只约束收口行为。\n"
            "【任务】基于本回合工具结果报告进度，不推断整个任务的信息收集或交付已经完成。\n"
            "【优先级】当前用户请求 > 历史上下文（后者仅作参考，不得覆盖当前请求）。\n"
            f"{objective_line}\n"
            f"{output_rules}\n"
            "【事实约束】不得宣称未有真实证据的修改、验证或完成；只读工具成功不证明交付完成。\n"
            "【输出要求】遵守原输出格式，不改写为通用总结；此收口阶段工具关闭，不再探索或执行。\n"
            "仍需读取、修改或验证时，按原契约报告剩余工作，由上层工作流决定后续回合。"
        )
