"""Summarizer Interface Contracts - 摘要器接口契约

ADR-0067: ContextOS 2.0 摘要策略选型
定义统一的摘要器接口，支持多种实现和运行时降级。
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable


class SummaryStrategy(Enum):
    """摘要策略枚举

    按优先级排序: 越靠前的策略越智能，但越可能失败/耗时
    """

    GENERATIVE = auto()  # transformers (BART/Qwen) - 生成式摘要
    SLM = auto()  # Small Language Model - 本地/边缘认知压缩
    ORCHESTRATED = auto()  # LangChain Map-Reduce - 超长文档
    STRUCTURED = auto()  # Tree-sitter AST - 代码感知
    EXTRACTIVE = auto()  # sumy (TextRank) - 抽取式摘要
    TRUNCATION = auto()  # 紧急截断 - 绝对安全网


class SummarizationError(Exception):
    """摘要生成错误

    当摘要器无法生成满足要求的摘要时抛出。
    """

    def __init__(self, message: str, strategy: SummaryStrategy | None = None) -> None:
        super().__init__(message)
        self.strategy = strategy


@runtime_checkable
class SummarizerInterface(Protocol):
    """摘要器统一接口

    所有摘要器实现必须遵循此接口，以便在 TieredSummarizer 中互换使用。

    Example:
        ```python
        summarizer = SumySummarizer()
        result = summarizer.summarize(
            content=long_text,
            max_tokens=500,
            content_type="log",
        )
        ```
    """

    strategy: SummaryStrategy

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """生成摘要

        Args:
            content: 原始内容
            max_tokens: 目标 token 数 (指导值，不是硬截断)
            content_type: 内容类型 (text, code, log, json, dialogue)

        Returns:
            摘要后的内容

        Raises:
            SummarizationError: 当无法生成满足要求的摘要时
        """
        ...

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数

        用于预算规划，帮助调用方决定是否启用摘要。

        Args:
            input_tokens: 输入内容的 token 数

        Returns:
            预估的输出 token 数
        """
        ...

    def is_available(self) -> bool:
        """检查摘要器是否可用

        用于检查依赖是否安装 (如 transformers, tree-sitter)。

        Returns:
            True 如果摘要器可以正常使用
        """
        ...
