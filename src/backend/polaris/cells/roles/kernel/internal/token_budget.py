"""Token Budget - Token 预算管理

管理 LLM 上下文的 token 预算分配。

委托给 kernelone ContextBudgetGate 进行统一预算管理。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.context.budget_gate import ContextBudgetGate


class CompressionStrategy(str, Enum):
    """压缩策略"""

    NONE = "none"
    TRUNCATE = "truncate"
    SLIDING_WINDOW = "sliding_window"
    SUMMARIZE = "summarize"


@dataclass
class TokenBudget:
    """Token 预算分配器

    将有限的 token 预算分配给不同层次的上下文。
    委托给 kernelone ContextBudgetGate 进行统一预算管理。
    """

    _gate: "ContextBudgetGate | None" = field(default=None, repr=False)

    # 各部分预算配额 (legacy fields, used for backward compatibility)
    system_context: int = 4000  # 系统上下文配额
    task_context: int = 2000  # 任务上下文配额
    conversation: int = 4000  # 对话历史配额
    override: int = 1000  # 上下文覆盖配额
    safety_margin: int = 500  # 安全边际

    def __post_init__(self) -> None:
        # Lazy initialization of kernelone gate
        if self._gate is None:
            from polaris.kernelone.context.budget_gate import ContextBudgetGate

            total = self.total
            self._gate = ContextBudgetGate(
                model_window=total,
                safety_margin=1.0,  # TokenBudget manages its own safety margin
            )

    @property
    def total(self) -> int:
        """总预算"""
        return self.system_context + self.task_context + self.conversation + self.override + self.safety_margin

    @property
    def available_conversation(self) -> int:
        """可用于对话的预算"""
        used = self.system_context + self.task_context + self.override
        return max(0, self.total - used - self.safety_margin)

    def get_stats(self) -> dict[str, Any]:
        """获取预算统计"""
        breakdown = self._gate.get_section_breakdown() if self._gate else {}
        return {
            "system_context": self.system_context,
            "task_context": self.task_context,
            "conversation": self.conversation,
            "override": self.override,
            "safety_margin": self.safety_margin,
            "total": self.total,
            "available_conversation": self.available_conversation,
            "section_breakdown": breakdown,
        }

    def allocate(self, actual: dict[str, int]) -> "AllocationResult":
        """返回各部分是否超预算及裁剪建议

        Args:
            actual: 实际使用的 token 数量

        Returns:
            分配结果
        """
        result = AllocationResult()

        # Map actual keys to section names and delegate to gate
        section_map = {
            "system": self.system_context,
            "task": self.task_context,
            "conversation": self.conversation,
            "override": self.override,
        }

        gate_results = {}
        for section_key, allocated in section_map.items():
            actual_tokens = actual.get(section_key, 0)
            if self._gate:
                gate_results[section_key] = self._gate.allocate_section(section_key, allocated, actual_tokens)
            else:
                # Fallback when gate is not available (backward compatibility)
                from polaris.kernelone.context.budget_gate import SectionAllocation

                gate_results[section_key] = SectionAllocation(
                    section=section_key,
                    allocated=allocated,
                    actual=actual_tokens,
                    compressed=actual_tokens < allocated,
                )

        # 检查各部分
        for section_key, alloc in gate_results.items():
            if alloc.actual > alloc.allocated:
                result.over_budget.append(section_key)
                if section_key == "system":
                    result.suggestions.append("系统上下文超出预算，建议精简角色提示词")
                elif section_key == "task":
                    result.suggestions.append("任务上下文超出预算，建议精简任务描述")
                elif section_key == "conversation":
                    result.suggestions.append("对话历史超出预算，建议启用压缩策略")
                elif section_key == "override":
                    result.suggestions.append("上下文覆盖超出预算，建议精简注入内容")

        # 计算总体
        total_used = sum(actual.values())
        if total_used > self.total:
            result.over_budget.append("total")
            result.suggestions.append(f"总 token 超出预算 ({total_used} > {self.total})")

        result.total_used = total_used
        result.total_budget = self.total

        return result

    def get_compression_strategy(self, current_tokens: int, target_tokens: int) -> CompressionStrategy:
        """根据当前 token 数量确定压缩策略

        Args:
            current_tokens: 当前 token 数量
            target_tokens: 目标 token 数量

        Returns:
            推荐的压缩策略
        """
        if current_tokens <= target_tokens:
            return CompressionStrategy.NONE

        # 计算需要压缩的比例
        ratio = target_tokens / current_tokens

        if ratio > 0.8:
            # 需要压缩少于 20%，使用滑动窗口
            return CompressionStrategy.SLIDING_WINDOW
        elif ratio > 0.5:
            # 需要压缩 20-50%，使用摘要
            return CompressionStrategy.SUMMARIZE
        else:
            # 需要压缩超过 50%，使用截断
            return CompressionStrategy.TRUNCATE


@dataclass
class AllocationResult:
    """分配结果"""

    over_budget: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    total_used: int = 0
    total_budget: int = 0

    @property
    def is_over_budget(self) -> bool:
        return len(self.over_budget) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "over_budget": self.over_budget,
            "suggestions": self.suggestions,
            "total_used": self.total_used,
            "total_budget": self.total_budget,
            "is_over_budget": self.is_over_budget,
        }


# Global token budget instance
_token_budget: TokenBudget | None = None


def get_global_token_budget() -> TokenBudget:
    """Get global TokenBudget instance"""
    global _token_budget
    if _token_budget is None:
        _token_budget = TokenBudget()
    return _token_budget
