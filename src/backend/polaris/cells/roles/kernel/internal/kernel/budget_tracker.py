"""Budget Tracker - 令牌/成本预算追踪

负责：
- 平台重试计数
- 内核修复重试计数
- 修复原因收集
- 执行统计构建
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polaris.cells.roles.kernel.internal.quality_checker import QualityResult


@dataclass
class BudgetTracker:
    """预算追踪器

    跟踪回合执行过程中的重试和修复统计信息。
    """

    total_platform_retry_count: int = 0
    kernel_repair_retry_count: int = 0
    kernel_repair_reasons: list[str] = field(default_factory=list)

    def record_validation_failure(self, attempt: int, errors: list[str]) -> None:
        """记录验证失败

        Args:
            attempt: 当前尝试次数
            errors: 错误信息列表
        """
        self.kernel_repair_retry_count += 1
        self.kernel_repair_reasons.append(f"attempt_{attempt}: {errors[-1] if errors else 'validation_failed'}")

    def build_execution_stats(
        self,
        te_result_stats: dict[str, Any],
        exhausted: bool,
    ) -> dict[str, Any]:
        """构建执行统计信息

        Args:
            te_result_stats: TurnEngine 返回的原始统计
            exhausted: 是否已耗尽重试次数

        Returns:
            完整的执行统计字典
        """
        return {
            "platform_retry_count": self.total_platform_retry_count,
            "kernel_repair_retry_count": self.kernel_repair_retry_count,
            "kernel_repair_reasons": list(self.kernel_repair_reasons),
            "kernel_repair_exhausted": exhausted,
            **te_result_stats,
        }

    def build_error_result(
        self,
        te_result: Any,
        profile: Any,
        fingerprint: Any,
        last_validation: QualityResult | None,
    ) -> Any:
        """构建错误结果（从 TurnEngine 错误构建 RoleTurnResult）

        Args:
            te_result: TurnEngine 结果
            profile: 角色配置
            fingerprint: 提示词指纹
            last_validation: 最后一次验证结果

        Returns:
            RoleTurnResult 错误结果
        """
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
            tool_execution_error=getattr(te_result, "tool_execution_error", None),
            should_retry=getattr(te_result, "should_retry", False),
            execution_stats=self.build_execution_stats(
                getattr(te_result, "execution_stats", {}) or {},
                exhausted=True,
            ),
            turn_history=list(te_result.turn_history) if te_result.turn_history else [],
            turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
        )

    def build_success_result(
        self,
        te_result: Any,
        profile: Any,
        fingerprint: Any,
        last_validation: QualityResult | None,
        final_structured_output: dict[str, Any] | None,
    ) -> Any:
        """构建成功结果

        Args:
            te_result: TurnEngine 结果
            profile: 角色配置
            fingerprint: 提示词指纹
            last_validation: 最后一次验证结果
            final_structured_output: 最终结构化输出

        Returns:
            RoleTurnResult 成功结果
        """
        from polaris.cells.roles.profile.public.service import RoleTurnResult

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
            execution_stats=self.build_execution_stats(
                getattr(te_result, "execution_stats", {}) or {},
                exhausted=False,
            ),
            turn_history=list(te_result.turn_history) if te_result.turn_history else [],
            turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
        )


__all__ = ["BudgetTracker"]
