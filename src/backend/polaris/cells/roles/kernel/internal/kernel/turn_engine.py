"""Turn Engine - Turn 执行逻辑

负责：
- TransactionKernel 创建与配置
- TransactionKernel 回合执行（非流式）
- TransactionKernel 流式执行
- ContextHandoffPack 构建
- 质量验证辅助
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.kernel.helpers import quality_result_to_dict
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TransactionConfig
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope
from polaris.kernelone.events.uep_publisher import UEPEventPublisher
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.profile.public.service import (
        RoleProfile,
        RoleTurnRequest,
        RoleTurnResult,
    )

logger = logging.getLogger(__name__)


class TurnEngineExecutor:
    """Turn 执行引擎

    负责执行单个回合的完整逻辑，包括 TransactionKernel 调用、
    结果映射、质量验证等。
    """

    __slots__ = ("_kernel",)

    def __init__(self, kernel: RoleExecutionKernel) -> None:
        """初始化 Turn 执行引擎

        Args:
            kernel: RoleExecutionKernel 实例
        """
        self._kernel = kernel

    async def execute_turn_with_retries(
        self,
        role: str,
        request: RoleTurnRequest,
        profile: RoleProfile,
        controller: Any,
        base_system_prompt: str,
        fingerprint: Any,
        observer_run_id: str,
        task_id: str | None,
    ) -> RoleTurnResult:
        """执行带重试机制的回合

        Args:
            role: 角色标识
            request: 回合请求
            profile: 角色配置
            controller: 工具循环控制器
            base_system_prompt: 基础系统提示词
            fingerprint: 提示词指纹
            observer_run_id: 观察运行 ID
            task_id: 任务 ID

        Returns:
            回合结果
        """
        from polaris.cells.roles.kernel.internal.kernel.budget_tracker import BudgetTracker
        from polaris.cells.roles.kernel.internal.kernel.error_handler import LLMEventType
        from polaris.cells.roles.profile.public.service import RoleTurnResult

        max_retries = request.max_retries if request.max_retries > 0 else self._kernel.config.max_retries
        validate_output = request.validate_output
        last_validation: QualityResult | None = None
        last_error: str | None = None

        pre_validated_data: dict[str, Any] | None = None
        instructor_validated = False

        budget = BudgetTracker()

        for attempt in range(max_retries + 1):
            system_prompt = self._kernel._get_prompt_builder().build_retry_prompt(
                base_system_prompt, quality_result_to_dict(last_validation), attempt
            )

            response_schema = self._kernel._get_response_schema(role)

            tracer = get_tracer()

            with tracer.span(
                "role.kernel.llm_call",
                tags={"role": role, "attempt": attempt, "model": profile.model},
            ) as span:
                llm_start_time = time.monotonic()
                if self._kernel._use_transaction_kernel():
                    te_result = await self.execute_transaction_kernel_turn(
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

                    engine = TurnEngine(kernel=self._kernel)
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

                try:
                    metrics = get_metrics_collector()
                    metrics.record_llm_latency(llm_latency)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record LLM latency metric")

                span.set_tag("llm_latency_seconds", llm_latency)
                span.set_tag("has_content", bool(te_result.content))
                span.set_tag("has_tool_calls", bool(te_result.tool_calls))

            if te_result.error:
                return budget.build_error_result(te_result, profile, fingerprint, last_validation)

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
                            candidate = self._kernel._get_output_parser().extract_json(effective_content)
                            if candidate is None:
                                raise ValueError("No JSON found in content")
                            validated = response_schema(**candidate)
                            pre_validated_data = validated.model_dump()
                            instructor_validated = True
                        except (RuntimeError, ValueError):
                            pre_validated_data = None
                            instructor_validated = False
                    try:
                        quality_result = self._kernel._get_quality_checker().validate_output(
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

                try:
                    metrics = get_metrics_collector()
                    metrics.record_quality_score(quality_result.quality_score)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record quality score metric")

                if not quality_result.success:
                    self._kernel._emit_event(
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
                    budget.record_validation_failure(attempt, quality_result.errors)

                    try:
                        metrics = get_metrics_collector()
                        metrics.record_retry(role, "validation_failed")
                    except (RuntimeError, ValueError):
                        logger.warning("Failed to record retry metric")

                    if attempt < max_retries:
                        self._kernel._emit_event(
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
                        execution_stats=budget.build_execution_stats(
                            getattr(te_result, "execution_stats", {}) or {},
                            exhausted=True,
                        ),
                        turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                        turn_events_metadata=list(te_result.turn_events_metadata)
                        if te_result.turn_events_metadata
                        else [],
                    )

                self._kernel._emit_event(
                    event_type=LLMEventType.VALIDATION_PASS,
                    role=role,
                    run_id=observer_run_id,
                    task_id=task_id,
                    attempt=attempt,
                    quality_score=quality_result.quality_score,
                    model=profile.model,
                    publish_realtime=False,
                )

            try:
                metrics = get_metrics_collector()
                metrics.record_execution(role, "success")
            except (RuntimeError, ValueError):
                logger.warning("Failed to record execution success metric")

            return budget.build_success_result(
                te_result, profile, fingerprint, last_validation, final_structured_output
            )

        raise RuntimeError("Unexpected fallthrough in TurnEngineExecutor.execute_turn_with_retries")

    async def execute_stream(
        self,
        role: str,
        request: RoleTurnRequest,
        profile: RoleProfile,
        system_prompt: str,
        fingerprint: Any,
        stream_run_id: str,
        uep_publisher: UEPEventPublisher,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行回合

        Args:
            role: 角色标识
            request: 回合请求
            profile: 角色配置
            system_prompt: 系统提示词
            fingerprint: 提示词指纹
            stream_run_id: 流式运行 ID
            uep_publisher: UEP 事件发布器

        Yields:
            流式事件字典
        """
        if self._kernel._use_transaction_kernel():
            async for event in self.execute_transaction_kernel_stream(
                role=role,
                profile=profile,
                request=request,
                system_prompt=system_prompt,
                fingerprint=fingerprint,
                stream_run_id=stream_run_id,
                uep_publisher=uep_publisher,
            ):
                yield event
        else:
            from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine

            engine = TurnEngine(kernel=self._kernel)
            async for event in engine.run_stream(
                request=request,
                role=role,
                controller=ToolLoopController.from_request(request=request, profile=profile),
                system_prompt=system_prompt,
                fingerprint=fingerprint,
            ):
                event_type = str(event.get("type") or "").strip()
                await uep_publisher.publish_stream_event(
                    workspace=self._kernel.workspace or __import__("os").getcwd(),
                    run_id=stream_run_id,
                    role=role,
                    event_type=event_type,
                    payload=dict(event),
                )
                yield event

    def create_transaction_kernel(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> TransactionKernel:
        """Create a TransactionKernel with kernel-backed LLM and tool adapters."""
        import copy
        import dataclasses
        import inspect
        import weakref

        caller = self._kernel._get_llm_caller()
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
                    return provider_profile.model_copy(update={"model": model_override})
                except (AttributeError, TypeError, ValueError):
                    pass
            if dataclasses.is_dataclass(provider_profile) and not isinstance(provider_profile, type):
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
                return __import__("types").SimpleNamespace(**fallback_payload)

        kernel_weakref = weakref.ref(self._kernel)
        provider_profile = profile
        provider_request = request

        class _LLMProvider:
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
            __slots__ = ()

            def reset_turn_boundary(self, turn_id: str) -> None:
                kernel = kernel_weakref()
                if kernel is None:
                    return
                normalized_turn_id = str(turn_id or "").strip()
                if not normalized_turn_id:
                    return
                cast(Any, provider_request).turn_id = normalized_turn_id
                kernel._tool_loop.reset_tool_gateway_turn_boundary(normalized_turn_id)

            async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any:
                kernel = kernel_weakref()
                if kernel is None:
                    raise RuntimeError("Kernel instance no longer exists")
                return await kernel._tool_loop.execute_single_tool(
                    tool_name=tool_name,
                    args=arguments,
                    context={"profile": provider_profile, "request": provider_request},
                )

        class _LLMProviderStream:
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

    async def execute_transaction_kernel_turn(
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
        from polaris.cells.roles.profile.public.service import RoleTurnResult

        tk = self._kernel._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or observer_run_id or uuid.uuid4().hex[:12])

        controller = ToolLoopController.from_request(request=request, profile=profile)
        context_request = controller.build_context_request()
        context_gateway = RoleContextGateway(profile, self._kernel.workspace)
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = (
            [] if self._kernel._benchmark_requires_no_tools(request) else build_native_tool_schemas(profile)
        )

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
            parsed = self._kernel._get_output_parser().parse_thinking(visible_content)
            visible_content = str(parsed.clean_content or "")
            thinking_text = parsed.thinking
        batch_receipt = tk_result.get("batch_receipt")
        finalization = tk_result.get("finalization")
        workflow_context = tk_result.get("workflow_context")
        metrics = tk_result.get("metrics", {})

        ledger = tk_result.get("ledger")

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

        structured_output: dict[str, Any] | None = None
        if response_schema is not None and visible_content:
            try:
                candidate = self._kernel._get_output_parser().extract_json(visible_content)
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
            handoff_pack = self.build_context_handoff_pack(tk_result, role, request, self._kernel.workspace)
            metadata["handoff_pack"] = handoff_pack.to_dict()
            metadata["transaction_kind"] = "handoff_workflow"

        error_msg: str | None = None
        is_complete = True
        if kind == "ask_user" and isinstance(finalization, dict):
            error_msg = finalization.get("error") or finalization.get("suspended_reason")
            is_complete = False

        final_thinking = thinking_text
        if final_thinking is None and isinstance(finalization, dict):
            final_thinking = finalization.get("final_visible_message")

        turn_history, turn_events_metadata = self._kernel._build_turn_history_and_events(
            turn_id=turn_id,
            request=request,
            visible_content=visible_content,
            thinking=final_thinking,
            tool_results=tool_results,
        )

        self._kernel._commit_turn_to_snapshot(
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

    async def execute_transaction_kernel_stream(
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

        tk = self._kernel._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or stream_run_id or uuid.uuid4().hex[:12])

        controller = ToolLoopController.from_request(request=request, profile=profile)
        context_request = controller.build_context_request()
        context_gateway = RoleContextGateway(profile, self._kernel.workspace)
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = (
            [] if self._kernel._benchmark_requires_no_tools(request) else build_native_tool_schemas(profile)
        )

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
                if event.status in ("failed", "suspended"):
                    event_dict = {
                        "type": "error",
                        "error": event.error or "execution_failed",
                        "error_type": "stream_execution_failed",
                        "turn_id": event.turn_id,
                    }
                    await uep_publisher.publish_stream_event(
                        workspace=self._kernel.workspace or __import__("os").getcwd(),
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
                turn_history, turn_events_metadata = self._kernel._build_turn_history_and_events(
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
                workspace=self._kernel.workspace or __import__("os").getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type=event_dict.get("type", "unknown"),
                payload=event_dict,
            )
            yield event_dict

    @staticmethod
    def build_context_handoff_pack(
        turn_result: dict[str, Any],
        role: str,
        request: RoleTurnRequest,
        workspace: str,
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
            workspace=str(request.workspace or workspace or "."),
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


__all__ = ["TurnEngineExecutor"]
