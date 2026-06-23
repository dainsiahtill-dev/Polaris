"""Public run-loop orchestration for RoleExecutionKernel.

Holds the bodies of ``RoleExecutionKernel.run`` (retry + quality loop) and
``RoleExecutionKernel.run_stream`` extracted verbatim (behavior-preserving)
into free functions. The class methods become thin delegating shims.

FROZEN behavior notes (do NOT change):
- ``run`` retry/quality semantics, the metrics-recording calls, the
  validation-fail / validation-pass event emissions, and the exact
  ``RoleTurnResult`` field-mapping for the error / validation-exhausted /
  success branches are preserved verbatim.
- ``run_stream`` resolves and writes back ``request.run_id``, emits the
  fingerprint event, and forwards the inner stream verbatim, including the
  ``inner_error`` swallow semantics.
- All collaborator calls go through ``kernel._<method>`` so that the
  monkeypatch / bound-method / dependency-injection surface is unchanged.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.error_handler import LLMEventType
from polaris.cells.roles.kernel.internal.kernel.helpers import quality_result_to_dict
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult
from polaris.cells.roles.profile.public.service import RoleTurnRequest, RoleTurnResult
from polaris.kernelone.events.uep_publisher import UEPEventPublisher
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


async def run(
    kernel: RoleExecutionKernel,
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
        profile = kernel.registry.get_profile_or_raise(role)
    except (RuntimeError, ValueError) as e:
        return RoleTurnResult(error=f"角色加载失败: {e}", is_complete=True)

    # 2. 处理废弃参数
    try:
        prompt_appendix = kernel._process_deprecated_params(request)
    except (RuntimeError, ValueError) as e:
        return RoleTurnResult(error=f"参数处理失败: {e}", is_complete=True)

    prompt_appendix = kernel._append_prompt_profiles_for_request(
        profile=profile,
        request=request,
        prompt_appendix=prompt_appendix,
        context_override=getattr(request, "context_override", None),
        message=str(getattr(request, "message", "") or ""),
    )

    # 3. 构建提示词指纹
    try:
        fingerprint = kernel._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
    except (RuntimeError, ValueError) as e:
        return RoleTurnResult(error=f"提示词构建失败: {e}", is_complete=True)

    # 4. 构建基础系统提示词
    try:
        base_system_prompt = kernel._build_system_prompt_for_request(profile, request, prompt_appendix)
    except (RuntimeError, ValueError) as e:
        return RoleTurnResult(error=f"系统提示词构建失败: {e}", is_complete=True)

    # 5. 构建上下文（验证可用性，结果由 TurnEngine 使用）
    try:
        _ = kernel._build_context(profile, request)
    except (RuntimeError, ValueError) as e:
        return RoleTurnResult(error=f"上下文构建失败: {e}", is_complete=True)

    # Reset cached gateway for new turn (FailureBudget should not persist across turns)
    kernel._cached_tool_gateway = None
    kernel._cached_gateway_profile = None

    # 6. 重试循环配置
    max_retries = request.max_retries if request.max_retries > 0 else kernel._config.max_retries
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
    observer_run_id = kernel._get_event_emitter().resolve_observer_run_id(role, getattr(request, "run_id", None))
    # 将 resolved run_id 写回 request，确保下游（TurnEngine/RoleToolGateway）能获取到
    if request.run_id is None:
        request.run_id = observer_run_id

    for attempt in range(max_retries + 1):
        # 构建当前尝试的系统提示词
        system_prompt = kernel._get_prompt_builder().build_retry_prompt(
            base_system_prompt, quality_result_to_dict(last_validation), attempt
        )

        response_schema = kernel._get_response_schema(role)

        # Get tracer for OpenTelemetry integration
        tracer = get_tracer()

        # Track LLM latency
        with tracer.span(
            "role.kernel.llm_call",
            tags={"role": role, "attempt": attempt, "model": profile.model},
        ) as span:
            llm_start_time = time.monotonic()
            te_result = await kernel._execute_transaction_kernel_turn(
                role=role,
                profile=profile,
                request=request,
                system_prompt=system_prompt,
                fingerprint=fingerprint,
                observer_run_id=observer_run_id,
                response_schema=response_schema,
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

        # TransactionKernel 返回错误，不重试
        if te_result.error:
            return RoleTurnResult(
                content=te_result.content or "",
                thinking=te_result.thinking,
                tool_calls=te_result.tool_calls or [],
                tool_results=te_result.tool_results or [],
                batch_receipt=dict(te_result.batch_receipt) if isinstance(te_result.batch_receipt, dict) else None,
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
                metadata=dict(getattr(te_result, "metadata", {}) or {}),
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
                        candidate = kernel._get_output_parser().extract_json(effective_content)
                        if candidate is None:
                            raise ValueError("No JSON found in content")
                        validated = response_schema(**candidate)
                        pre_validated_data = validated.model_dump()
                        instructor_validated = True
                    except (RuntimeError, ValueError):
                        pre_validated_data = None
                        instructor_validated = False
                try:
                    quality_result = kernel._get_quality_checker().validate_output(
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
                kernel._emit_event(
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
                    f"attempt_{attempt}: {quality_result.errors[-1] if quality_result.errors else 'validation_failed'}"
                )

                # Record retry
                try:
                    metrics = get_metrics_collector()
                    metrics.record_retry(role, "validation_failed")
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record retry metric")

                if attempt < max_retries:
                    kernel._emit_event(
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
                    tool_calls=te_result.tool_calls or [],
                    tool_results=te_result.tool_results or [],
                    batch_receipt=dict(te_result.batch_receipt) if isinstance(te_result.batch_receipt, dict) else None,
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
                    turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
                    metadata=dict(getattr(te_result, "metadata", {}) or {}),
                )

            kernel._emit_event(
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
            batch_receipt=dict(te_result.batch_receipt) if isinstance(te_result.batch_receipt, dict) else None,
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
            metadata=dict(getattr(te_result, "metadata", {}) or {}),
        )

    # unreachable
    raise RuntimeError("Unexpected fallthrough in RoleExecutionKernel.run")


async def run_stream(
    kernel: RoleExecutionKernel,
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
    stream_run_id = kernel._resolve_stream_run_id(request.run_id)
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
        profile = kernel.registry.get_profile_or_raise(role)

        # Reset cached gateway for new turn (FailureBudget should not persist across turns)
        kernel._cached_tool_gateway = None
        kernel._cached_gateway_profile = None

        # 2. 处理废弃参数
        prompt_appendix = kernel._process_deprecated_params(request)
        prompt_appendix = kernel._append_prompt_profiles_for_request(
            profile=profile,
            request=request,
            prompt_appendix=prompt_appendix,
            context_override=getattr(request, "context_override", None),
            message=str(getattr(request, "message", "") or ""),
        )

        # 3. 构建提示词指纹
        fingerprint = kernel._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
        await uep_publisher.publish_stream_event(
            workspace=kernel.workspace or os.getcwd(),
            run_id=stream_run_id,
            role=role,
            event_type="fingerprint",
            payload={"fingerprint": str(fingerprint.full_hash or "")},
        )
        yield {"type": "fingerprint", "fingerprint": fingerprint}

        # 4. 构建系统提示词
        system_prompt = kernel._build_system_prompt_for_request(profile, request, prompt_appendix)

        try:
            async for event in kernel._execute_transaction_kernel_stream(
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
                workspace=kernel.workspace or os.getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type="error",
                payload={"error": str(e)},
            )
            yield {"type": "error", "error": str(e)}

    except (RuntimeError, ValueError):
        if inner_error is None:
            raise
