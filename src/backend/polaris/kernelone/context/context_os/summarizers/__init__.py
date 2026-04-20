"""ContextOS Summarizers - 智能摘要模块

ADR-0067: ContextOS 2.0 摘要策略选型
- Tier 1: 智能摘要 (transformers, langchain)
- Tier 2: 安全摘要 (sumy, tree-sitter)
- Tier 3: 紧急回退 (truncation)
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummarizerInterface,
    SummaryStrategy,
)
from polaris.kernelone.context.context_os.summarizers.extractive import SumySummarizer, TruncationSummarizer
from polaris.kernelone.context.context_os.summarizers.semantic import LLMLinguaSummarizer
from polaris.kernelone.context.context_os.summarizers.slm import SLMSummarizer
from polaris.kernelone.context.context_os.summarizers.structured import TreeSitterSummarizer
from polaris.kernelone.context.context_os.summarizers.tiered import TieredSummarizer

__all__ = [
    "LLMLinguaSummarizer",
    "SLMSummarizer",
    "SummarizationError",
    "SummarizerInterface",
    "SummaryStrategy",
    "SumySummarizer",
    "TieredSummarizer",
    "TreeSitterSummarizer",
    "TruncationSummarizer",
]


def create_slm_summarizer(
    *,
    config: Any | None = None,
    timeout_seconds: float = 2.5,
    max_content_length: int = 8000,
    gateway: Any | None = None,
) -> SLMSummarizer:
    """Factory for creating an SLMSummarizer with optional gateway injection.

    Args:
        config: TransactionConfig with SLM settings. If None, uses defaults.
        timeout_seconds: Timeout for SLM calls (including health check).
        max_content_length: Pre-truncate content longer than this before sending to SLM.
        gateway: Optional gateway instance. If None, lazily creates one internally.

    Returns:
        Configured SLMSummarizer instance.
    """
    return SLMSummarizer(
        config=config,
        timeout_seconds=timeout_seconds,
        max_content_length=max_content_length,
        gateway=gateway,
    )
