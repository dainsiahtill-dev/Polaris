"""Turn Runner - 回合执行器

负责 RoleExecutionKernel 的 run 方法核心逻辑。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.helpers import quality_result_to_dict
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
    from polaris.cells.roles.profile.public.service import (
        PromptFingerprint,
        RoleProfile,
        RoleTurnRequest,
        RoleTurnResult,
    )

logger = logging.getLogger(__name__)


class TurnRunner:
    """回合执行器

    负责：
    - TurnEngine 执行
    - 质量验证
    - 重试逻辑
    - 结果构建
    """

    __slots__ = ("_kernel",)

    def __init__(self, kernel: RoleExecutionKernel) -> None:
        """初始化回合执行器

        Args:
            kernel: RoleExecutionKernel 实例
        """
        self._kernel = kernel

    async def run_turn(
        self,
        role: str,
        request: RoleTurnRequest,
        profile: RoleProfile,
        controller: ToolLoopController,
        base_system_prompt: str,
        fingerprint: PromptFingerprint,
        observer_run_id: str,
        task_id: str | None,
    ) -> RoleTurnResult:
        """执行回合主循环

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
        from polaris.cells.roles.profile.public.service import RoleTurnResult

        max_retries = request.max_retries if request.max_retries > 0 else self._kernel.config.max_retries
        validate_output = request.validate_output
        last_validation: QualityResult | None = None

        # 重试统计
        total_platform_retry_count = 0
        kernel_repair_retry_count = 0
        kernel_repair_reasons: list[str] = []

        for attempt in range(max_retries + 1):
            prompt_builder = self._kernel._prompt_builder
            if prompt_builder is not None:
                system_prompt = prompt_builder.build_retry_prompt(
                    base_system_prompt, quality_result_to_dict(last_validation), attempt
                )
            else:
                system_prompt = base_system_prompt

            response_schema = self._get_response_schema(role)
            tracer = get_tracer()

            with tracer.span(
                "role.kernel.llm_call",
                tags={"role": role, "attempt": attempt, "model": profile.model},
            ) as span:
                llm_start_time = time.monotonic()
                te_result = await self._kernel._execute_transaction_kernel_turn(
                    role=role,
                    profile=profile,
                    request=request,
                    system_prompt=system_prompt,
                    fingerprint=fingerprint,
                    observer_run_id=observer_run_id,
                    response_schema=response_schema,
                )
                llm_latency = time.monotonic() - llm_start_time

                self._record_llm_latency(llm_latency)
                span.set_tag("llm_latency_seconds", llm_latency)
                span.set_tag("has_content", bool(te_result.content))
                span.set_tag("has_tool_calls", bool(te_result.tool_calls))

            # TurnEngine 返回错误
            if te_result.error:
                return self._build_error_result(
                    te_result,
                    profile,
                    fingerprint,
                    last_validation,
                    total_platform_retry_count,
                    kernel_repair_retry_count,
                    kernel_repair_reasons,
                )

            # 质量验证
            if validate_output:
                validation_result = await self._validate_output(
                    te_result.content or "",
                    profile,
                    response_schema,
                    role,
                    observer_run_id,
                    task_id,
                    attempt,
                    max_retries,
                    kernel_repair_retry_count,
                    kernel_repair_reasons,
                )

                if isinstance(validation_result, RoleTurnResult):
                    return validation_result

                last_validation = validation_result
                final_structured_output = validation_result.data if isinstance(validation_result.data, dict) else None
            else:
                final_structured_output = None

            # 最终结果
            self._record_execution(role, "success")
            return self._build_success_result(
                te_result,
                profile,
                fingerprint,
                last_validation,
                final_structured_output,
                total_platform_retry_count,
                kernel_repair_retry_count,
                kernel_repair_reasons,
            )

        raise RuntimeError("Unexpected fallthrough in TurnRunner.run_turn")

    def _get_response_schema(self, role: str) -> type | None:
        """获取响应模式"""
        if not self._kernel._use_structured_output:
            return None
        try:
            from polaris.cells.roles.adapters.public.service import get_schema_for_role

            return get_schema_for_role(role)
        except ImportError:
            return None

    def _record_llm_latency(self, latency: float) -> None:
        """记录 LLM 延迟"""
        try:
            metrics = get_metrics_collector()
            metrics.record_llm_latency(latency)
        except (RuntimeError, ValueError):
            logger.debug("Failed to record LLM latency metric: %s", latency)

    def _record_execution(self, role: str, status: str) -> None:
        """记录执行状态"""
        try:
            metrics = get_metrics_collector()
            metrics.record_execution(role, status)
        except (RuntimeError, ValueError):
            logger.debug("Failed to record execution metric: role=%s, status=%s", role, status)

    def _build_error_result(
        self,
        te_result: Any,
        profile: RoleProfile,
        fingerprint: PromptFingerprint,
        last_validation: QualityResult | None,
        platform_retry_count: int,
        repair_retry_count: int,
        repair_reasons: list[str],
    ) -> RoleTurnResult:
        """构建错误结果"""
        from polaris.cells.roles.profile.public.service import RoleTurnResult

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
            execution_stats={
                "platform_retry_count": platform_retry_count,
                "kernel_repair_retry_count": repair_retry_count,
                "kernel_repair_reasons": repair_reasons,
                "kernel_repair_exhausted": True,
                **te_result.execution_stats,
            },
            turn_history=list(te_result.turn_history) if te_result.turn_history else [],
            turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
        )

    def _build_success_result(
        self,
        te_result: Any,
        profile: RoleProfile,
        fingerprint: PromptFingerprint,
        last_validation: QualityResult | None,
        structured_output: dict[str, Any] | None,
        platform_retry_count: int,
        repair_retry_count: int,
        repair_reasons: list[str],
    ) -> RoleTurnResult:
        """构建成功结果"""
        from polaris.cells.roles.profile.public.service import RoleTurnResult

        return RoleTurnResult(
            content=te_result.content or "",
            thinking=te_result.thinking,
            structured_output=structured_output,
            tool_calls=te_result.tool_calls or [],
            tool_results=te_result.tool_results or [],
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            quality_score=last_validation.quality_score if last_validation else 0.0,
            quality_suggestions=last_validation.suggestions if last_validation else [],
            error=None,
            is_complete=True,
            execution_stats={
                "platform_retry_count": platform_retry_count,
                "kernel_repair_retry_count": repair_retry_count,
                "kernel_repair_reasons": repair_reasons,
                "kernel_repair_exhausted": False,
                **te_result.execution_stats,
            },
            turn_history=list(te_result.turn_history) if te_result.turn_history else [],
            turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
        )

    async def _validate_output(
        self,
        content: str,
        profile: RoleProfile,
        response_schema: type | None,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        max_retries: int,
        repair_retry_count: int,
        repair_reasons: list[str],
    ) -> QualityResult | RoleTurnResult:
        """验证输出质量

        Returns:
            QualityResult 如果验证通过或需要重试
            RoleTurnResult 如果验证失败且重试耗尽
        """

        tool_only_turn = not str(content or "").strip() and False  # placeholder

        if tool_only_turn:
            return QualityResult(
                success=True,
                errors=[],
                suggestions=[],
                data={"tool_only_turn": True},
                quality_score=100.0,
                quality_passed=True,
            )

        # 执行质量检查
        pre_validated_data = None
        instructor_validated = False

        if response_schema is not None:
            try:
                output_parser = self._kernel._output_parser
                if output_parser is not None:
                    candidate = output_parser.extract_json(content)
                    if candidate is None:
                        raise ValueError("No JSON found in content")
                    validated = response_schema(**candidate)
                    pre_validated_data = validated.model_dump()
                    instructor_validated = True
            except (RuntimeError, ValueError) as e:
                logger.debug("Instructor validation failed (attempt=%d): %s", attempt, e)

        try:
            quality_checker = self._kernel._quality_checker
            if quality_checker is not None:
                quality_result = quality_checker.validate_output(
                    content,
                    profile,
                    pre_validated_data=pre_validated_data,
                    instructor_validated=instructor_validated,
                )
            else:
                quality_result = QualityResult(
                    success=True,
                    errors=[],
                    suggestions=[],
                    data=pre_validated_data if pre_validated_data is not None else {},
                    quality_score=100.0,
                    quality_passed=True,
                )
        except (RuntimeError, ValueError) as e:
            logger.warning("质量检查失败 (attempt=%d): %s", attempt, e)
            quality_result = QualityResult(
                success=False,
                errors=[f"质量检查失败: {e}"],
                suggestions=["请确保输出内容完整准确"] if attempt < max_retries else [],
                data={"quality_check_error": True},
                quality_score=0.0,
                quality_passed=False,
            )

        # 记录质量分数
        try:
            metrics = get_metrics_collector()
            metrics.record_quality_score(quality_result.quality_score)
        except (RuntimeError, ValueError):
            logger.debug("Failed to record quality score metric: %s", quality_result.quality_score)

        return quality_result


__all__ = ["TurnRunner"]
