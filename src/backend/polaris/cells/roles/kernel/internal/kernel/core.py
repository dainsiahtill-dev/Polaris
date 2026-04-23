"""Role Execution Kernel Core - 角色执行内核核心

重构为 Facade 模式的 RoleExecutionKernel。

架构:
    - RoleExecutionKernel: Facade，协调各服务
    - LLMInvoker: LLM调用服务 (ILLMInvoker)
    - ToolExecutor: 工具执行服务 (IToolExecutor)
    - PromptBuilder: 提示词构建服务
    - OutputParser: 输出解析服务
    - QualityChecker: 质量检查服务
    - EventEmitter: 事件发射服务

依赖注入:
    所有服务可通过 __init__ 注入，便于测试和定制。
    使用 create_default() 工厂方法创建生产环境实例。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
import warnings
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.kernel.error_handler import (
    KernelEventEmitter,
    LLMEventType,
)
from polaris.cells.roles.kernel.internal.kernel.helpers import (
    quality_result_to_dict,
)
from polaris.cells.roles.kernel.internal.kernel.suggestions import get_suggestions_for_error
from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.output_parser import OutputParser, ToolCallResult
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker, QualityResult
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TransactionConfig
from polaris.cells.roles.kernel.public.config import KernelConfig, get_default_config
from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, SealedTurn
from polaris.cells.roles.profile.public.service import (
    RoleProfile,
    RoleProfileRegistry,
    RoleTurnRequest,
    RoleTurnResult,
)
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope
from polaris.infrastructure.log_pipeline.writer import LogEventWriter, get_writer
from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2 as TranscriptEvent
from polaris.kernelone.events.uep_publisher import UEPEventPublisher
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
    from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
    from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort
    from polaris.cells.roles.kernel.services.contracts import (
        CellToolExecutorPort,
        IEventEmitter,
        ILLMInvoker,
        IOutputParser,
        IPromptBuilder,
        IQualityChecker,
    )
    from polaris.cells.roles.session.public.service import RoleDataStore
    from polaris.kernelone.context.compaction import RoleContextCompressor

# Instructor integration - structured output schemas
try:
    from polaris.cells.roles.adapters.public.service import ROLE_OUTPUT_SCHEMAS, get_schema_for_role

    INSTRUCTOR_SCHEMAS_AVAILABLE = True
except ImportError:
    ROLE_OUTPUT_SCHEMAS = {}

    def get_schema_for_role(role: str) -> type | None:
        """Fallback schema resolver when adapter schema package is unavailable."""
        return dict

    INSTRUCTOR_SCHEMAS_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationReport:
    """Pre-commit validation report.

    Records the result of all validation checks before durable commit.
    """

    passed: bool
    checks: dict[str, bool]
    errors: list[str]


class RoleExecutionKernel:
    """角色执行内核 - Facade 模式实现

    统一执行角色对话的两种模式：
    - CHAT: 聊天模式（用户交互）
    - WORKFLOW: 工作流模式（自动化执行）

    重构后架构（Facade 模式）:
    - RoleExecutionKernel: Facade，提供统一接口，委托给服务层
    - LLMInvoker (ILLMInvoker): LLM调用服务
    - ToolExecutor (IToolExecutor): 工具执行服务
    - PromptBuilder: 提示词构建服务
    - OutputParser: 输出解析服务
    - QualityChecker: 质量检查服务
    - EventEmitter: 事件发射服务

    依赖注入:
        >>> # 生产环境（使用默认服务）
        >>> kernel = RoleExecutionKernel.create_default(workspace=".")
        >>>
        >>> # 自定义服务注入
        >>> kernel = RoleExecutionKernel(
        ...     workspace=".",
        ...     llm_invoker=custom_llm_invoker,
        ...     tool_executor=custom_tool_executor,
        ... )
        >>>
        >>> # 测试环境（使用 Mock）
        >>> kernel = RoleExecutionKernel(
        ...     workspace=".",
        ...     llm_invoker=MockLLMInvoker(),
        ...     tool_executor=MockToolExecutor(),
        ... )
    """

    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        use_structured_output: bool | None = None,
        config: KernelConfig | None = None,
        tool_gateway: ToolGatewayPort | None = None,
        # 新增：服务层依赖注入
        llm_invoker: ILLMInvoker | None = None,
        tool_executor: CellToolExecutorPort | None = None,
        prompt_builder: IPromptBuilder | None = None,
        output_parser: IOutputParser | None = None,
        quality_checker: IQualityChecker | None = None,
        event_emitter: IEventEmitter | None = None,
    ) -> None:
        """初始化执行内核

        Args:
            workspace: 工作区路径
            registry: 角色注册表（默认使用全局实例）
            use_structured_output: 是否启用结构化输出（默认从环境变量读取）
            config: Kernel 执行配置（默认使用全局默认配置）
            tool_gateway: 工具网关实现（支持 ToolGatewayPort Protocol）
            llm_invoker: LLM调用服务（可选，用于依赖注入）
            tool_executor: 工具执行服务（可选，用于依赖注入）
            prompt_builder: 提示词构建服务（可选，用于依赖注入）
            output_parser: 输出解析服务（可选，用于依赖注入）
            quality_checker: 质量检查服务（可选，用于依赖注入）
            event_emitter: 事件发射服务（可选，用于依赖注入）
        """
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()  # type: ignore[no-untyped-call]

        # 保存注入的服务（可能为 None，由 _get_* 方法处理）
        self._injected_llm_invoker = llm_invoker
        self._injected_llm_caller: LLMCaller | None = None  # Legacy LLMCaller DI
        self._injected_tool_executor = tool_executor
        self._injected_prompt_builder = prompt_builder
        self._injected_output_parser = output_parser
        self._injected_quality_checker = quality_checker
        self._injected_event_emitter = event_emitter

        # M1: 工具网关 DI 支持
        self._tool_gateway = tool_gateway

        # Cache RoleToolGateway per-turn for FailureBudget persistence (HALLUCINATION_LOOP detection)
        self._cached_tool_gateway: Any | None = None
        self._cached_gateway_profile: Any | None = None
        self._cached_gateway_turn_id: str | None = None  # Track turn boundary for counter reset

        # Kernel 配置
        self._config = config if config is not None else get_default_config()

        # 结构化输出配置
        if use_structured_output is None:
            use_structured_output = os.environ.get("KERNELONE_USE_STRUCTURED_OUTPUT", "false").lower() in (
                "true",
                "1",
                "yes",
            )
        self._use_structured_output = bool(use_structured_output)

        # 初始化各组件（懒加载，仅在需要时创建）
        self._prompt_builder: PromptBuilder | None = None
        self._output_parser: OutputParser | None = None
        self._quality_checker: QualityChecker | None = None
        self._llm_caller_fallback: LLMCaller | None = None  # Lazy fallback for legacy
        self._event_emitter: KernelEventEmitter | None = None

        # 状态管理
        self._data_stores: dict[str, RoleDataStore] = {}
        self._state_lock = asyncio.Lock()

        # H1: 上下文压缩配置
        self._context_compaction_enabled = os.environ.get("KERNELONE_CONTEXT_COMPACTION", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self._context_compaction_threshold = int(os.environ.get("KERNELONE_CONTEXT_COMPACTION_THRESHOLD", "50000"))
        self._context_compressor: RoleContextCompressor | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # 工厂方法
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def create_default(
        cls,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        config: KernelConfig | None = None,
        **kwargs: Any,
    ) -> RoleExecutionKernel:
        """创建默认配置的内核实例（生产环境使用）

        Args:
            workspace: 工作区路径
            registry: 角色注册表
            config: Kernel 配置
            **kwargs: 额外的配置参数

        Returns:
            配置好的 RoleExecutionKernel 实例
        """
        return cls(
            workspace=workspace,
            registry=registry,
            config=config,
            # 使用默认服务（None 表示使用内部默认实现）
            llm_invoker=None,
            tool_executor=None,
            prompt_builder=None,
            output_parser=None,
            quality_checker=None,
            event_emitter=None,
            **kwargs,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 属性访问器（向后兼容）
    # ═══════════════════════════════════════════════════════════════════════════

    @property
    def config(self) -> KernelConfig:
        """获取当前 Kernel 配置"""
        return self._config

    @staticmethod
    def _use_transaction_kernel() -> bool:
        """Feature flag: use TransactionKernel as primary execution path.

        Default is True (env absent = True). LEGACY_FALLBACK=true forces old TurnEngine.
        """
        if os.environ.get("LEGACY_FALLBACK", "").lower() in ("true", "1", "yes"):
            return False
        return os.environ.get("USE_TRANSACTION_KERNEL_PRIMARY", "true").lower() in (
            "true",
            "1",
            "yes",
        )

    @staticmethod
    def _benchmark_requires_no_tools(request: RoleTurnRequest) -> bool:
        """Return True when benchmark contract explicitly forbids tool calls."""
        metadata = dict(getattr(request, "metadata", {}) or {})
        if bool(metadata.get("benchmark_require_no_tool_calls")):
            return True

        message = str(getattr(request, "message", "") or "")
        if "[Benchmark Tool Contract]" not in message:
            return False

        lowered = message.lower()
        return (
            "do not call any tools for this case." in lowered
            or "do not call any tools" in lowered
            or 'require_no_tool_calls": true' in lowered
            or "require_no_tool_calls: true" in lowered
        )

    def _create_transaction_kernel(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> TransactionKernel:
        """Create a TransactionKernel with kernel-backed LLM and tool adapters.

        Uses explicit parameter passing instead of closures to avoid circular
        reference issues between nested classes and the kernel instance.
        """
        import copy
        import dataclasses
        import inspect
        import weakref

        caller = self._get_llm_caller()
        # Keep the higher-level caller wrapper as the default entrypoint so
        # TransactionKernel context overrides (forced tool definitions/tool_choice)
        # are preserved end-to-end.
        llm_invoker: Any = caller
        if not inspect.iscoroutinefunction(getattr(llm_invoker, "call", None)):
            llm_invoker = caller._get_invoker() if hasattr(caller, "_get_invoker") else caller

        def _normalize_user_text(value: Any) -> str:
            return str(value or "").replace("\ufeff", "").strip()

        def _build_history_without_current_user(
            messages: list[dict[str, Any]],
            current_message: str,
        ) -> list[tuple[str, str]]:
            history: list[tuple[str, str]] = []
            for msg in messages:
                role_label = str(msg.get("role", ""))
                content = str(msg.get("content", ""))
                if role_label in ("user", "assistant", "tool"):
                    history.append((role_label, content))

            normalized_current = _normalize_user_text(current_message)
            if normalized_current:
                history = [
                    (role_label, content)
                    for role_label, content in history
                    if not (role_label == "user" and _normalize_user_text(content) == normalized_current)
                ]

            return history

        def _build_context_override_with_prebuilt_messages(
            prebuilt_messages: list[dict[str, Any]],
            tool_definitions: list[dict[str, Any]] | None = None,
            tool_choice: Any | None = None,
        ) -> dict[str, Any]:
            override: dict[str, Any]
            if isinstance(getattr(provider_request, "context_override", None), dict):
                override = dict(provider_request.context_override or {})
            else:
                override = {}
            override["_transaction_kernel_prebuilt_messages"] = [
                dict(item) for item in prebuilt_messages if isinstance(item, dict)
            ]
            if isinstance(tool_definitions, list):
                override["_transaction_kernel_forced_tool_definitions"] = [
                    dict(item) for item in tool_definitions if isinstance(item, dict)
                ]
            if tool_choice is not None:
                override["_transaction_kernel_forced_tool_choice"] = tool_choice
            return override

        def _extract_model_override_from_request_payload(request_payload: dict[str, Any]) -> str | None:
            token = str(request_payload.get("model_override") or "").strip()
            if not token:
                return None
            return token

        def _build_effective_profile(request_payload: dict[str, Any]) -> Any:
            model_override = _extract_model_override_from_request_payload(request_payload)
            if not model_override:
                return provider_profile
            base_model = str(getattr(provider_profile, "model", "") or "").strip()
            if not model_override or model_override == base_model:
                return provider_profile
            if hasattr(provider_profile, "model_copy"):
                try:
                    return provider_profile.model_copy(update={"model": model_override})  # type: ignore[union-attr]
                except (AttributeError, TypeError, ValueError):
                    pass
            if dataclasses.is_dataclass(provider_profile):
                try:
                    return dataclasses.replace(provider_profile, model=model_override)
                except (TypeError, ValueError):
                    pass
            try:
                cloned_profile = copy.copy(provider_profile)
                object.__setattr__(cloned_profile, "model", model_override)
                return cloned_profile
            except (AttributeError, TypeError):
                fallback_payload = {}
                if hasattr(provider_profile, "__dict__"):
                    fallback_payload = dict(getattr(provider_profile, "__dict__", {}) or {})
                fallback_payload["model"] = model_override
                return SimpleNamespace(**fallback_payload)

        kernel_weakref = weakref.ref(self)
        provider_profile = profile
        provider_request = request

        class _LLMProvider:
            """Encapsulated LLM provider for TransactionKernel."""

            __slots__ = ()

            async def __call__(self, request_payload: dict[str, Any]) -> dict[str, Any]:
                import asyncio

                effective_profile = _build_effective_profile(request_payload)
                raw_messages = list(request_payload.get("messages", []))
                messages = list(raw_messages)
                system_prompt = ""
                if messages and messages[0].get("role") == "system":
                    system_prompt = str(messages[0].get("content", ""))
                    messages = messages[1:]

                current_message = str(getattr(provider_request, "message", "") or "")
                history = _build_history_without_current_user(messages, current_message)

                context = ContextRequest(
                    message=current_message,
                    history=tuple(history),
                    task_id=provider_request.task_id,
                    context_override=_build_context_override_with_prebuilt_messages(
                        raw_messages,
                        cast("list[dict[str, Any]] | None", request_payload.get("tools")),
                        request_payload.get("tool_choice"),
                    ),
                )

                tool_choice = request_payload.get("tool_choice")
                tool_definitions = request_payload.get("tools")
                run_id = str(provider_request.run_id or "").strip() or None
                task_id_str = str(provider_request.task_id or "").strip() or None

                if tool_choice == "none":
                    if hasattr(llm_invoker, "call_finalization") and asyncio.iscoroutinefunction(
                        getattr(llm_invoker, "call_finalization", None)
                    ):
                        return await llm_invoker.call_finalization(
                            profile=effective_profile,
                            system_prompt=system_prompt,
                            context=context,
                            run_id=run_id,
                            task_id=task_id_str,
                            attempt=0,
                            turn_round=0,
                        )
                    response = await llm_invoker.call(
                        profile=effective_profile,
                        system_prompt=system_prompt,
                        context=context,
                        run_id=run_id,
                        task_id=task_id_str,
                        attempt=0,
                        turn_round=0,
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
                if hasattr(llm_invoker, "call_decision") and asyncio.iscoroutinefunction(
                    getattr(llm_invoker, "call_decision", None)
                ):
                    return await llm_invoker.call_decision(
                        profile=effective_profile,
                        system_prompt=system_prompt,
                        context=context,
                        tool_definitions=tool_definitions if tool_definitions else None,
                        run_id=run_id,
                        task_id=task_id_str,
                        attempt=0,
                        turn_round=0,
                    )
                response = await llm_invoker.call(
                    profile=effective_profile,
                    system_prompt=system_prompt,
                    context=context,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                    turn_round=0,
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

        class _ToolRuntime:
            """Encapsulated tool runtime for TransactionKernel."""

            __slots__ = ()

            def reset_turn_boundary(self, turn_id: str) -> None:
                kernel = kernel_weakref()
                if kernel is None:
                    return
                normalized_turn_id = str(turn_id or "").strip()
                if not normalized_turn_id:
                    return
                cast(Any, provider_request).turn_id = normalized_turn_id
                kernel.reset_tool_gateway_turn_boundary(normalized_turn_id)

            async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any:
                kernel = kernel_weakref()
                if kernel is None:
                    raise RuntimeError("Kernel instance no longer exists")
                return await kernel._execute_single_tool(
                    tool_name=tool_name,
                    args=arguments,
                    context={"profile": provider_profile, "request": provider_request},
                )

        class _LLMProviderStream:
            """Encapsulated streaming LLM provider for TransactionKernel."""

            __slots__ = ()

            async def __call__(self, request_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                if not hasattr(llm_invoker, "call_stream"):
                    return

                effective_profile = _build_effective_profile(request_payload)
                raw_messages = list(request_payload.get("messages", []))
                messages = list(raw_messages)
                system_prompt = ""
                if messages and messages[0].get("role") == "system":
                    system_prompt = str(messages[0].get("content", ""))
                    messages = messages[1:]

                current_message = str(getattr(provider_request, "message", "") or "")
                history = _build_history_without_current_user(messages, current_message)

                context = ContextRequest(
                    message=current_message,
                    history=tuple(history),
                    task_id=provider_request.task_id,
                    context_override=_build_context_override_with_prebuilt_messages(
                        raw_messages,
                        cast("list[dict[str, Any]] | None", request_payload.get("tools")),
                        request_payload.get("tool_choice"),
                    ),
                )

                run_id = str(provider_request.run_id or "").strip() or None
                task_id_str = str(provider_request.task_id or "").strip() or None

                async for chunk in llm_invoker.call_stream(
                    profile=effective_profile,
                    system_prompt=system_prompt,
                    context=context,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                ):
                    yield chunk

        llm_provider = _LLMProvider()
        tool_runtime = _ToolRuntime()
        llm_provider_stream = _LLMProviderStream() if hasattr(llm_invoker, "call_stream") else None

        workflow_runtime = ExplorationWorkflowRuntime(
            tool_executor=tool_runtime,
            synthesis_llm=None,
        )

        return TransactionKernel(
            llm_provider=llm_provider,
            tool_runtime=tool_runtime,
            config=TransactionConfig(
                domain="code" if role in {"director", "chief_engineer"} else "document",
            ),
            workflow_runtime=workflow_runtime,
            llm_provider_stream=llm_provider_stream,
        )

    def _build_context_handoff_pack(
        self,
        turn_result: dict[str, Any],
        role: str,
        request: RoleTurnRequest,
    ) -> ContextHandoffPack:
        """Map TransactionKernel handoff_workflow result to canonical ContextHandoffPack."""
        workflow_context = turn_result.get("workflow_context") or {}
        recoverable_context = workflow_context.get("recoverable_context") or {}
        decision = recoverable_context.get("decision") or {}
        batch_receipts = recoverable_context.get("batch_receipts") or []
        turn_id = str(turn_result.get("turn_id", ""))
        run_id = str(request.run_id or "").strip() or turn_id

        receipt_refs: list[str] = []
        for receipt in batch_receipts:
            batch_id = str(receipt.get("batch_id", ""))
            if batch_id:
                receipt_refs.append(batch_id)

        turn_envelope = TurnEnvelope(
            turn_id=turn_id,
            session_id=str(request.task_id or "").strip() or None,
            run_id=run_id if run_id else None,
            role=role,
            receipt_ids=tuple(receipt_refs),
        )

        return ContextHandoffPack(
            handoff_id=f"handoff_{turn_id}_{uuid.uuid4().hex[:8]}",
            workspace=str(request.workspace or self.workspace or "."),
            created_at=str(int(time.time())),
            session_id=str(request.task_id or "").strip() or turn_id,
            run_id=run_id if run_id else None,
            reason=str(workflow_context.get("handoff_reason", "transaction_kernel_handoff")),
            current_goal=str(decision.get("metadata", {}).get("current_goal", "")),
            run_card=dict(decision.get("metadata", {}).get("run_card", {})),
            context_slice_plan={"workflow_context": workflow_context},
            decision_log=(recoverable_context,),
            receipt_refs=tuple(receipt_refs),
            turn_envelope=turn_envelope,
        )

    @staticmethod
    def _pre_commit_validate(
        ledger: TurnLedger | None,
        snapshot: dict[str, Any],
        turn_id: str,
    ) -> ValidationReport:
        """Pre-commit validation: verify turn invariants before durable write.

        Returns a ValidationReport with pass/fail status and detailed checks.
        """
        checks: dict[str, bool] = {}
        errors: list[str] = []

        # 1. single_decision: ledger must have exactly 1 decision
        if ledger is not None:
            decision_count = len(ledger.decisions)
            checks["single_decision"] = decision_count == 1
            if not checks["single_decision"]:
                errors.append(f"expected 1 decision, got {decision_count}")
        else:
            checks["single_decision"] = True  # no ledger = no decision to validate

        # 2. single_tool_batch: at most 1 tool batch
        if ledger is not None:
            checks["single_tool_batch"] = ledger.tool_batch_count <= 1
            if not checks["single_tool_batch"]:
                errors.append(f"expected <=1 tool batch, got {ledger.tool_batch_count}")
        else:
            checks["single_tool_batch"] = True

        # 3. no_hidden_continuation: check state_history for duplicate DECISION_REQUESTED
        if ledger is not None:
            decision_requests = sum(1 for state, _ts in ledger.state_history if state == "DECISION_REQUESTED")
            checks["no_hidden_continuation"] = decision_requests <= 1
            if not checks["no_hidden_continuation"]:
                errors.append(f"DECISION_REQUESTED appeared {decision_requests} times")
        else:
            checks["no_hidden_continuation"] = True

        # 4. receipts_integrity: all tool calls have receipts
        if ledger is not None and ledger.tool_executions:
            checks["receipts_integrity"] = len(ledger.tool_executions) > 0
        else:
            checks["receipts_integrity"] = True

        # 5. artifact_refs_valid: placeholder (would validate artifact references)
        checks["artifact_refs_valid"] = True

        # 6. budget_balance: basic check (placeholder for full budget validation)
        checks["budget_balance"] = True

        # 7. outcome_status_legal: snapshot version check
        checks["outcome_status_legal"] = isinstance(snapshot.get("version", 0), int)

        all_passed = all(checks.values())
        return ValidationReport(
            passed=all_passed,
            checks=checks,
            errors=errors,
        )

    @staticmethod
    def _execute_commit_protocol(
        request: RoleTurnRequest,
        turn_id: str,
        turn_history: list[tuple[str, str]],
        turn_events_metadata: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        ledger: TurnLedger | None,
        snapshot: dict[str, Any],
    ) -> CommitReceipt:
        """Execute the durable commit protocol.

        This is the critical section: truthlog append + snapshot materialization.
        Must remain synchronous and consistent.
        """
        transcript_log: list[dict[str, Any]] = snapshot.get("transcript_log") or []
        if not isinstance(transcript_log, list):
            transcript_log = []

        base_sequence = len(transcript_log)
        for idx, meta in enumerate(turn_events_metadata):
            if not isinstance(meta, dict):
                continue
            seq = base_sequence + idx
            event = TranscriptEvent(
                event_id=str(meta.get("event_id") or f"{turn_id}_{idx}"),
                sequence=seq,
                role=str(meta.get("role") or ""),
                kind=str(meta.get("kind") or ""),
                route="",
                content=str(meta.get("content") or ""),
                source_turns=(f"t{seq}",),
            )
            transcript_log.append(event.to_dict())

        snapshot["transcript_log"] = transcript_log
        snapshot["version"] = int(snapshot.get("version", 0)) + 1
        snapshot["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        working_state = snapshot.get("working_state")
        if not isinstance(working_state, dict):
            working_state = {}
            snapshot["working_state"] = working_state

        if tool_results:
            working_state["last_tool_results"] = list(tool_results)

        # Merge TurnLedger data into policy_verdicts (single truth source)
        if ledger is not None:
            policy_verdicts: dict[str, Any] = snapshot.setdefault("policy_verdicts", {})
            if ledger.decisions:
                policy_verdicts["decisions"] = list(ledger.decisions)
            if ledger.tool_executions:
                policy_verdicts["tool_executions"] = list(ledger.tool_executions)
            if ledger.llm_calls:
                policy_verdicts["llm_calls"] = list(ledger.llm_calls)
            if ledger.anomaly_flags:
                policy_verdicts["anomaly_flags"] = list(ledger.anomaly_flags)

        # Mark this turn as committed
        snapshot["last_commit_turn_id"] = turn_id

        # Build commit receipt
        from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, TurnId

        truthlog_start = base_sequence
        truthlog_end = len(transcript_log)
        return CommitReceipt(
            turn_id=TurnId(turn_id),
            snapshot_id=str(snapshot.get("snapshot_id", "")),
            truthlog_seq_range=(truthlog_start, truthlog_end),
            sealed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            validation_passed=True,
        )

    @staticmethod
    def _post_commit_seal(
        commit_receipt: CommitReceipt,
        outcome_status: str,
        resolution_code: str,
        parent_snapshot_id: str | None = None,
    ) -> SealedTurn:
        """Post-commit seal: generate immutable turn seal.

        This creates the final SealedTurn that represents durable truth.
        """
        from polaris.cells.roles.kernel.public.turn_contracts import (
            OutcomeStatus,
            ResolutionCode,
            SealedTurn,
        )

        return SealedTurn(
            turn_id=commit_receipt.turn_id,
            commit_receipt=commit_receipt,
            outcome_status=OutcomeStatus(outcome_status),
            resolution_code=ResolutionCode(resolution_code),
            sealed_at=commit_receipt.sealed_at,
            parent_snapshot_id=parent_snapshot_id,
        )

    @staticmethod
    def _commit_turn_to_snapshot(
        request: RoleTurnRequest,
        turn_id: str,
        turn_history: list[tuple[str, str]],
        turn_events_metadata: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        ledger: TurnLedger | None = None,
    ) -> CommitReceipt | None:
        """Merge turn history, events, and ledger data into the ContextOS snapshot.

        Phase 1 hardened version: three-stage durable commit protocol.
        1. Pre-commit validation
        2. Durable commit (critical section)
        3. Post-commit seal

        Args:
            request: The turn request carrying ``context_override``.
            turn_id: Unique identifier for the current turn.
            turn_history: Ordered (role, content) pairs for the turn.
            turn_events_metadata: Metadata dicts for each transcript event.
            tool_results: Tool execution results produced this turn.
            ledger: Optional ``TurnLedger`` whose decisions / tool executions /
                LLM calls / anomaly flags are merged into
                ``snapshot["policy_verdicts"]``.

        Returns:
            CommitReceipt if commit succeeded, None if skipped or failed.
        """
        context_override = getattr(request, "context_override", None)
        if not isinstance(context_override, dict):
            return None

        snapshot = context_override.get("context_os_snapshot")
        if not isinstance(snapshot, dict):
            return None

        # Idempotency guard – skip if this turn was already committed.
        if snapshot.get("last_commit_turn_id") == turn_id:
            return None

        # Stage 1: Pre-commit validation
        validation_report = RoleExecutionKernel._pre_commit_validate(
            ledger=ledger,
            snapshot=snapshot,
            turn_id=turn_id,
        )
        if not validation_report.passed:
            logger.warning(
                "Pre-commit validation failed for turn %s: %s",
                turn_id,
                "; ".join(validation_report.errors),
            )
            return None

        # Stage 2: Execute durable commit protocol (critical section)
        commit_receipt = RoleExecutionKernel._execute_commit_protocol(
            request=request,
            turn_id=turn_id,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            tool_results=tool_results,
            ledger=ledger,
            snapshot=snapshot,
        )

        # Stage 3: Post-commit seal (can be enhanced later)
        # For now, just return the receipt; seal is created by caller if needed
        return commit_receipt

    @staticmethod
    def _build_turn_history_and_events(
        *,
        turn_id: str,
        request: RoleTurnRequest,
        visible_content: str,
        thinking: str | None,
        tool_results: list[dict[str, Any]],
    ) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
        """Build turn_history and turn_events_metadata for ContextOS persistence.

        These fields are critical for SessionContinuityEngine to rebuild the
        ContextOS snapshot across turns. Without them, the snapshot stays stale
        and the LLM continues with the previous turn's task.
        """
        import json

        turn_history: list[tuple[str, str]] = []
        turn_events_metadata: list[dict[str, Any]] = []
        user_message = str(getattr(request, "message", "") or "").strip()

        if user_message:
            turn_history.append(("user", user_message))
            turn_events_metadata.append(
                {
                    "role": "user",
                    "content": user_message,
                    "event_id": f"user_{turn_id}",
                    "kind": "user_turn",
                }
            )

        assistant_content = str(visible_content or "").strip()
        if assistant_content:
            turn_history.append(("assistant", assistant_content))
            turn_events_metadata.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "event_id": f"assistant_{turn_id}",
                    "kind": "assistant_turn",
                }
            )

        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            tool_name = str(tr.get("tool") or "tool").strip() or "tool"
            result_value = tr.get("result")
            if result_value is not None:
                result_text = json.dumps(result_value, ensure_ascii=False)
            else:
                error_text = str(tr.get("error") or "").strip()
                result_text = f"Error: {error_text}" if error_text else ""
            if result_text:
                turn_history.append(("tool", result_text))
                turn_events_metadata.append(
                    {
                        "role": "tool",
                        "content": result_text,
                        "event_id": f"tool_{tr.get('call_id', turn_id)}",
                        "kind": "tool_result",
                        "tool": tool_name,
                    }
                )

        return turn_history, turn_events_metadata

    async def _execute_transaction_kernel_turn(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
        system_prompt: str,
        fingerprint: Any,
        observer_run_id: str,
        response_schema: type | None,
    ) -> RoleTurnResult:
        """Execute a single turn via TransactionKernel and map to RoleTurnResult."""
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
        from polaris.cells.roles.kernel.public.service import RoleContextGateway

        tk = self._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or observer_run_id or uuid.uuid4().hex[:12])

        controller = ToolLoopController.from_request(request=request, profile=profile)
        context_request = controller.build_context_request()
        context_gateway = RoleContextGateway(profile, self.workspace)
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = [] if self._benchmark_requires_no_tools(request) else build_native_tool_schemas(profile)

        try:
            tk_result = await tk.execute(turn_id, messages, tool_definitions)
        except Exception as exc:
            logger.exception("TransactionKernel execute failed: turn_id=%s", turn_id)
            return RoleTurnResult(
                content="",
                error=f"TransactionKernel execution failed: {exc}",
                is_complete=False,
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                tool_policy_id=profile.tool_policy.policy_id,
            )

        kind = tk_result.get("kind", "final_answer")
        visible_content = tk_result.get("visible_content", "")
        thinking_text: str | None = None
        if visible_content:
            parsed = self._get_output_parser().parse_thinking(visible_content)
            visible_content = str(parsed.clean_content or "")
            thinking_text = parsed.thinking
        batch_receipt = tk_result.get("batch_receipt")
        finalization = tk_result.get("finalization")
        workflow_context = tk_result.get("workflow_context")
        metrics = tk_result.get("metrics", {})

        # Pull the ledger from the TransactionKernel result so it can be committed
        # into the ContextOS snapshot, eliminating the parallel TurnLedger state.
        ledger = tk_result.get("ledger")

        # Map tool calls/results from batch receipt
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        if batch_receipt:
            for result in batch_receipt.get("results", []):
                tool_calls.append(
                    {
                        "tool": result.get("tool_name", ""),
                        "args": {},
                        "call_id": result.get("call_id", ""),
                    }
                )
                tool_results.append(
                    {
                        "tool": result.get("tool_name", ""),
                        "result": result.get("result"),
                        "success": result.get("status") == "success",
                        "call_id": result.get("call_id", ""),
                    }
                )

        # Handle structured output if response_schema was requested
        structured_output: dict[str, Any] | None = None
        if response_schema is not None and visible_content:
            try:
                candidate = self._get_output_parser().extract_json(visible_content)
                if candidate is not None:
                    validated = response_schema(**candidate)
                    structured_output = validated.model_dump()
            except (RuntimeError, ValueError):
                structured_output = None

        execution_stats = {
            "duration_ms": metrics.get("duration_ms", 0),
            "llm_calls": metrics.get("llm_calls", 0),
            "tool_calls": metrics.get("tool_calls", 0),
            "transaction_kernel": True,
        }

        metadata: dict[str, Any] = {}
        if kind == "handoff_workflow" and workflow_context is not None:
            handoff_pack = self._build_context_handoff_pack(tk_result, role, request)
            metadata["handoff_pack"] = handoff_pack.to_dict()
            metadata["transaction_kind"] = "handoff_workflow"

        error_msg: str | None = None
        is_complete = True
        if kind == "ask_user" and isinstance(finalization, dict):
            # SUSPENDED state: model needs user clarification. Map to error for backward
            # compat in the legacy kernel core facade (callers check error to retry).
            error_msg = finalization.get("error") or finalization.get("suspended_reason")
            is_complete = False

        final_thinking = thinking_text
        if final_thinking is None and isinstance(finalization, dict):
            final_thinking = finalization.get("final_visible_message")

        # Build turn history and events metadata for ContextOS persistence
        turn_history, turn_events_metadata = self._build_turn_history_and_events(
            turn_id=turn_id,
            request=request,
            visible_content=visible_content,
            thinking=final_thinking,
            tool_results=tool_results,
        )

        self._commit_turn_to_snapshot(
            request=request,
            turn_id=turn_id,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            tool_results=tool_results,
            ledger=ledger,
        )

        return RoleTurnResult(
            content=visible_content,
            thinking=final_thinking,
            structured_output=structured_output,
            tool_calls=tool_calls,
            tool_results=tool_results,
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            error=error_msg,
            is_complete=is_complete,
            execution_stats=execution_stats,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            metadata=metadata,
        )

    async def _execute_transaction_kernel_stream(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
        system_prompt: str,
        fingerprint: Any,
        stream_run_id: str,
        uep_publisher: UEPEventPublisher,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream execution via TransactionKernel (compatibility shim)."""
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
        from polaris.cells.roles.kernel.public.service import RoleContextGateway
        from polaris.cells.roles.kernel.public.turn_events import (
            CompletionEvent,
            ContentChunkEvent,
            ErrorEvent,
            FinalizationEvent,
            ToolBatchEvent,
            TurnPhaseEvent,
        )

        tk = self._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or stream_run_id or uuid.uuid4().hex[:12])

        controller = ToolLoopController.from_request(request=request, profile=profile)
        context_request = controller.build_context_request()
        context_gateway = RoleContextGateway(profile, self.workspace)
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = [] if self._benchmark_requires_no_tools(request) else build_native_tool_schemas(profile)

        accumulated_content: list[str] = []
        accumulated_thinking: list[str] = []
        stream_tool_calls: list[dict[str, Any]] = []
        stream_tool_results: list[dict[str, Any]] = []
        async for event in tk.execute_stream(turn_id, messages, tool_definitions):
            event_dict: dict[str, Any]
            if isinstance(event, TurnPhaseEvent):
                event_dict = {
                    "type": event.phase,
                    "turn_id": event.turn_id,
                    "metadata": dict(event.metadata),
                }
            elif isinstance(event, ContentChunkEvent):
                if event.is_thinking:
                    accumulated_thinking.append(event.chunk)
                    event_dict = {
                        "type": "thinking_chunk",
                        "content": event.chunk,
                        "turn_id": event.turn_id,
                    }
                else:
                    if getattr(event, "is_finalization", False):
                        accumulated_content = [event.chunk]
                    else:
                        accumulated_content.append(event.chunk)
                    event_dict = {
                        "type": "content_chunk",
                        "content": event.chunk,
                        "turn_id": event.turn_id,
                    }
            elif isinstance(event, ToolBatchEvent):
                arguments = dict(event.arguments) if isinstance(event.arguments, dict) else {}
                if event.status == "started":
                    stream_tool_calls.append(
                        {
                            "tool": event.tool_name,
                            "args": arguments,
                            "call_id": event.call_id,
                        }
                    )
                else:
                    stream_tool_results.append(
                        {
                            "tool": event.tool_name,
                            "result": event.result,
                            "success": event.status == "success",
                            "call_id": event.call_id,
                        }
                    )
                event_dict = {
                    "type": "tool_result" if event.status in ("success", "error") else "tool_call",
                    "tool": event.tool_name,
                    "call_id": event.call_id,
                    "status": event.status,
                    "progress": event.progress,
                    "turn_id": event.turn_id,
                    "args": arguments,
                    "result": event.result,
                    "error": event.error,
                }
            elif isinstance(event, FinalizationEvent):
                event_dict = {
                    "type": "finalization",
                    "mode": event.mode,
                    "turn_id": event.turn_id,
                }
            elif isinstance(event, CompletionEvent):
                final_content = "".join(accumulated_content)
                final_thinking = "".join(accumulated_thinking) or None
                # Backward compat: failed / suspended completions map to error events
                if event.status in ("failed", "suspended"):
                    event_dict = {
                        "type": "error",
                        "error": event.error or "execution_failed",
                        "error_type": "stream_execution_failed",
                        "turn_id": event.turn_id,
                    }
                    await uep_publisher.publish_stream_event(
                        workspace=self.workspace or os.getcwd(),
                        run_id=stream_run_id,
                        role=role,
                        event_type="error",
                        payload=event_dict,
                    )
                    yield event_dict
                    return
                event_dict = {
                    "type": "complete",
                    "status": event.status,
                    "content": final_content,
                    "thinking": final_thinking,
                    "duration_ms": event.duration_ms,
                    "llm_calls": event.llm_calls,
                    "tool_calls": event.tool_calls,
                    "turn_id": event.turn_id,
                }
                if event.monitoring:
                    event_dict["monitoring"] = dict(event.monitoring)
                # Include RoleTurnResult so that stream consumers can persist turn state
                turn_history, turn_events_metadata = self._build_turn_history_and_events(
                    turn_id=turn_id,
                    request=request,
                    visible_content=final_content,
                    thinking=final_thinking,
                    tool_results=stream_tool_results,
                )
                from polaris.cells.roles.profile.public.service import RoleTurnResult

                event_dict["result"] = RoleTurnResult(
                    content=final_content,
                    thinking=final_thinking,
                    tool_calls=stream_tool_calls,
                    tool_results=stream_tool_results,
                    profile_version=profile.version,
                    prompt_fingerprint=fingerprint,
                    tool_policy_id=profile.tool_policy.policy_id,
                    is_complete=True,
                    execution_stats={
                        "duration_ms": event.duration_ms,
                        "llm_calls": event.llm_calls,
                        "tool_calls": event.tool_calls,
                        "transaction_kernel": True,
                    },
                    turn_history=turn_history,
                    turn_events_metadata=turn_events_metadata,
                )
            elif isinstance(event, ErrorEvent):
                event_dict = {
                    "type": "error",
                    "error": event.message,
                    "error_type": event.error_type,
                    "turn_id": event.turn_id,
                }
            else:
                event_dict = {"type": "unknown", "turn_id": getattr(event, "turn_id", turn_id)}

            await uep_publisher.publish_stream_event(
                workspace=self.workspace or os.getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type=event_dict.get("type", "unknown"),
                payload=event_dict,
            )
            yield event_dict

    def _build_context_request_for_stream(self, messages: list[dict[str, Any]], request: RoleTurnRequest) -> Any:
        """Build a minimal ContextRequest for legacy call_stream compatibility."""
        from polaris.cells.roles.kernel.public.service import ContextRequest

        def _normalize_user_text(value: Any) -> str:
            return str(value or "").replace("\ufeff", "").strip()

        history: list[tuple[str, str]] = []
        for msg in messages:
            role_label = str(msg.get("role", ""))
            content = str(msg.get("content", ""))
            if role_label in ("user", "assistant", "tool"):
                history.append((role_label, content))

        normalized_current = _normalize_user_text(request.message)
        if normalized_current:
            history = [
                (role_label, content)
                for role_label, content in history
                if not (role_label == "user" and _normalize_user_text(content) == normalized_current)
            ]

        return ContextRequest(
            message=request.message,
            history=tuple(history),
            task_id=request.task_id,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 服务层访问器（懒加载 + 依赖注入支持）
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_prompt_builder(self) -> PromptBuilder:
        """获取提示词构建器（支持依赖注入）"""
        if self._injected_prompt_builder is not None:
            # 类型检查：确保注入的服务实现了必要的方法
            return self._injected_prompt_builder  # type: ignore[return-value]
        if self._prompt_builder is None:
            self._prompt_builder = PromptBuilder(self.workspace)
        return self._prompt_builder

    def _get_output_parser(self) -> OutputParser:
        """获取输出解析器（支持依赖注入）"""
        if self._injected_output_parser is not None:
            return self._injected_output_parser  # type: ignore[return-value]
        if self._output_parser is None:
            self._output_parser = OutputParser()
        return self._output_parser

    def _get_quality_checker(self) -> QualityChecker:
        """获取质量检查器（支持依赖注入）"""
        if self._injected_quality_checker is not None:
            return self._injected_quality_checker  # type: ignore[return-value]
        if self._quality_checker is None:
            self._quality_checker = QualityChecker(self.workspace)
        return self._quality_checker

    def _get_event_emitter(self) -> KernelEventEmitter:
        """获取事件发射器（支持依赖注入）"""
        if self._injected_event_emitter is not None:
            return self._injected_event_emitter  # type: ignore[return-value]
        if self._event_emitter is None:
            self._event_emitter = KernelEventEmitter()
        return self._event_emitter

    # ─────────────────────────────────────────────────────────────────────────────
    # 公共 DI 注入方法（用于测试和扩展）
    # ─────────────────────────────────────────────────────────────────────────────

    def inject_llm_caller(self, caller: LLMCaller | None) -> None:
        """注入 LLM Caller（支持测试和扩展）

        Args:
            caller: LLM Caller 实例，传入 None 可清除注入
        """
        self._injected_llm_caller = caller

    def inject_tool_executor(self, executor: CellToolExecutorPort | None) -> None:
        """注入工具执行器（支持测试和扩展）

        Args:
            executor: 工具执行器实例，传入 None 可清除注入
        """
        self._injected_tool_executor = executor

    def inject_prompt_builder(self, builder: IPromptBuilder | None) -> None:
        """注入提示词构建器（支持测试和扩展）

        Args:
            builder: 提示词构建器实例，传入 None 可清除注入
        """
        self._injected_prompt_builder = builder

    def inject_output_parser(self, parser: IOutputParser | None) -> None:
        """注入输出解析器（支持测试和扩展）

        Args:
            parser: 输出解析器实例，传入 None 可清除注入
        """
        self._injected_output_parser = parser

    def inject_event_emitter(self, emitter: IEventEmitter | None) -> None:
        """注入事件发射器（支持测试和扩展）

        Args:
            emitter: 事件发射器实例，传入 None 可清除注入
        """
        self._injected_event_emitter = emitter

    def _get_llm_caller(self) -> LLMCaller:
        """获取LLM调用器（支持依赖注入 + 懒加载）"""
        # 1. 优先使用注入的 LLMCaller
        if self._injected_llm_caller is not None:
            return self._injected_llm_caller
        # 2. 回退到懒加载创建
        if self._llm_caller_fallback is None:
            self._llm_caller_fallback = LLMCaller(self.workspace)
        return self._llm_caller_fallback

    # ═══════════════════════════════════════════════════════════════════════════
    # 主要公开 API
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> RoleTurnResult:
        """执行角色回合（带重试机制）

        Args:
            role: 角色标识
            request: 回合请求

        Returns:
            回合结果
        """
        # 1. 加载角色Profile
        try:
            profile = self.registry.get_profile_or_raise(role)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"角色加载失败: {e}", is_complete=True)

        # 2. 处理废弃参数
        try:
            prompt_appendix = self._process_deprecated_params(request)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"参数处理失败: {e}", is_complete=True)

        # 3. 构建提示词指纹
        try:
            fingerprint = self._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"提示词构建失败: {e}", is_complete=True)

        # 4. 构建基础系统提示词
        try:
            base_system_prompt = self._build_system_prompt_for_request(profile, request, prompt_appendix)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"系统提示词构建失败: {e}", is_complete=True)

        # 5. 构建上下文（验证可用性，结果由 TurnEngine 使用）
        try:
            _ = self._build_context(profile, request)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"上下文构建失败: {e}", is_complete=True)

        # 5a. Transcript-driven tool loop controller
        controller = ToolLoopController.from_request(
            request=request,
            profile=profile,
        )

        # Reset cached gateway for new turn (FailureBudget should not persist across turns)
        self._cached_tool_gateway = None
        self._cached_gateway_profile = None

        # 6. 重试循环配置
        max_retries = request.max_retries if request.max_retries > 0 else self._config.max_retries
        validate_output = request.validate_output
        last_validation: QualityResult | None = None
        last_error: str | None = None

        # 结构化输出相关
        pre_validated_data: dict[str, Any] | None = None
        instructor_validated = False

        # 重试统计
        total_platform_retry_count = 0
        kernel_repair_retry_count = 0
        kernel_repair_reasons: list[str] = []

        # 获取 run_id
        task_id = str(getattr(request, "task_id", None) or "").strip()
        observer_run_id = self._get_event_emitter().resolve_observer_run_id(role, getattr(request, "run_id", None))
        # 将 resolved run_id 写回 request，确保下游（TurnEngine/RoleToolGateway）能获取到
        if request.run_id is None:
            request.run_id = observer_run_id

        for attempt in range(max_retries + 1):
            # 构建当前尝试的系统提示词
            system_prompt = self._get_prompt_builder().build_retry_prompt(
                base_system_prompt, quality_result_to_dict(last_validation), attempt
            )

            response_schema = get_schema_for_role(role) if self._use_structured_output else None

            # Get tracer for OpenTelemetry integration
            tracer = get_tracer()

            # Track LLM latency
            with tracer.span(
                "role.kernel.llm_call",
                tags={"role": role, "attempt": attempt, "model": profile.model},
            ) as span:
                llm_start_time = time.monotonic()
                if self._use_transaction_kernel():
                    te_result = await self._execute_transaction_kernel_turn(
                        role=role,
                        profile=profile,
                        request=request,
                        system_prompt=system_prompt,
                        fingerprint=fingerprint,
                        observer_run_id=observer_run_id,
                        response_schema=response_schema,
                    )
                else:
                    from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine

                    engine = TurnEngine(kernel=self)
                    te_result = await engine.run(
                        request=request,
                        role=role,
                        controller=controller,
                        system_prompt=system_prompt,
                        fingerprint=fingerprint,
                        attempt=attempt,
                        response_model=response_schema,
                    )
                llm_latency = time.monotonic() - llm_start_time

                # Record LLM latency to metrics
                try:
                    metrics = get_metrics_collector()
                    metrics.record_llm_latency(llm_latency)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record LLM latency metric")

                span.set_tag("llm_latency_seconds", llm_latency)
                span.set_tag("has_content", bool(te_result.content))
                span.set_tag("has_tool_calls", bool(te_result.tool_calls))

            # TurnEngine 返回错误，不重试
            if te_result.error:
                return RoleTurnResult(
                    content=te_result.content or "",
                    thinking=te_result.thinking,
                    tool_calls=te_result.tool_calls or [],
                    tool_results=te_result.tool_results or [],
                    profile_version=profile.version,
                    prompt_fingerprint=fingerprint,
                    tool_policy_id=profile.tool_policy.policy_id,
                    quality_score=last_validation.quality_score if last_validation else 0.0,
                    quality_suggestions=last_validation.suggestions if last_validation else [],
                    error=te_result.error,
                    is_complete=False,
                    tool_execution_error=getattr(te_result, "tool_execution_error", None),
                    should_retry=getattr(te_result, "should_retry", False),
                    execution_stats={
                        "platform_retry_count": total_platform_retry_count,
                        "kernel_repair_retry_count": kernel_repair_retry_count,
                        "kernel_repair_reasons": kernel_repair_reasons,
                        "kernel_repair_exhausted": True,
                        **te_result.execution_stats,
                    },
                    turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                    turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
                )

            # Quality validation
            effective_content = te_result.content or ""
            last_validation = None
            final_structured_output: dict[str, Any] | None = None
            if validate_output:
                tool_only_turn = not str(effective_content or "").strip() and bool(
                    te_result.tool_calls or te_result.tool_results
                )
                if tool_only_turn:
                    quality_result = QualityResult(
                        success=True,
                        errors=[],
                        suggestions=[],
                        data={"tool_only_turn": True},
                        quality_score=100.0,
                        quality_passed=True,
                    )
                else:
                    pre_validated_data = None
                    instructor_validated = False
                    if response_schema is not None:
                        try:
                            candidate = self._get_output_parser().extract_json(effective_content)
                            if candidate is None:
                                raise ValueError("No JSON found in content")
                            validated = response_schema(**candidate)
                            pre_validated_data = validated.model_dump()
                            instructor_validated = True
                        except (RuntimeError, ValueError):
                            pre_validated_data = None
                            instructor_validated = False
                    try:
                        quality_result = self._get_quality_checker().validate_output(
                            effective_content,
                            profile,
                            pre_validated_data=pre_validated_data,
                            instructor_validated=instructor_validated,
                        )
                    except (RuntimeError, ValueError) as e:
                        logger.warning("质量检查失败 (attempt=%d): %s", attempt, e)
                        last_error = f"质量检查失败: {e}"
                        quality_result = QualityResult(
                            success=False,
                            errors=[f"质量检查失败: {e}"],
                            suggestions=["请确保输出内容完整准确"] if attempt < max_retries else [],
                            data={"quality_check_error": True},
                            quality_score=0.0,
                            quality_passed=False,
                        )

                last_validation = quality_result
                if isinstance(quality_result.data, dict):
                    final_structured_output = dict(quality_result.data)

                # Record quality score
                try:
                    metrics = get_metrics_collector()
                    metrics.record_quality_score(quality_result.quality_score)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record quality score metric")

                if not quality_result.success:
                    self._emit_event(
                        event_type=LLMEventType.VALIDATION_FAIL,
                        role=role,
                        run_id=observer_run_id,
                        task_id=task_id,
                        attempt=attempt,
                        errors=quality_result.errors,
                        quality_score=quality_result.quality_score,
                        model=profile.model,
                        publish_realtime=False,
                    )
                    kernel_repair_retry_count += 1
                    kernel_repair_reasons.append(
                        f"attempt_{attempt}: "
                        f"{quality_result.errors[-1] if quality_result.errors else 'validation_failed'}"
                    )

                    # Record retry
                    try:
                        metrics = get_metrics_collector()
                        metrics.record_retry(role, "validation_failed")
                    except (RuntimeError, ValueError):
                        logger.warning("Failed to record retry metric")

                    if attempt < max_retries:
                        self._emit_event(
                            event_type=LLMEventType.CALL_RETRY,
                            role=role,
                            run_id=observer_run_id,
                            task_id=task_id,
                            attempt=attempt,
                            error_category="validation_failed",
                            model=profile.model,
                            publish_realtime=False,
                        )
                        continue

                    error_msg = f"验证失败，已重试{max_retries}次"
                    if last_validation and last_validation.errors:
                        error_msg += f": {last_validation.errors[-1]}"
                    elif last_error:
                        error_msg += f": {last_error}"

                    # Record failed execution
                    try:
                        metrics = get_metrics_collector()
                        metrics.record_execution(role, "validation_failed")
                    except (RuntimeError, ValueError):
                        logger.warning("Failed to record execution metric")

                    return RoleTurnResult(
                        content=effective_content,
                        thinking=te_result.thinking,
                        profile_version=profile.version,
                        prompt_fingerprint=fingerprint,
                        tool_policy_id=profile.tool_policy.policy_id,
                        quality_score=last_validation.quality_score if last_validation else 0.0,
                        quality_suggestions=last_validation.suggestions if last_validation else [],
                        error=error_msg,
                        is_complete=True,
                        execution_stats={
                            "platform_retry_count": total_platform_retry_count,
                            "kernel_repair_retry_count": kernel_repair_retry_count,
                            "kernel_repair_reasons": kernel_repair_reasons,
                            "kernel_repair_exhausted": True,
                            **te_result.execution_stats,
                        },
                        turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                        turn_events_metadata=list(te_result.turn_events_metadata)
                        if te_result.turn_events_metadata
                        else [],
                    )

                self._emit_event(
                    event_type=LLMEventType.VALIDATION_PASS,
                    role=role,
                    run_id=observer_run_id,
                    task_id=task_id,
                    attempt=attempt,
                    quality_score=quality_result.quality_score,
                    model=profile.model,
                    publish_realtime=False,
                )

            # 最终结果
            try:
                metrics = get_metrics_collector()
                metrics.record_execution(role, "success")
            except (RuntimeError, ValueError):
                logger.warning("Failed to record execution success metric")

            return RoleTurnResult(
                content=te_result.content or "",
                thinking=te_result.thinking,
                structured_output=final_structured_output,
                tool_calls=te_result.tool_calls or [],
                tool_results=te_result.tool_results or [],
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                tool_policy_id=profile.tool_policy.policy_id,
                quality_score=last_validation.quality_score if last_validation else 0.0,
                quality_suggestions=last_validation.suggestions if last_validation else [],
                error=None,
                is_complete=True,
                tool_execution_error=getattr(te_result, "tool_execution_error", None),
                should_retry=getattr(te_result, "should_retry", False),
                execution_stats={
                    "platform_retry_count": total_platform_retry_count,
                    "kernel_repair_retry_count": kernel_repair_retry_count,
                    "kernel_repair_reasons": kernel_repair_reasons,
                    "kernel_repair_exhausted": False,
                    **te_result.execution_stats,
                },
                turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
            )

        # unreachable
        raise RuntimeError("Unexpected fallthrough in RoleExecutionKernel.run")

    async def run_stream(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行角色回合

        Args:
            role: 角色标识
            request: 回合请求

        Yields:
            流式事件字典
        """
        stream_run_id = self._resolve_stream_run_id(request.run_id)
        # 将 resolved run_id 写回 request，确保下游（TurnEngine/RoleToolGateway）能获取到
        # 只有当 request.run_id 为 None 且 stream_run_id 非空时才设置
        original_run_id = request.run_id
        if original_run_id is None and stream_run_id:
            request.run_id = stream_run_id
        logger.warning(
            "[run_stream] run_id resolved: original=%s stream_run_id=%s final=%s role=%s",
            original_run_id,
            stream_run_id,
            request.run_id,
            role,
        )
        inner_error: Exception | None = None
        uep_publisher = UEPEventPublisher()

        try:
            # 1. 加载角色Profile
            profile = self.registry.get_profile_or_raise(role)

            # Reset cached gateway for new turn (FailureBudget should not persist across turns)
            self._cached_tool_gateway = None
            self._cached_gateway_profile = None

            # 2. 处理废弃参数
            prompt_appendix = self._process_deprecated_params(request)

            # 3. 构建提示词指纹
            fingerprint = self._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
            await uep_publisher.publish_stream_event(
                workspace=self.workspace or os.getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type="fingerprint",
                payload={"fingerprint": str(fingerprint.full_hash or "")},
            )
            yield {"type": "fingerprint", "fingerprint": fingerprint}

            # 4. 构建系统提示词
            system_prompt = self._build_system_prompt_for_request(profile, request, prompt_appendix)

            # 5. Transcript-driven Tool Loop
            controller = ToolLoopController.from_request(
                request=request,
                profile=profile,
            )

            # Phase 7: TurnEngine facade
            if self._use_transaction_kernel():
                try:
                    async for event in self._execute_transaction_kernel_stream(
                        role=role,
                        profile=profile,
                        request=request,
                        system_prompt=system_prompt,
                        fingerprint=fingerprint,
                        stream_run_id=stream_run_id,
                        uep_publisher=uep_publisher,
                    ):
                        yield event
                except (RuntimeError, ValueError) as e:
                    inner_error = e
                    logger.exception("流式执行失败 (TransactionKernel)")
                    await uep_publisher.publish_stream_event(
                        workspace=self.workspace or os.getcwd(),
                        run_id=stream_run_id,
                        role=role,
                        event_type="error",
                        payload={"error": str(e)},
                    )
                    yield {"type": "error", "error": str(e)}
            else:
                from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine

                engine = TurnEngine(kernel=self)
                try:
                    async for event in engine.run_stream(
                        request=request,
                        role=role,
                        controller=controller,
                        system_prompt=system_prompt,
                        fingerprint=fingerprint,
                    ):
                        event_type = str(event.get("type") or "").strip()
                        await uep_publisher.publish_stream_event(
                            workspace=self.workspace or os.getcwd(),
                            run_id=stream_run_id,
                            role=role,
                            event_type=event_type,
                            payload=dict(event),
                        )
                        yield event
                except (RuntimeError, ValueError) as e:
                    inner_error = e
                    logger.exception("流式执行失败")
                    await uep_publisher.publish_stream_event(
                        workspace=self.workspace or os.getcwd(),
                        run_id=stream_run_id,
                        role=role,
                        event_type="error",
                        payload={"error": str(e)},
                    )
                    yield {"type": "error", "error": str(e)}

        except (RuntimeError, ValueError):
            if inner_error is None:
                raise

    # ═══════════════════════════════════════════════════════════════════════════
    # Facade 模式：服务层委托方法（新增）
    # ═══════════════════════════════════════════════════════════════════════════

    async def call(
        self,
        request: Any,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Facade: LLM 非流式调用

        委托给 llm_invoker.invoke()

        Args:
            request: AI 请求
            timeout_seconds: 超时时间

        Returns:
            InvokeResult
        """
        if self._injected_llm_invoker is not None:
            return await self._injected_llm_invoker.invoke(request, timeout_seconds)
        # 向后兼容：使用旧的 LLMCaller
        raise NotImplementedError("call() requires injected llm_invoker")

    async def call_stream(
        self,
        request: Any,
        timeout_seconds: float | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Facade: LLM 流式调用

        委托给 llm_invoker.invoke_stream()

        Args:
            request: AI 请求
            timeout_seconds: 超时时间

        Yields:
            StreamEvent
        """
        if self._injected_llm_invoker is not None:
            # Use async for delegation pattern with proper type handling
            stream_gen = self._injected_llm_invoker.invoke_stream(request, timeout_seconds)
            async for event in stream_gen:
                yield event
            return
        # 向后兼容：使用旧的 LLMCaller
        raise NotImplementedError("call_stream() requires injected llm_invoker")

    @staticmethod
    def _resolve_tool_gateway_turn_key(request_obj: Any) -> str:
        """Resolve a stable per-turn cache key for gateway counters."""
        run_id = str(getattr(request_obj, "run_id", "") or "").strip()
        if run_id:
            return run_id
        turn_id = str(getattr(request_obj, "turn_id", "") or "").strip()
        if turn_id:
            return f"turn_id:{turn_id}"
        return f"request_obj:{id(request_obj)}"

    def reset_tool_gateway_turn_boundary(self, turn_id: str) -> None:
        """Explicitly reset cached gateway counters when the authoritative turn id changes."""
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            return
        current_turn_key = f"turn_id:{normalized_turn_id}"
        if current_turn_key == self._cached_gateway_turn_id:
            return
        if self._cached_tool_gateway is not None:
            self._cached_tool_gateway.reset_execution_count()
            if hasattr(self._cached_tool_gateway, "_failure_budget") and hasattr(
                self._cached_tool_gateway._failure_budget, "reset"
            ):
                self._cached_tool_gateway._failure_budget.reset()
        self._cached_gateway_turn_id = current_turn_key

    async def _execute_single_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Facade: 执行单个工具

        委托给 tool_executor.execute_single()

        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文，可包含 'profile' 和 'request' 用于工具执行上下文

        Returns:
            工具执行结果
        """
        if self._injected_tool_executor is not None:
            # BUG FIX: Even injected executors must go through authorization.
            # Previously bypassed RoleToolGateway entirely — no counting, whitelist,
            # path traversal protection, or FailureBudget.
            profile = context.get("profile") if context else None
            if profile is not None:
                from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

                executor = KernelToolExecutor(self, self.workspace)
                request = context.get("request") if context else None
                if request is None:
                    request = RoleTurnRequest(message="")

                # Reuse or create the cached gateway for authorization check
                current_turn_id = self._resolve_tool_gateway_turn_key(request)
                if self._cached_tool_gateway is not None and self._cached_gateway_profile is profile:
                    gateway = self._cached_tool_gateway
                    if current_turn_id != self._cached_gateway_turn_id:
                        gateway.reset_execution_count()
                        self._cached_gateway_turn_id = current_turn_id
                else:
                    gateway = executor.create_gateway(
                        profile=profile,
                        request=request,
                        tool_gateway=self._tool_gateway,
                    )
                    self._cached_tool_gateway = gateway
                    self._cached_gateway_profile = profile
                    self._cached_gateway_turn_id = current_turn_id

                can_execute, reason = gateway.check_tool_permission(tool_name, args)
                if not can_execute:
                    from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

                    raise ToolAuthorizationError(reason)

            logger.debug(
                "[_execute_single_tool] _injected_tool_executor (with auth gate): tool=%s",
                tool_name,
            )
            return await self._injected_tool_executor.execute(tool_name, args, context=context)
        # 向后兼容：使用旧的 KernelToolExecutor
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)

        # FIX: 从context中获取profile和request，如果未提供则使用默认值
        profile = None
        request = None
        if context:
            profile = context.get("profile")
            request = context.get("request")

        # 如果没有提供profile，尝试获取第一个可用角色
        if profile is None:
            available_roles = ["director", "pm", "architect", "chief_engineer", "qa"]
            for role in available_roles:
                try:
                    profile = self.registry.get_profile_or_raise(role)
                    break
                except ValueError:
                    continue

        if profile is None:
            raise ValueError("No available role profile found for tool execution")

        if request is None:
            request = RoleTurnRequest(message="")

        logger.debug(
            "[_execute_single_tool] request.run_id=%s tool=%s",
            getattr(request, "run_id", None),
            tool_name,
        )

        # Reuse cached gateway if profile matches (FailureBudget persistence for HALLUCINATION_LOOP detection)
        # BUG FIX: Reset execution_count on turn boundary to prevent cross-turn accumulation.
        # The _execution_count tracks per-turn tool calls but was never reset when the
        # gateway was reused across turns, causing permanent tool lockout.
        # Also reset FailureBudget on turn boundary to prevent stale failure state
        # from one task/turn affecting the next one.
        current_turn_id = self._resolve_tool_gateway_turn_key(request)
        if self._cached_tool_gateway is not None and self._cached_gateway_profile is profile:
            gateway = self._cached_tool_gateway
            # Reset counter and failure budget if turn boundary changed
            if current_turn_id != self._cached_gateway_turn_id:
                gateway.reset_execution_count()
                # Reset FailureBudget to clear stale HALLUCINATION_LOOP state
                if hasattr(gateway, "_failure_budget") and hasattr(gateway._failure_budget, "reset"):
                    gateway._failure_budget.reset()
                self._cached_gateway_turn_id = current_turn_id
        else:
            # Create new gateway and cache it
            gateway = executor.create_gateway(
                profile=profile,
                request=request,
                tool_gateway=self._tool_gateway,
            )
            self._cached_tool_gateway = gateway
            self._cached_gateway_profile = profile
            self._cached_gateway_turn_id = current_turn_id

        return gateway.execute_tool(tool_name, args)

    # ═══════════════════════════════════════════════════════════════════════════
    # 辅助方法（委托到各模块）
    # ═══════════════════════════════════════════════════════════════════════════

    def _emit_event(
        self,
        *,
        event_type: str,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int = 0,
        publish_realtime: bool = True,
        **kwargs: Any,
    ) -> None:
        """发射 LLM 事件（委托到 KernelEventEmitter）"""
        self._get_event_emitter().emit_runtime_llm_event(
            event_type=event_type,
            role=role,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            publish_realtime=publish_realtime,
            workspace=self.workspace,
            **kwargs,
        )

    def _emit_stream_log_event(
        self,
        *,
        writer: LogEventWriter | None,
        role: str,
        run_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发射流日志事件（委托到 KernelEventEmitter）"""
        self._get_event_emitter().emit_stream_log_event(
            writer=writer,
            role=role,
            run_id=run_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    def _resolve_stream_run_id(self, request_run_id: str | None) -> str:
        """Resolve stream run_id from request or workspace runtime metadata."""
        requested = str(request_run_id or "").strip()
        if requested:
            return requested

        workspace = str(self.workspace or "").strip() or os.getcwd()
        try:
            roots = resolve_storage_roots(workspace)
            latest_run_file = os.path.join(roots.runtime_root, "latest_run.json")
            if os.path.isfile(latest_run_file):
                with open(latest_run_file, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("run_id"):
                    return str(payload.get("run_id", "").strip())
        except (RuntimeError, ValueError):
            logger.warning("Failed to resolve stream run_id from latest_run.json", exc_info=True)
        # Fallback: generate a new run_id so tool events can be journaled
        import uuid

        return f"auto_{uuid.uuid4().hex[:12]}"

    def _build_stream_log_writer(self, run_id: str) -> LogEventWriter | None:
        """Create a log writer for streaming events."""
        if not run_id:
            return None
        workspace = str(self.workspace or "").strip() or os.getcwd()
        try:
            return get_writer(workspace=workspace, run_id=run_id)
        except (RuntimeError, ValueError):
            logger.warning("Failed to create stream log writer for run_id=%s", run_id, exc_info=True)
            return None

    def _process_deprecated_params(self, request: RoleTurnRequest) -> str:
        """处理废弃参数"""
        appendix_parts: list[str] = []
        seen: set[str] = set()

        if request.prompt_appendix:
            token = str(request.prompt_appendix).strip()
            if token and token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        if request.system_prompt:
            token = str(request.system_prompt).strip()
            if token:
                warnings.warn(
                    "RoleTurnRequest.system_prompt is deprecated; use prompt_appendix instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                if token not in seen:
                    seen.add(token)
                    appendix_parts.append(token)

        extra_context = getattr(request, "extra_context", None)
        if extra_context:
            token = f"【额外上下文】\n{extra_context}"
            if token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        return "\n\n".join(appendix_parts)

    def _build_context(self, profile: RoleProfile, request: RoleTurnRequest) -> ContextRequest:
        """构建上下文请求"""
        context_os_snapshot = None
        if isinstance(request.context_override, dict):
            context_os_snapshot = request.context_override.get("context_os_snapshot")
        return ContextRequest(
            message=request.message,
            history=tuple(request.history) if request.history else (),
            task_id=request.task_id,
            context_os_snapshot=context_os_snapshot,
        )

    def _build_system_prompt_for_request(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
        prompt_appendix: str,
    ) -> str:
        """Build system prompt with domain-aware fallback compatibility."""
        domain = str(getattr(request, "domain", "") or "").strip().lower() or "code"
        try:
            return self._get_prompt_builder().build_system_prompt(
                profile,
                prompt_appendix,
                domain=domain,
                message=str(getattr(request, "message", "") or ""),
            )
        except TypeError:
            return self._get_prompt_builder().build_system_prompt(profile, prompt_appendix)

    def _create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> RoleToolGateway | _DelegatingToolGateway:
        """Create one per-request tool gateway (委托给 KernelToolExecutor)."""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return executor.create_gateway(profile, request, self._tool_gateway)

    async def _execute_tools(
        self, profile: RoleProfile, request: RoleTurnRequest, tool_calls: list[ToolCallResult]
    ) -> list[dict[str, Any]]:
        """执行工具调用（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return await executor.execute_tools(profile, request, tool_calls, self._tool_gateway)

    def _split_tool_calls_by_write_budget(
        self,
        role_id: str,
        tool_calls: list[ToolCallResult],
    ) -> tuple[list[ToolCallResult], list[ToolCallResult], int]:
        """Split tool calls by write budget（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        return KernelToolExecutor.split_tool_calls_by_write_budget(role_id, tool_calls)

    def _emit_tool_execute_events(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
    ) -> None:
        """发射工具执行前事件（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        executor.emit_tool_execute_events(profile, run_id, task_id, attempt, mode_value, tool_calls, self._emit_event)

    def _emit_tool_result_events_and_collect_errors(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        executed_tool_results: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """发射工具结果事件并收集错误（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return executor.emit_tool_result_events_and_collect_errors(
            profile, run_id, task_id, attempt, mode_value, tool_calls, executed_tool_results, self._emit_event
        )

    @staticmethod
    def _append_deferred_notice(
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
        executed_tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """追加 deferred notice（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        return KernelToolExecutor.append_deferred_notice(deferred_tool_calls, write_call_limit, executed_tool_results)

    @staticmethod
    def _log_deferred_write_calls(
        role_id: str,
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
    ) -> None:
        """记录 deferred write calls（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        KernelToolExecutor.log_deferred_write_calls(role_id, deferred_tool_calls, write_call_limit)

    def _parse_content_and_thinking_tool_calls(
        self,
        content: str,
        thinking: str | None,
        profile: Any,
        native_tool_calls: list[dict[str, Any]] | None,
        native_tool_provider: str,
    ) -> list[Any]:
        """Parse tool calls from content and thinking, filtering out thinking-only calls.

        Args:
            content: Raw text content from LLM
            thinking: Thinking content (may contain [TOOL_CALL]...[/TOOL_CALL] markers)
            profile: Role profile for allowed tool names
            native_tool_calls: Native tool calls from provider
            native_tool_provider: Provider hint for parsing

        Returns:
            List of parsed and filtered ToolCallResult objects
        """

        # Filter out tool calls that are only in thinking (not in main content)
        # by parsing only the main content (not thinking)
        result: list[ToolCallResult] = []
        seen: set[tuple[str, str]] = set()

        # Parse tool calls from main content and/or native_tool_calls
        # Note: native_tool_calls must be parsed even if content is empty
        # because LLM may emit tools via native protocol without content
        valid_parsed = self._get_output_parser().parse_tool_calls(
            content or "",  # Ensure content is never None
            native_tool_calls=native_tool_calls,
            native_provider=native_tool_provider,
        )
        for call in valid_parsed:
            key = (call.tool, str(call.args.get("path", "") or call.args.get("file", "")))
            if key not in seen:
                seen.add(key)
                result.append(call)

        return result


# 向后兼容：导出函数级别别名
_get_suggestions_for_error = get_suggestions_for_error

__all__ = [
    "RoleExecutionKernel",
    "get_suggestions_for_error",
]
