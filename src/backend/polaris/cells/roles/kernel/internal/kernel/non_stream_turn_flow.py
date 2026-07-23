"""Non-streaming role-turn flow owner.

This module owns the non-streaming RoleExecutionKernel run loop around
TransactionKernel execution: prompt setup, context preflight, retry prompt
construction, quality validation, metrics, and final RoleTurnResult projection.
The public ``RoleExecutionKernel`` class remains the API shell only.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.context_request_builder import build_context_request
from polaris.cells.roles.kernel.internal.kernel.error_handler import LLMEventType
from polaris.cells.roles.kernel.internal.kernel.event_emitter_provider import get_kernel_event_emitter
from polaris.cells.roles.kernel.internal.kernel.helpers import quality_result_to_dict
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    role_turn_error_result,
    role_turn_result_from_transaction_result,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor import TransactionTurnExecutor
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import (
    _bind_transaction_attempt,
    _start_transaction_invocation,
)
from polaris.cells.roles.kernel.internal.kernel.turn_output_validation import (
    validate_turn_output as _validate_turn_output,
)
from polaris.cells.roles.kernel.internal.kernel.turn_prompt_setup import (
    RoleTurnSetupError,
    build_role_turn_prompt_setup,
    format_role_turn_setup_error,
)
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult
from polaris.cells.roles.profile.public.service import RoleTurnRequest, RoleTurnResult
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


async def execute_non_stream_role_turn(
    *,
    kernel: RoleExecutionKernel,
    role: str,
    request: RoleTurnRequest,
) -> RoleTurnResult:
    """Execute a non-streaming role turn through TransactionKernel.

    Boundary:
        This function may mutate per-turn runtime state on ``kernel`` and may
        emit runtime/metrics events. It must not perform direct file, network,
        or tool side effects itself; those remain inside TransactionKernel and
        its controlled tool runtime.

    Complexity:
        O(r * (p + v)) time where ``r`` is retry attempts, ``p`` is prompt
        construction size, and ``v`` is quality validation cost. Memory is
        O(p + m), where ``m`` is result metadata copied into RoleTurnResult.
    """
    try:
        turn_setup = build_role_turn_prompt_setup(
            kernel=kernel,
            role=role,
            request=request,
        )
    except RoleTurnSetupError as exc:
        return role_turn_error_result(
            error=format_role_turn_setup_error(exc),
            is_complete=True,
        )

    profile = turn_setup.profile
    prompt_builder = turn_setup.prompt_builder
    fingerprint = turn_setup.fingerprint
    base_system_prompt = turn_setup.system_prompt

    try:
        _ = build_context_request(request)
    except (RuntimeError, ValueError) as exc:
        return role_turn_error_result(
            error=f"上下文构建失败: {exc}",
            is_complete=True,
        )

    # Reset cached gateway for new turn (FailureBudget should not persist across turns).
    kernel._cached_tool_gateway = None
    kernel._cached_gateway_profile = None

    # ``RoleTurnRequest`` owns the default. An explicit zero is a real
    # no-retry budget, not a request to silently borrow the kernel default.
    max_retries = max(0, int(request.max_retries))
    validate_output = request.validate_output
    last_validation: QualityResult | None = None
    last_error: str | None = None
    total_platform_retry_count = 0
    kernel_repair_retry_count = 0
    kernel_repair_reasons: list[str] = []

    task_id = str(getattr(request, "task_id", None) or "").strip()
    event_emitter = get_kernel_event_emitter(kernel)
    observer_run_id = event_emitter.resolve_observer_run_id(role, getattr(request, "run_id", None))
    if request.run_id is None:
        request.run_id = observer_run_id
    transaction_executor = TransactionTurnExecutor(kernel)
    transaction_invocation_id = _start_transaction_invocation(
        request,
        role=role,
        workspace=kernel.workspace,
    )

    for attempt in range(max_retries + 1):
        _bind_transaction_attempt(
            request,
            invocation_id=transaction_invocation_id,
            attempt=attempt,
        )
        system_prompt = prompt_builder.build_retry_prompt(
            base_system_prompt,
            quality_result_to_dict(last_validation),
            attempt,
        )

        response_schema: type | None = None
        tracer = get_tracer()
        with tracer.span(
            "role.kernel.llm_call",
            tags={"role": role, "attempt": attempt, "model": profile.model},
        ) as span:
            llm_start_time = time.monotonic()
            te_result = await transaction_executor.execute_turn(
                role=role,
                profile=profile,
                request=request,
                system_prompt=system_prompt,
                fingerprint=fingerprint,
                observer_run_id=observer_run_id,
                response_schema=response_schema,
            )
            llm_latency = time.monotonic() - llm_start_time

            _record_llm_latency(llm_latency)
            span.set_tag("llm_latency_seconds", llm_latency)
            span.set_tag("has_content", bool(te_result.content))
            span.set_tag("has_tool_calls", bool(te_result.tool_calls))

        if te_result.error:
            return role_turn_result_from_transaction_result(
                transaction_result=te_result,
                profile=profile,
                fingerprint=fingerprint,
                quality_result=last_validation,
                platform_retry_count=total_platform_retry_count,
                kernel_repair_retry_count=kernel_repair_retry_count,
                kernel_repair_reasons=kernel_repair_reasons,
                kernel_repair_exhausted=True,
                error=te_result.error,
                is_complete=False,
            )

        effective_content = te_result.content or ""
        last_validation = None
        final_structured_output: dict[str, Any] | None = None
        if validate_output:
            quality_result, last_error = _validate_turn_output(
                kernel=kernel,
                profile=profile,
                content=effective_content,
                response_schema=response_schema,
                attempt=attempt,
                max_retries=max_retries,
                last_error=last_error,
                has_tool_activity=bool(te_result.tool_calls or te_result.tool_results),
            )
            last_validation = quality_result
            if isinstance(quality_result.data, dict):
                final_structured_output = dict(quality_result.data)

            _record_quality_score(quality_result.quality_score)

            if not quality_result.success:
                event_emitter.emit_runtime_llm_event(
                    event_type=LLMEventType.VALIDATION_FAIL,
                    role=role,
                    run_id=observer_run_id,
                    task_id=task_id,
                    attempt=attempt,
                    workspace=kernel.workspace,
                    errors=quality_result.errors,
                    quality_score=quality_result.quality_score,
                    model=profile.model,
                    publish_realtime=False,
                )
                kernel_repair_retry_count += 1
                kernel_repair_reasons.append(
                    f"attempt_{attempt}: {quality_result.errors[-1] if quality_result.errors else 'validation_failed'}"
                )
                _record_retry(role, "validation_failed")

                if attempt < max_retries:
                    event_emitter.emit_runtime_llm_event(
                        event_type=LLMEventType.CALL_RETRY,
                        role=role,
                        run_id=observer_run_id,
                        task_id=task_id,
                        attempt=attempt,
                        workspace=kernel.workspace,
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

                _record_execution(role, "validation_failed")
                return role_turn_result_from_transaction_result(
                    transaction_result=te_result,
                    profile=profile,
                    fingerprint=fingerprint,
                    quality_result=last_validation,
                    platform_retry_count=total_platform_retry_count,
                    kernel_repair_retry_count=kernel_repair_retry_count,
                    kernel_repair_reasons=kernel_repair_reasons,
                    kernel_repair_exhausted=True,
                    error=error_msg,
                    is_complete=True,
                    content_override=effective_content,
                )

            event_emitter.emit_runtime_llm_event(
                event_type=LLMEventType.VALIDATION_PASS,
                role=role,
                run_id=observer_run_id,
                task_id=task_id,
                attempt=attempt,
                workspace=kernel.workspace,
                quality_score=quality_result.quality_score,
                model=profile.model,
                publish_realtime=False,
            )

        _record_execution(role, "success")
        return role_turn_result_from_transaction_result(
            transaction_result=te_result,
            profile=profile,
            fingerprint=fingerprint,
            quality_result=last_validation,
            structured_output=final_structured_output,
            platform_retry_count=total_platform_retry_count,
            kernel_repair_retry_count=kernel_repair_retry_count,
            kernel_repair_reasons=kernel_repair_reasons,
            kernel_repair_exhausted=False,
            error=None,
            is_complete=True,
        )

    raise RuntimeError("Unexpected fallthrough in execute_non_stream_role_turn")


def _record_llm_latency(latency_seconds: float) -> None:
    try:
        get_metrics_collector().record_llm_latency(latency_seconds)
    except (RuntimeError, ValueError):
        logger.warning("Failed to record LLM latency metric")


def _record_quality_score(quality_score: float) -> None:
    try:
        get_metrics_collector().record_quality_score(quality_score)
    except (RuntimeError, ValueError):
        logger.warning("Failed to record quality score metric")


def _record_retry(role: str, reason: str) -> None:
    try:
        get_metrics_collector().record_retry(role, reason)
    except (RuntimeError, ValueError):
        logger.warning("Failed to record retry metric")


def _record_execution(role: str, status: str) -> None:
    try:
        get_metrics_collector().record_execution(role, status)
    except (RuntimeError, ValueError):
        logger.warning("Failed to record execution metric")


__all__ = ["execute_non_stream_role_turn"]
