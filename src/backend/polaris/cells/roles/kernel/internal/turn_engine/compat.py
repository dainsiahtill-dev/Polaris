"""TurnEngine compatibility mixin - Phase 3/4 API for external callers.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 3 Compat Extraction

职责：
    提供 Phase 3/4 兼容 API，供外部调用者（如 kernel_bridge）使用。
    这些方法不是 run()/run_stream() 主循环的一部分，
    而是为遗留接口提供兼容层。

Wave 3 提取内容:
    - _state_to_history_tuples: transcript IR 转 legacy history tuples
    - _build_turn_request_from_state: ConversationState 转 RoleTurnRequest
    - _resolve_profile_from_state: 从 state 解析 role profile
    - _call_model: 兼容 model 调用路径
    - _decode: 兼容 decode 路径
    - _execute_tools: 兼容批量工具执行路径
    - _should_stop: 判断是否停止（接入 PolicyLayer）
    - _maybe_compact: 判断是否触发 context compaction

设计原则：
    - 作为 mixin 类，TurnEngine 继承使用
    - 保持向后兼容，不修改现有行为
    - 这些方法依赖 self._kernel / self._policy_layer，不能独立使用
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.conversation_state import ConversationState
    from polaris.cells.roles.kernel.internal.llm_caller import LLMResponse
    from polaris.cells.roles.profile.internal.schema import RoleTurnRequest

logger = logging.getLogger(__name__)


class TurnEngineCompatMixin:
    """Phase 3/4 兼容 API mixin。

    提供外部调用者使用的兼容方法，不参与 run()/run_stream() 主循环。
    """

    # ── Transcript IR 转换 ───────────────────────────────────────────────────────

    @staticmethod
    def _state_to_history_tuples(state: ConversationState) -> list[tuple[str, str]]:
        """Convert transcript IR items into legacy history tuples.

        Args:
            state: ConversationState instance with transcript.

        Returns:
            List of (role_label, content) tuples.
        """
        from polaris.cells.roles.kernel.public.transcript_ir import (
            AssistantMessage,
            ToolResult,
            UserMessage,
        )

        history: list[tuple[str, str]] = []
        for item in list(getattr(state, "transcript", []) or []):
            if isinstance(item, UserMessage):
                history.append(("user", str(getattr(item, "content", "") or "")))
            elif isinstance(item, AssistantMessage):
                history.append(("assistant", str(getattr(item, "content", "") or "")))
            elif isinstance(item, ToolResult):
                history.append(("tool", str(getattr(item, "content", "") or "")))
        return history

    # ── State to Request 转换 ───────────────────────────────────────────────────────

    def _build_turn_request_from_state(self: Any, state: ConversationState) -> RoleTurnRequest:
        """Build a compatibility RoleTurnRequest snapshot from ConversationState.

        Args:
            state: ConversationState instance.

        Returns:
            RoleTurnRequest instance for compatibility API.
        """
        from polaris.cells.roles.profile.public.service import (
            RoleExecutionMode,
            RoleTurnRequest,
        )

        history = self._state_to_history_tuples(state)
        latest_user_message = ""
        for role_label, content in reversed(history):
            if role_label == "user":
                latest_user_message = str(content or "")
                break

        return RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            workspace=str(state.workspace or self._kernel.workspace or "."),
            message=latest_user_message,
            history=history,
            context_override={},
        )

    # ── Profile 解析 ───────────────────────────────────────────────────────

    def _resolve_profile_from_state(self: Any, state: ConversationState) -> Any:
        """Resolve role profile for compatibility methods.

        Args:
            state: ConversationState instance.

        Returns:
            RoleProfile instance.

        Raises:
            ValueError: If state.role is empty or profile not found.
        """
        role_id = str(getattr(state, "role", "") or "").strip()
        if not role_id:
            raise ValueError("ConversationState.role is required")
        profile = self._kernel.registry.get_profile_or_raise(role_id)
        self._compat_last_profile = profile
        return profile

    # ── Model 调用 ───────────────────────────────────────────────────────

    async def _call_model(self: Any, state: ConversationState) -> LLMResponse:
        """Compatibility model call path for external callers.

        Args:
            state: ConversationState instance.

        Returns:
            LLMResponse instance.
        """
        kernel = self._kernel
        profile = self._resolve_profile_from_state(state)
        request = self._build_turn_request_from_state(state)

        system_prompt = str(getattr(state, "system_prompt", "") or "").strip()
        if not system_prompt:
            if hasattr(kernel, "_build_system_prompt_for_request"):
                system_prompt = kernel._build_system_prompt_for_request(
                    profile,
                    request,
                    request.prompt_appendix or "",
                )
            else:
                system_prompt = kernel._prompt_builder.build_system_prompt(
                    profile,
                    request.prompt_appendix or "",
                )

        if hasattr(kernel, "_build_context"):
            context = kernel._build_context(profile, request)
        else:
            from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest

            context = ContextRequest(
                message=request.message,
                history=tuple(request.history) if request.history else (),
                task_id=request.task_id,
            )

        # FIX: 使用 self._llm_caller (通过依赖注入) 而非 kernel._llm_caller
        llm_caller = getattr(self, "_llm_caller", None) or getattr(kernel, "_llm_caller", None)
        if llm_caller is None:
            raise RuntimeError("LLM caller not available")
        return await llm_caller.call(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            run_id=None,
            task_id=None,
            attempt=max(int(getattr(state.budgets, "turn_count", 0)), 0),
        )

    # ── Response 解码 ───────────────────────────────────────────────────────

    def _decode(self: Any, response: Any) -> dict[str, Any]:
        """Compatibility decode path that mirrors run()/run_stream contracts.

        Args:
            response: LLMResponse instance.

        Returns:
            Dict with error, turn, content, thinking, tool_calls.
        """
        from polaris.cells.roles.kernel.internal.turn_engine.artifacts import AssistantTurnArtifacts

        response_error = str(getattr(response, "error", "") or "").strip()
        if response_error:
            return {
                "error": response_error,
                "content": "",
                "thinking": None,
                "tool_calls": [],
            }

        content = str(getattr(response, "content", "") or "")
        metadata = getattr(response, "metadata", {})
        safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}

        native_tool_calls = getattr(response, "tool_calls", None)
        if not isinstance(native_tool_calls, list):
            native_tool_calls = safe_metadata.get("native_tool_calls")
        if not isinstance(native_tool_calls, list):
            native_tool_calls = []

        native_tool_provider = (
            str(
                getattr(response, "tool_call_provider", "") or safe_metadata.get("native_tool_provider", "") or "auto"
            ).strip()
            or "auto"
        )

        profile = self._compat_last_profile
        if profile is None:
            thinking_result = self._kernel._output_parser.parse_thinking(content)
            turn = AssistantTurnArtifacts(
                raw_content=str(thinking_result.clean_content or ""),
                clean_content=str(thinking_result.clean_content or ""),
                thinking=str(thinking_result.thinking or "") or None,
                native_tool_calls=(),
                native_tool_provider=native_tool_provider,
            )
            parsed_tool_calls: list[Any] = []
        else:
            turn = self._materialize_assistant_turn(
                profile=profile,
                raw_output=content,
                native_tool_calls=native_tool_calls,
                native_tool_provider=native_tool_provider,
            )
            parsed_tool_calls = self._parse_tool_calls_from_turn(profile=profile, turn=turn)

        normalized_calls: list[dict[str, Any]] = []
        for call in parsed_tool_calls:
            if isinstance(call, dict):
                normalized_calls.append(
                    {
                        "tool": str(call.get("tool") or call.get("name") or ""),
                        "args": dict(call.get("args") or call.get("arguments") or {}),
                    }
                )
            else:
                normalized_calls.append(
                    {
                        "tool": str(getattr(call, "tool", "") or getattr(call, "name", "") or ""),
                        "args": dict(getattr(call, "args", None) or getattr(call, "arguments", None) or {}),
                    }
                )

        return {
            "error": None,
            "turn": turn,
            "content": turn.clean_content,
            "thinking": turn.thinking,
            "tool_calls": normalized_calls,
        }

    # ── 工具执行 ───────────────────────────────────────────────────────

    async def _execute_tools(
        self: Any,
        tool_calls: list[Any],
        state: ConversationState,
    ) -> list[Any]:
        """Compatibility batch tool execution path for external callers.

        Args:
            tool_calls: List of tool calls to execute.
            state: ConversationState instance.

        Returns:
            List of tool execution results.
        """
        from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult

        profile = self._resolve_profile_from_state(state)
        request = self._build_turn_request_from_state(state)

        results: list[Any] = []
        for call in list(tool_calls or []):
            if isinstance(call, dict):
                tool_name = str(call.get("tool") or call.get("name") or "").strip()
                raw_args = call.get("args") or call.get("arguments") or {}
            else:
                tool_name = str(getattr(call, "tool", "") or getattr(call, "name", "")).strip()
                raw_args = getattr(call, "args", None)
                if raw_args is None:
                    raw_args = getattr(call, "arguments", None)
            tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}
            if not tool_name:
                continue
            parsed_call = ToolCallResult(tool=tool_name, args=tool_args)
            result = await self._execute_single_tool(
                profile=profile,
                request=request,
                call=parsed_call,
            )
            results.append(result)
        return results

    # ── 停止判断 ───────────────────────────────────────────────────────

    def _should_stop(self: Any, state: ConversationState) -> str | None:
        """判断是否停止（Phase 3 接入 PolicyLayer）。

        实际 policy 评估已在 run()/run_stream() 循环内部完成。
        本方法作为独立调用入口（kernel facade 直接调用场景），
        通过 PolicyLayer.evaluate([]) 做预算检查。

        Args:
            state: ConversationState instance.

        Returns:
            None if safe to continue, stop_reason string otherwise.
        """
        if self._policy_layer is None:
            return None
        result = self._policy_layer.evaluate(
            [],
            budget_state={
                "tool_call_count": state.budgets.tool_call_count,
                "turn_count": state.budgets.turn_count,
            },
        )
        return result.stop_reason

    # ── Context Compaction 判断 ───────────────────────────────────────────────────────

    def _maybe_compact(self: Any, state: ConversationState) -> bool:
        """Determine whether context compaction should be triggered.

        Args:
            state: ConversationState instance.

        Returns:
            True if compaction should be triggered, False otherwise.
        """
        if not state.is_within_budget():
            return True

        budgets = state.budgets

        def _pressure(current: float, maximum: float | int) -> float:
            if float(maximum) <= 0:
                return 0.0
            return float(current) / float(maximum)

        turn_pressure = _pressure(budgets.turn_count, budgets.max_turns)
        tool_pressure = _pressure(budgets.tool_call_count, budgets.max_tool_calls)
        wall_pressure = _pressure(budgets.wall_time_seconds, budgets.max_wall_time_seconds)
        token_pressure = 0.0
        if budgets.max_tokens is not None and budgets.max_tokens > 0:
            token_pressure = _pressure(budgets.total_tokens, budgets.max_tokens)

        return max(turn_pressure, tool_pressure, wall_pressure, token_pressure) >= 0.85


__all__ = [
    "TurnEngineCompatMixin",
]
