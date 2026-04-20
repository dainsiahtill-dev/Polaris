"""工具执行失败后的自动重试与纠偏策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetryConfig:
    """重试配置。"""

    max_retries: int = 3
    base_delay: float = 0.5
    exponential_backoff: bool = True


@dataclass(frozen=True)
class ToolError:
    """工具执行错误。"""

    tool_name: str
    error_message: str
    args: dict[str, Any]


class RetryPolicy:
    """错误重试策略。"""

    def __init__(self, config: RetryConfig | None = None) -> None:
        self._config = config or RetryConfig()

    # 不应重试的错误模式（大小写不敏感）
    _NO_RETRY_PATTERNS = (
        "permission denied",
        "access denied",
        "forbidden",
        "unauthorized",
    )

    def should_retry(self, error: ToolError, attempt: int) -> bool:
        """判断是否应该重试。"""
        if attempt >= self._config.max_retries:
            return False
        # 检查错误信息是否包含不可重试的模式
        error_lower = error.error_message.lower()
        return not any(p in error_lower for p in self._NO_RETRY_PATTERNS)

    def compute_delay(self, attempt: int) -> float:
        """计算重试延迟。

        Args:
            attempt: 当前重试次数（从 0 开始，与旧行为一致）
                   注意：工具级重试的 attempt=0 对应首次重试前延迟，
                   而 ResilienceManager 使用 attempt 从 1 开始。
                   两层重试独立计算，无需强制对齐。

        Returns:
            延迟秒数。指数退避时随尝试次数增加。
        """
        if self._config.exponential_backoff:
            return self._config.base_delay * (2**attempt)
        return self._config.base_delay

    def build_error_context(self, error: ToolError) -> str:
        """为下一次 LLM 调用构建错误上下文。"""
        return (
            f"[Previous Action Failed]\n"
            f"Tool: {error.tool_name}\n"
            f"Error: {error.error_message}\n"
            f"Arguments: {error.args}\n\n"
            f"Think: How to recover from this error?"
        )
