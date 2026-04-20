"""Tiered Summarization - 分层摘要架构

ADR-0067: ContextOS 2.0 摘要策略选型
实现三层的摘要策略，根据内容类型、预算压力和系统负载动态选择。

架构:
- Tier 1: 智能摘要 (transformers, langchain)
- Tier 2: 安全摘要 (sumy, tree-sitter)
- Tier 3: 紧急回退 (truncation)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummaryStrategy,
)
from polaris.kernelone.context.context_os.summarizers.extractive import (
    SumySummarizer,
    TruncationSummarizer,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.summarizers.contracts import (
        SummarizerInterface,
    )

logger = logging.getLogger(__name__)

# 内容类型到策略链的映射
# 按优先级排序: 越靠前的策略越智能
STRATEGY_CHAIN: dict[str, list[SummaryStrategy]] = {
    "dialogue": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.GENERATIVE,  # BART/Qwen
        SummaryStrategy.EXTRACTIVE,  # sumy
        SummaryStrategy.TRUNCATION,
    ],
    "code": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.STRUCTURED,  # tree-sitter
        SummaryStrategy.EXTRACTIVE,  # sumy on docstrings
        SummaryStrategy.TRUNCATION,
    ],
    "log": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.EXTRACTIVE,  # sumy (error keywords)
        SummaryStrategy.TRUNCATION,
    ],
    "json": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.STRUCTURED,  # tree-sitter / JSON folding
        SummaryStrategy.TRUNCATION,
    ],
    "error": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.EXTRACTIVE,  # sumy (critical keywords)
        SummaryStrategy.TRUNCATION,
    ],
    "default": [
        SummaryStrategy.SLM,  # Local small LLM semantic compression
        SummaryStrategy.EXTRACTIVE,
        SummaryStrategy.TRUNCATION,
    ],
}

# 关键错误关键字 (用于质量验证)
CRITICAL_KEYWORDS = frozenset(
    {
        "error",
        "exception",
        "failed",
        "failure",
        "crash",
        "abort",
        "timeout",
        "deadlock",
        "corruption",
        "invalid",
        "missing",
        "permission denied",
        "not found",
        "unable to",
        "cannot",
        "traceback",
        "stacktrace",
    }
)


class TieredSummarizer:
    """分层摘要器

    根据内容类型和可用性自动选择最佳摘要策略。
    支持运行时降级，确保在任何情况下都有输出。

    Example:
        ```python
        summarizer = TieredSummarizer()

        # 自动选择最佳策略
        summary = summarizer.summarize(
            content=long_log,
            max_tokens=500,
            content_type="log",
        )

        # 获取统计信息
        stats = summarizer.get_fallback_stats()
        ```
    """

    def __init__(self, enable_tracking: bool = True, max_iterations: int = 3) -> None:
        """初始化分层摘要器

        延迟初始化各策略实现，避免导入时加载 heavy 依赖。

        Args:
            enable_tracking: 是否启用压缩状态追踪
            max_iterations: 最大迭代压缩次数
        """
        self._summarizers: dict[SummaryStrategy, SummarizerInterface | None] = {
            SummaryStrategy.EXTRACTIVE: None,  # 延迟初始化
            SummaryStrategy.TRUNCATION: TruncationSummarizer(),  # 总是可用
            SummaryStrategy.STRUCTURED: None,  # 延迟初始化
            SummaryStrategy.GENERATIVE: None,  # 延迟初始化
            SummaryStrategy.ORCHESTRATED: None,  # 延迟初始化
        }

        # 降级统计
        self._fallback_stats: dict[SummaryStrategy, int] = defaultdict(int)
        self._success_stats: dict[SummaryStrategy, int] = defaultdict(int)

        # 压缩状态追踪
        self._enable_tracking = enable_tracking
        self._tracker: Any | None = None

        # 迭代压缩配置
        self._max_iterations = max_iterations

    def _get_summarizer(self, strategy: SummaryStrategy) -> SummarizerInterface | None:
        """获取或初始化指定策略的摘要器

        Args:
            strategy: 摘要策略

        Returns:
            摘要器实例，如果不可用则返回 None
        """
        summarizer = self._summarizers.get(strategy)
        if summarizer is not None:
            return summarizer

        # 延迟初始化
        if strategy == SummaryStrategy.EXTRACTIVE:
            try:
                summarizer = SumySummarizer()
                if summarizer.is_available():
                    self._summarizers[strategy] = summarizer
                    return summarizer
            except (ImportError, ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("SumySummarizer not available (%s): %s", type(e).__name__, e)

        elif strategy == SummaryStrategy.STRUCTURED:
            try:
                from polaris.kernelone.context.context_os.summarizers.structured import (
                    TreeSitterSummarizer,
                )

                summarizer = TreeSitterSummarizer()
                if summarizer.is_available():
                    self._summarizers[strategy] = summarizer
                    return summarizer
            except (ImportError, ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("TreeSitterSummarizer not available (%s): %s", type(e).__name__, e)

        elif strategy == SummaryStrategy.GENERATIVE:
            # TODO: 实现 TransformersSummarizer
            pass

        elif strategy == SummaryStrategy.ORCHESTRATED:
            try:
                from polaris.kernelone.context.context_os.summarizers.semantic import (
                    LLMLinguaSummarizer,
                )

                summarizer = LLMLinguaSummarizer()
                if summarizer.is_available():
                    self._summarizers[strategy] = summarizer
                    return summarizer
            except (ImportError, ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("LLMLinguaSummarizer not available (%s): %s", type(e).__name__, e)

        elif strategy == SummaryStrategy.SLM:
            try:
                from polaris.kernelone.context.context_os.summarizers.slm import (
                    SLMSummarizer,
                )

                summarizer = SLMSummarizer()
                if summarizer.is_available():
                    self._summarizers[strategy] = summarizer
                    return summarizer
            except (ImportError, ConnectionError, TimeoutError, ValueError) as e:
                logger.debug("SLMSummarizer not available (%s): %s", type(e).__name__, e)

        return None

    def _get_tracker(self) -> Any | None:
        """获取或初始化压缩状态追踪器

        Returns:
            CompressionStateTracker 实例
        """
        if self._tracker is not None:
            return self._tracker

        if not self._enable_tracking:
            return None

        try:
            from polaris.kernelone.context.context_os.compression_tracker import (
                CompressionStateTracker,
            )

            self._tracker = CompressionStateTracker()
            return self._tracker
        except ImportError:
            logger.debug("CompressionStateTracker not available")
            return None

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
        force_strategy: SummaryStrategy | None = None,
    ) -> str:
        """生成摘要 (自动降级 + 迭代压缩)

        按照策略链依次尝试，直到成功。
        如果压缩后仍超过目标预算，使用迭代压缩逐步达标。

        Args:
            content: 原始内容
            max_tokens: 目标 token 数
            content_type: 内容类型 (text, code, log, json, dialogue, error)
            force_strategy: 强制使用指定策略 (用于调试)

        Returns:
            摘要后的内容

        Raises:
            SummarizationError: 当所有策略都失败时
        """
        if not content:
            return content

        # 内容太短，无需摘要
        if len(content.strip()) < 100:
            return content

        # 确定策略链
        strategies = [force_strategy] if force_strategy else STRATEGY_CHAIN.get(content_type, STRATEGY_CHAIN["default"])

        last_error = None
        current_content = content
        current_max_tokens = max_tokens

        # 迭代压缩循环
        for iteration in range(self._max_iterations):
            if iteration > 0:
                logger.debug(f"Iterative compression iteration {iteration + 1}")

            for strategy in strategies:
                summarizer = self._get_summarizer(strategy)
                if summarizer is None:
                    continue

                start_time = time.time()
                try:
                    result = summarizer.summarize(current_content, current_max_tokens, content_type)

                    # 质量验证
                    if self._validate_result(result, current_content, current_max_tokens):
                        self._success_stats[strategy] += 1
                        duration_ms = (time.time() - start_time) * 1000

                        # 记录压缩结果
                        if self._enable_tracking and self._tracker:
                            assert self._tracker is not None
                            content_hash = self._tracker.compute_hash(content)
                            self._tracker.record(
                                content_hash=content_hash,
                                original_size=len(content),
                                compressed_size=len(result),
                                strategy=strategy.name,
                                content_type=content_type,
                                duration_ms=duration_ms,
                            )

                        logger.debug(f"Summarization succeeded with {strategy.name}")

                        # 检查是否需要进一步压缩
                        estimated_tokens = self._estimate_tokens(result)
                        if estimated_tokens <= current_max_tokens or iteration >= self._max_iterations - 1:
                            return result

                        # 需要进一步压缩: 减小目标并继续迭代
                        current_content = result
                        current_max_tokens = int(current_max_tokens * 0.8)  # 每次减少20%
                        break  # 用新的内容重新尝试策略链

                    else:
                        logger.debug(f"Summarization validation failed for {strategy.name}")
                        self._fallback_stats[strategy] += 1

                except (RuntimeError, ValueError, SummarizationError) as e:
                    logger.debug(f"Summarizer {strategy.name} failed: {e}")
                    self._fallback_stats[strategy] += 1
                    last_error = e
                    continue

        # 所有策略都失败
        raise SummarizationError(
            f"All summarization strategies failed. Last error: {last_error}",
            strategy=None,
        )

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count considering CJK characters.

        Args:
            text: Text content.

        Returns:
            Estimated token count.
        """
        if not text:
            return 0
        # CJK characters typically consume 1-2 tokens each
        cjk_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        non_cjk_chars = len(text) - cjk_chars
        return max(1, cjk_chars // 2 + non_cjk_chars // 4)

    def _validate_result(
        self,
        result: str,
        original: str,
        max_tokens: int,
    ) -> bool:
        """验证摘要结果质量

        检查项:
        1. 非空且长度合理
        2. 没有丢失关键错误信息
        3. 输出长度符合预算

        Args:
            result: 生成的摘要
            original: 原始内容
            max_tokens: 目标 token 数

        Returns:
            True 如果结果质量可接受
        """
        if not result or len(result) < 10:
            return False

        # 检查关键错误信息是否丢失
        original_lower = original.lower()
        result_lower = result.lower()

        original_has_critical = any(kw in original_lower for kw in CRITICAL_KEYWORDS)
        result_has_critical = any(kw in result_lower for kw in CRITICAL_KEYWORDS)

        if original_has_critical and not result_has_critical:
            logger.warning("Summary lost critical error keywords")
            return False

        # 检查长度 (粗略估计: 1 token ≈ 4 chars)
        max_chars = max_tokens * 4
        if len(result) > max_chars * 1.2:  # 允许 20% 溢出
            logger.warning(f"Summary too long: {len(result)} chars > {max_chars} target")
            return False

        return True

    def get_fallback_stats(self) -> dict[str, dict[SummaryStrategy, int]]:
        """获取降级统计信息

        Returns:
            各策略的失败次数
        """
        return {
            "fallbacks": dict(self._fallback_stats),
            "successes": dict(self._success_stats),
        }

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._fallback_stats.clear()
        self._success_stats.clear()

    def get_available_strategies(self) -> list[SummaryStrategy]:
        """获取当前可用的策略列表

        Returns:
            可用策略列表
        """
        available = []
        for strategy in SummaryStrategy:
            summarizer = self._get_summarizer(strategy)
            if summarizer is not None and summarizer.is_available():
                available.append(strategy)
        return available
