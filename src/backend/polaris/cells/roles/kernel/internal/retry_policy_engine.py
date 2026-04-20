"""Retry Policy Engine - 重试策略引擎

根据错误类别决策重试策略。
"""

import random
from dataclasses import dataclass

from .error_category import AUTO_RETRY_CATEGORIES, LIMITED_RETRY_CATEGORIES, ErrorCategory


@dataclass
class RetryDecision:
    """重试决策结果"""

    should_retry: bool  # 是否应该重试
    next_backoff_seconds: float  # 下次重试的延迟（秒）
    reason: str  # 决策原因
    category: ErrorCategory  # 错误类别


class RetryPolicyEngine:
    """重试策略引擎

    根据错误类别和当前重试次数，决策是否重试以及重试延迟。
    """

    DEFAULT_MAX_RETRIES = 3
    DEFAULT_BASE_DELAY = 1.0
    DEFAULT_MAX_DELAY = 30.0

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def should_retry(self, category: ErrorCategory, attempt: int) -> RetryDecision:
        """决策是否应该重试

        Args:
            category: 错误类别
            attempt: 当前尝试次数（从 0 开始）

        Returns:
            RetryDecision 重试决策
        """
        # 检查是否超过最大重试次数
        if attempt >= self.max_retries:
            return RetryDecision(
                should_retry=False,
                next_backoff_seconds=0,
                reason=f"已超过最大重试次数 {self.max_retries}",
                category=category,
            )

        # 认证错误：直接短路，不重试
        if category == ErrorCategory.AUTH:
            return RetryDecision(
                should_retry=False, next_backoff_seconds=0, reason="认证失败不应重试，需检查配置", category=category
            )

        # 自动重试类别
        if category in AUTO_RETRY_CATEGORIES:
            return RetryDecision(
                should_retry=True,
                next_backoff_seconds=self.calculate_backoff(attempt),
                reason=f"错误类别 {category.value} 可自动重试",
                category=category,
            )

        # 有限重试类别
        if category in LIMITED_RETRY_CATEGORIES:
            max_for_category = LIMITED_RETRY_CATEGORIES[category]
            if attempt >= max_for_category:
                return RetryDecision(
                    should_retry=False,
                    next_backoff_seconds=0,
                    reason=f"类别 {category.value} 已达重试上限 {max_for_category}",
                    category=category,
                )
            return RetryDecision(
                should_retry=True,
                next_backoff_seconds=self.calculate_backoff(attempt),
                reason=f"类别 {category.value} 有限重试 (尝试 {attempt + 1}/{max_for_category})",
                category=category,
            )

        # 反馈修复类别（解析错误、质量错误、工具错误）
        if category in (ErrorCategory.PARSE, ErrorCategory.QUALITY, ErrorCategory.TOOL):
            return RetryDecision(
                should_retry=True,
                next_backoff_seconds=self.calculate_backoff(attempt),
                reason=f"错误类别 {category.value} 可通过反馈修复重试",
                category=category,
            )

        # 未知错误
        return RetryDecision(
            should_retry=False,
            next_backoff_seconds=0,
            reason=f"未知错误类别 {category.value}，不建议重试",
            category=category,
        )

    def calculate_backoff(self, attempt: int, category: ErrorCategory | None = None) -> float:
        """计算退避时间（指数退避 + 抖动）

        Args:
            attempt: 当前尝试次数（从 0 开始）
            category: 错误类别（可选，用于调整策略）

        Returns:
            退避时间（秒）
        """
        # 基础延迟
        delay = self.base_delay * (2**attempt)

        # 根据错误类别调整
        if category == ErrorCategory.RATE_LIMIT:
            # 速率限制需要更长的等待
            delay = max(delay, 5.0 * (2**attempt))
        elif category == ErrorCategory.TIMEOUT:
            # 超时错误可以快速重试
            delay = min(delay, self.base_delay)

        # 限制最大延迟
        delay = min(delay, self.max_delay)

        # 添加抖动（±30%）
        jitter = 0.7 + (random.random() * 0.6)
        delay *= jitter

        return delay

    def get_suggestions(self, category: ErrorCategory) -> list[str]:
        """根据错误类别获取修复建议

        Args:
            category: 错误类别

        Returns:
            修复建议列表
        """
        suggestions_map = {
            ErrorCategory.TIMEOUT: ["请求超时，请稍后重试", "可以尝试减少上下文长度", "考虑降低 max_tokens 参数"],
            ErrorCategory.RATE_LIMIT: ["触发了速率限制，请稍后重试", "可以降低请求频率", "考虑使用批量处理"],
            ErrorCategory.NETWORK: ["网络连接问题，请检查网络后重试", "可能需要使用代理或 VPN", "请确认服务可达"],
            ErrorCategory.AUTH: ["认证失败，请检查 API Key 配置", "可能需要刷新认证信息", "请确认有权限访问该资源"],
            ErrorCategory.PROVIDER: [
                "LLM 服务提供商出现问题，请稍后重试",
                "可以尝试切换到其他模型",
                "可能需要等待服务恢复",
            ],
            ErrorCategory.PARSE: ["输出解析失败，请确保输出格式正确", "请严格按照要求的格式输出", "可以简化输出结构"],
            ErrorCategory.QUALITY: ["输出质量不达标，请改进内容质量", "请确保提供完整准确的信息", "可以增加具体细节"],
            ErrorCategory.TOOL: ["工具执行失败，请更换方案", "请尝试使用其他工具或调整参数", "检查工具参数是否正确"],
            ErrorCategory.UNKNOWN: ["请检查网络连接后重试", "如果问题持续，可能需要等待服务恢复", "可以尝试简化请求"],
        }

        return suggestions_map.get(category, suggestions_map[ErrorCategory.UNKNOWN])
