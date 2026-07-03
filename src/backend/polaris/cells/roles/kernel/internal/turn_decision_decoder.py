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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

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
from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_tool_arguments,
    normalize_tool_name,
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
            all_tools, _ = self._extract_tool_calls(response)
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
        all_tools, decode_failures = self._extract_tool_calls(response)
        native_tool_call_envelopes = self._native_tool_call_envelopes(response)

        # Step 2: 判断是否直接回答
        if self._is_final_answer(response, all_tools):
            direct_metadata: dict[str, Any] = {"source": "direct_answer", "model": response.model}
            if native_tool_call_envelopes:
                direct_metadata["native_tool_call_envelopes"] = native_tool_call_envelopes
            if decode_failures:
                # ADR-0090 I3.1: the model TRIED to call tools but every call failed to
                # parse — keep the prose answer, but surface the failures so the
                # orchestrator can run one corrective re-ask instead of losing the turn.
                direct_metadata["decode_failures"] = decode_failures
            return TurnDecision(
                turn_id=turn_id,
                kind=TurnDecisionKind.FINAL_ANSWER,
                visible_message=response.content,
                reasoning_summary=response.thinking,
                tool_batch=None,
                finalize_mode=FinalizeMode.NONE,
                domain=self.config.domain,
                metadata=direct_metadata,
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

            batch_metadata: dict[str, Any] = {
                "tool_count": len(all_tools),
                "native_tools": len(all_tools),
                "model": response.model,
            }
            if native_tool_call_envelopes:
                batch_metadata["native_tool_call_envelopes"] = native_tool_call_envelopes
            if decode_failures:
                batch_metadata["decode_failures"] = decode_failures
            return TurnDecision(
                turn_id=turn_id,
                kind=TurnDecisionKind.TOOL_BATCH,
                visible_message=response.content,
                reasoning_summary=response.thinking,
                tool_batch=tool_batch,
                finalize_mode=finalize_mode,
                domain=self.config.domain,
                metadata=batch_metadata,
            )

        # Step 6: 无法确定意图，请求澄清
        # ADR-0090 I3: 若模型确实尝试了工具调用但全部解析失败，标记真实根因,
        # 让编排层先做一次 corrective re-ask 而不是直接挂起等待人类。
        clarify_metadata: dict[str, Any] = {
            "source": "tool_call_decode_failure" if decode_failures else "clarification_needed",
            "raw_content_preview": response.content[:200],
        }
        if decode_failures:
            clarify_metadata["decode_failures"] = decode_failures
        return TurnDecision(
            turn_id=turn_id,
            kind=TurnDecisionKind.ASK_USER,
            visible_message="我需要更多信息才能继续。请澄清您的需求。",
            reasoning_summary=response.thinking,
            tool_batch=None,
            finalize_mode=FinalizeMode.NONE,
            domain=self.config.domain,
            metadata=clarify_metadata,
        )

    def _extract_tool_calls(self, response: RawLLMResponse) -> tuple[list[ToolInvocation], list[dict[str, str]]]:
        """
        提取工具调用：native-only

        关键逻辑：
        - 只消费 native_tool_calls
        - 允许同一工具同参数重复出现（例如 read -> edit -> read 验证链路）
        - 仅按 call_id 去重（用于防止流重连重放）
        - thinking / content 文本不参与执行性工具解析
        - 解析失败的调用不再无痕丢弃 (ADR-0090 I3.1)：收集 (tool, error) 供
          corrective retry 把确切错误反馈给模型。
        """
        tools: list[ToolInvocation] = []
        failures: list[dict[str, str]] = []
        seen_call_ids: set[str] = set()

        # 解析native tool calls
        for native in self._native_tool_calls(response):
            try:
                tool = self._parse_native_tool(native)
                call_id = str(tool["call_id"]).strip()
                if call_id and call_id in seen_call_ids:
                    continue
                if call_id:
                    seen_call_ids.add(call_id)
                tools.append(tool)
            except (RuntimeError, ValueError, TurnDecisionDecodeError) as exc:
                tool_hint = self._native_tool_name_hint(native)
                failures.append(
                    {
                        "tool": tool_hint,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                logger.warning(
                    "native_tool_call_decode_failed: tool=%s error=%s",
                    tool_hint or "<unknown>",
                    exc,
                )
                continue

        return tools, failures

    @staticmethod
    def _native_tool_calls(response: RawLLMResponse) -> list[dict[str, Any]]:
        native_calls = getattr(response, "native_tool_calls", None)
        if isinstance(native_calls, list):
            return [dict(item) for item in native_calls if isinstance(item, Mapping)]
        alias_calls = getattr(response, "tool_calls", None)
        if isinstance(alias_calls, list):
            return [dict(item) for item in alias_calls if isinstance(item, Mapping)]
        if isinstance(response, Mapping):
            raw_calls = response.get("native_tool_calls")
            if not isinstance(raw_calls, list):
                raw_calls = response.get("tool_calls")
            if isinstance(raw_calls, list):
                return [dict(item) for item in raw_calls if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _native_tool_call_envelopes(response: RawLLMResponse) -> list[dict[str, Any]]:
        usage = getattr(response, "usage", None)
        if not isinstance(usage, Mapping) and isinstance(response, Mapping):
            usage = response.get("usage")
        if not isinstance(usage, Mapping):
            return []
        envelopes = usage.get("native_tool_call_envelopes")
        if not isinstance(envelopes, list):
            return []
        return [dict(item) for item in envelopes if isinstance(item, Mapping)]

    @staticmethod
    def _native_tool_name_hint(native: dict[str, Any]) -> str:
        """Best-effort tool-name extraction from an unparseable native payload."""
        function = native.get("function")
        if isinstance(function, dict):
            name = str(function.get("name", "") or "").strip()
            if name:
                return name
        name = str(native.get("name", "") or "").strip()
        if name:
            return name
        flat_name = TurnDecisionDecoder._native_tool_name_from_payload(native)
        if flat_name:
            return flat_name
        function_call = native.get("functionCall") or native.get("function_call")
        if isinstance(function_call, dict):
            return str(function_call.get("name", "") or "").strip()
        return ""

    def _parse_native_tool(self, native: dict[str, Any]) -> ToolInvocation:
        """解析 provider 原生工具调用格式。

        The transaction decoder is the execution authorization point, so it
        must normalize provider-native envelopes here instead of assuming the
        upstream LLM caller has converted every response into OpenAI shape.
        """
        call_id: Any = native.get("id") or native.get("tool_call_id") or native.get("tool_use_id")
        function = native.get("function", {})
        if isinstance(function, dict) and function:
            tool_name = str(function.get("name", "") or "").strip()
            arguments = self._native_tool_arguments_payload(function)
        elif str(native.get("type") or "").strip().lower() == "tool_use" or self._native_tool_name_from_payload(native):
            tool_name = self._native_tool_name_from_payload(native)
            arguments = self._native_tool_arguments_payload(native)
        else:
            function_call = native.get("functionCall") or native.get("function_call")
            if not isinstance(function_call, dict):
                raise TurnDecisionDecodeError("native tool payload missing function/tool_use block")
            call_id = call_id or function_call.get("id")
            tool_name = str(function_call.get("name", "") or "").strip()
            arguments = self._native_tool_arguments_payload(function_call)

        if not tool_name:
            raise TurnDecisionDecodeError("native tool payload missing tool name")

        canonical_tool_name = normalize_tool_name(tool_name)
        parsed_arguments = self._parse_native_tool_arguments(arguments)
        normalized_arguments = normalize_tool_arguments(canonical_tool_name, parsed_arguments)

        return ToolInvocation(
            call_id=ToolCallId(str(call_id or self._generate_id())),
            tool_name=canonical_tool_name,
            arguments=normalized_arguments,
            effect_type=self._infer_effect_type(canonical_tool_name),
            execution_mode=self._infer_execution_mode(canonical_tool_name),
        )

    @staticmethod
    def _native_tool_name_from_payload(payload: dict[str, Any]) -> str:
        for key in ("name", "tool_name", "toolName", "function_name", "functionName"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _native_tool_arguments_payload(payload: dict[str, Any]) -> Any:
        for key in (
            "arguments",
            "args",
            "parameters",
            "params",
            "input",
            "kwargs",
            "tool_input",
            "toolInput",
            "tool_arguments",
            "toolArguments",
            "function_arguments",
            "functionArguments",
        ):
            if key in payload:
                return payload[key]
        return {}

    @staticmethod
    def _parse_native_tool_arguments(arguments: Any) -> dict[str, Any]:
        """Normalize provider-native tool arguments into a mapping.

        Strict JSON first; on failure a bounded lenient repair runs (ADR-0090) —
        the arguments string is complete at decode time, so repair is safe here.
        Still-unparseable input re-raises the ORIGINAL strict error so the
        decode-failure feedback quotes the real problem.
        """
        if isinstance(arguments, str):
            if not arguments.strip():
                return {}
            try:
                parsed = json.loads(arguments)
            except (ValueError, json.JSONDecodeError) as strict_error:
                from polaris.kernelone.llm.toolkit.parsers.lenient_json import (
                    parse_lenient_json_object,
                )

                repaired, was_repaired = parse_lenient_json_object(arguments)
                if repaired is None:
                    raise strict_error
                if was_repaired:
                    logger.info(
                        "native_tool_arguments_lenient_repair_applied: chars=%d",
                        len(arguments),
                    )
                parsed = repaired
        else:
            parsed = arguments
        if not isinstance(parsed, dict):
            raise TurnDecisionDecodeError("native tool payload arguments must be a mapping")
        return dict(parsed)

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
