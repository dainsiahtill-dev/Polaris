"""
Turn Decision Decoder - 执行授权点收口

核心职责：
1. 从LLM响应中解码出唯一的TurnDecision
2. 确保thinking永远不会产生可执行工具
3. 统一 native tool calls 为唯一执行来源
4. 强制执行领域策略
"""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    _infer_effect_type as global_infer_effect_type,
    _infer_execution_mode as global_infer_execution_mode,
)

logger = logging.getLogger(__name__)


class TurnDecisionDecodeError(Exception):
    """决策解码错误"""

    pass


@dataclass
class DecodeConfig:
    """解码配置"""

    domain: Literal["document", "code"] = "document"
    max_tools_per_turn: int = 10
    enable_textual_fallback: bool = False


class TurnDecisionDecoder:
    """
    单一职责：把任何格式的LLM响应，转换为唯一的TurnDecision

    关键约束：
    - thinking内容永不参与工具解析
    - native tool calls 是唯一可执行来源
    - 超过阈值触发handoff_workflow
    """

    # 领域默认策略
    DOMAIN_DEFAULTS: dict[str, FinalizeMode] = {
        "document": FinalizeMode.LLM_ONCE,  # 文档域需要总结
        "code": FinalizeMode.LLM_ONCE,  # 代码域：工具结果需经 LLM 综合分析后输出（避免 raw dump）
    }

    def __init__(self, config: DecodeConfig | None = None) -> None:
        self.config = config or DecodeConfig()
        self._default_finalize = self.DOMAIN_DEFAULTS.get(self.config.domain, FinalizeMode.LLM_ONCE)

    def decode(
        self,
        response: RawLLMResponse,
        turn_id: TurnId,
        *,
        phase: str | None = None,
        finalize_mode_hint: FinalizeMode | None = None,
    ) -> TurnDecision:
        """
        解码LLM响应为TurnDecision

        决策优先级：
        1. 如果有 final_answer 标记 -> FINAL_ANSWER
        2. 如果有 native 工具调用 -> TOOL_BATCH (或 HANDOFF_WORKFLOW 如果复杂)
        3. 如果需要澄清 -> ASK_USER
        """

        # Step 0: Finalization phase — tool calls are hallucinations; discard them.
        if phase == "optional_finalize" and finalize_mode_hint == FinalizeMode.LLM_ONCE:
            all_tools = self._extract_tool_calls(response)
            if all_tools:
                # Model hallucinated tool calls during finalization despite tool_choice=none.
                # Log and drop them rather than panicking or handing off.
                logger.warning(
                    "finalization_hallucinated_tool_calls_dropped: turn_id=%s tools=%s",
                    turn_id,
                    [t.get("tool_name") for t in all_tools],
                )
            return TurnDecision(
                turn_id=turn_id,
                kind=TurnDecisionKind.FINAL_ANSWER,
                visible_message=response.content,
                reasoning_summary=response.thinking,
                tool_batch=None,
                finalize_mode=FinalizeMode.NONE,
                domain=self.config.domain,
                metadata={"source": "finalization_answer", "model": response.model},
            )

        # Step 1: 提取所有 native 工具调用
        # 关键：thinking/content 文本都不参与执行性工具解析
        all_tools = self._extract_tool_calls(response)

        # Step 2: 判断是否直接回答
        if self._is_final_answer(response, all_tools):
            return TurnDecision(
                turn_id=turn_id,
                kind=TurnDecisionKind.FINAL_ANSWER,
                visible_message=response.content,
                reasoning_summary=response.thinking,
                tool_batch=None,
                finalize_mode=FinalizeMode.NONE,
                domain=self.config.domain,
                metadata={"source": "direct_answer", "model": response.model},
            )

        # Step 3: 构建ToolBatch
        if all_tools:
            tool_batch = self._build_tool_batch(all_tools, turn_id)

            # Step 4: 确定finalize_mode
            finalize_mode = self._determine_finalize_mode(response, all_tools)

            # Step 5: 检查是否需要移交workflow
            if any(t["execution_mode"] == ToolExecutionMode.ASYNC_RECEIPT for t in all_tools):
                return self._create_handoff_decision(response, all_tools, turn_id, "async_operation")
            if self._should_handoff_to_workflow(all_tools, response):
                return self._create_handoff_decision(response, all_tools, turn_id, "complex_exploration")

            return TurnDecision(
                turn_id=turn_id,
                kind=TurnDecisionKind.TOOL_BATCH,
                visible_message=response.content,
                reasoning_summary=response.thinking,
                tool_batch=tool_batch,
                finalize_mode=finalize_mode,
                domain=self.config.domain,
                metadata={
                    "tool_count": len(all_tools),
                    "native_tools": len(response.native_tool_calls),
                    "model": response.model,
                },
            )

        # Step 6: 无法确定意图，请求澄清
        return TurnDecision(
            turn_id=turn_id,
            kind=TurnDecisionKind.ASK_USER,
            visible_message="我需要更多信息才能继续。请澄清您的需求。",
            reasoning_summary=response.thinking,
            tool_batch=None,
            finalize_mode=FinalizeMode.NONE,
            domain=self.config.domain,
            metadata={"source": "clarification_needed", "raw_content_preview": response.content[:200]},
        )

    def _extract_tool_calls(self, response: RawLLMResponse) -> list[ToolInvocation]:
        """
        提取工具调用：native-only

        关键逻辑：
        - 只消费 native_tool_calls
        - 允许同一工具同参数重复出现（例如 read -> edit -> read 验证链路）
        - 仅按 call_id 去重（用于防止流重连重放）
        - thinking / content 文本不参与执行性工具解析
        """
        tools: list[ToolInvocation] = []
        seen_call_ids: set[str] = set()

        # 解析native tool calls
        for native in response.native_tool_calls:
            try:
                tool = self._parse_native_tool(native)
                call_id = str(tool["call_id"]).strip()
                if call_id and call_id in seen_call_ids:
                    continue
                if call_id:
                    seen_call_ids.add(call_id)
                tools.append(tool)
            except (RuntimeError, ValueError, TurnDecisionDecodeError):
                # 记录但继续处理其他工具
                continue

        return tools

    def _parse_native_tool(self, native: dict) -> ToolInvocation:
        """解析provider原生格式"""
        function = native.get("function", {})
        if not isinstance(function, dict):
            raise TurnDecisionDecodeError("native tool payload missing function block")
        tool_name = str(function.get("name", "") or "").strip()
        if not tool_name:
            raise TurnDecisionDecodeError("native tool payload missing function.name")
        arguments = function.get("arguments", "{}")

        # 处理arguments可能是字符串或dict的情况
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise TurnDecisionDecodeError("native tool payload arguments must be a mapping")

        return ToolInvocation(
            call_id=ToolCallId(native.get("id", self._generate_id())),
            tool_name=tool_name,
            arguments=arguments,
            effect_type=self._infer_effect_type(tool_name),
            execution_mode=self._infer_execution_mode(tool_name),
        )

    def decode_for_finalization(
        self,
        response: RawLLMResponse,
        turn_id: TurnId,
        finalize_mode: FinalizeMode,
    ) -> TurnDecision:
        """
        FinalizationCaller guard: enforce conceptual tool_choice=none.
        If the LLM response contains tool calls during LLM_ONCE finalization,
        immediately return a protocol-panic HANDOFF_WORKFLOW decision.
        """
        return self.decode(
            response,
            turn_id,
            phase="optional_finalize",
            finalize_mode_hint=finalize_mode,
        )

    def _build_tool_batch(self, tools: list[ToolInvocation], turn_id: TurnId) -> ToolBatch:
        """按执行模式分类工具"""
        parallel = [t for t in tools if t["execution_mode"] == ToolExecutionMode.READONLY_PARALLEL]
        readonly_serial = [t for t in tools if t["execution_mode"] == ToolExecutionMode.READONLY_SERIAL]
        serial = [t for t in tools if t["execution_mode"] == ToolExecutionMode.WRITE_SERIAL]
        async_tools = [t for t in tools if t["execution_mode"] == ToolExecutionMode.ASYNC_RECEIPT]

        return ToolBatch(
            batch_id=BatchId(f"{turn_id}_batch"),
            invocations=tools,
            parallel_readonly=parallel,
            readonly_serial=readonly_serial,
            serial_writes=serial,
            async_receipts=async_tools,
        )

    def _determine_finalize_mode(self, response: RawLLMResponse, tools: list[ToolInvocation]) -> FinalizeMode:
        """
        确定finalize_mode

        策略：
        - 如果LLM显式指定 -> 使用指定值
        - 如果有写操作 -> NONE（工具结果即最终答案）
        - 否则使用领域默认
        """
        # 检查LLM是否显式指定
        content_lower = response.content.lower()
        if "[finalize_mode:none]" in content_lower:
            return FinalizeMode.NONE
        elif "[finalize_mode:local]" in content_lower:
            return FinalizeMode.LOCAL
        elif "[finalize_mode:llm_once]" in content_lower:
            return FinalizeMode.LLM_ONCE

        # 检查是否有写操作
        has_writes = any(t["execution_mode"] == ToolExecutionMode.WRITE_SERIAL for t in tools)

        # 写操作默认NONE
        if has_writes:
            return FinalizeMode.NONE

        # 使用领域默认
        return self._default_finalize

    def _should_handoff_to_workflow(self, tools: list[ToolInvocation], response: RawLLMResponse) -> bool:
        """
        判断是否应该移交workflow层

        触发条件：
        1. 明确标记[handoff_workflow]
        2. 包含async工具

        注：已移除“大量纯读取即移交 workflow”的启发式规则。
        LLM 显式指定的读取列表应走正常 TOOL_BATCH + LLM_ONCE 流程；
        ExplorationWorkflowRuntime 仅用于真正需要自适应探索的场景。
        """
        return "[handoff_workflow]" in response.content.lower() or any(
            t["execution_mode"] == ToolExecutionMode.ASYNC_RECEIPT for t in tools
        )

    def _create_handoff_decision(
        self, response: RawLLMResponse, tools: list[ToolInvocation], turn_id: TurnId, reason: str
    ) -> TurnDecision:
        """创建移交workflow的决策"""
        tool_batch = self._build_tool_batch(tools, turn_id) if tools else None

        return TurnDecision(
            turn_id=turn_id,
            kind=TurnDecisionKind.HANDOFF_WORKFLOW,
            visible_message=response.content,
            reasoning_summary=response.thinking,
            tool_batch=tool_batch,
            finalize_mode=FinalizeMode.NONE,
            domain=self.config.domain,
            metadata={
                "handoff_reason": reason,
                "tool_count": len(tools),
                "initial_tools": [t["tool_name"] for t in tools],
            },
        )

    def _is_final_answer(self, response: RawLLMResponse, tools: list[ToolInvocation]) -> bool:
        """判断是否直接回答"""
        import re

        visible_content = str(response.content or "").strip()
        if not tools and "[final_answer]" in response.content:
            return True
        from polaris.kernelone.llm.reasoning import strip_reasoning_tags

        stripped = strip_reasoning_tags(visible_content).strip()
        # Fallback: remove any remaining unclosed thinking blocks
        stripped = re.sub(r"<thinking\b.*?(?:</thinking>|$)", "", stripped, flags=re.DOTALL).strip()
        stripped = re.sub(r"<think\b.*?(?:</think>|$)", "", stripped, flags=re.DOTALL).strip()
        return len(tools) == 0 and bool(stripped)

    def _infer_execution_mode(self, tool_name: str) -> ToolExecutionMode:
        """根据工具名推断执行模式。

        优先使用 turn_contracts 全局函数，若返回 WRITE_SERIAL 且该工具
        不在 WRITE_TOOLS 常量中，则通过 ToolSpecRegistry fallback 避免误分类。
        """
        normalized = tool_name.lower().replace("-", "_")

        mode = global_infer_execution_mode(tool_name)
        if mode != ToolExecutionMode.WRITE_SERIAL:
            return mode

        # 明确在写工具白名单中 -> 确认为写
        if normalized in WRITE_TOOLS:
            return ToolExecutionMode.WRITE_SERIAL

        # Fallback: query ToolSpecRegistry for canonical classification
        # to avoid misclassifying read tools as write (which forces NONE finalize)
        try:
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            spec = ToolSpecRegistry.get(normalized)
            if spec is not None:
                if spec.is_read_tool():
                    return ToolExecutionMode.READONLY_PARALLEL
                if spec.is_write_tool():
                    return ToolExecutionMode.WRITE_SERIAL
                if spec.is_exec_tool():
                    # exec tools default to serial for safety
                    return ToolExecutionMode.WRITE_SERIAL
        except (ImportError, RuntimeError, KeyError, AttributeError):
            pass
        # 默认串行（安全优先）
        return ToolExecutionMode.WRITE_SERIAL

    def _infer_effect_type(self, tool_name: str) -> ToolEffectType:
        """根据工具名推断 effect type。

        优先使用 turn_contracts 全局函数，若返回 WRITE 且该工具不在
        WRITE_TOOLS 常量中，则通过 ToolSpecRegistry fallback 避免误分类。
        """
        normalized = tool_name.lower().replace("-", "_")

        mode = global_infer_execution_mode(tool_name)
        effect = global_infer_effect_type(tool_name, mode)
        if effect != ToolEffectType.WRITE:
            return effect

        # 明确在写工具白名单中 -> 确认为写
        if normalized in WRITE_TOOLS:
            return ToolEffectType.WRITE

        # Fallback: query ToolSpecRegistry for canonical classification
        try:
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            spec = ToolSpecRegistry.get(normalized)
            if spec is not None:
                if spec.is_read_tool():
                    return ToolEffectType.READ
                if spec.is_write_tool():
                    return ToolEffectType.WRITE
                if "async" in (spec.categories or ()):
                    return ToolEffectType.ASYNC
        except (ImportError, RuntimeError, KeyError, AttributeError):
            pass
        return ToolEffectType.WRITE

    def _generate_id(self) -> str:
        """生成唯一ID"""
        return str(uuid.uuid4())[:12]
