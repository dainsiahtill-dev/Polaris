"""Semantic Summarization - LLMLingua 语义压缩实现

ADR-0067: ContextOS 2.0 摘要策略选型 - Tier 1 智能摘要层

基于 LLMLingua 的预算感知压缩，利用 token 重要性评分实现语义保留。

特点:
- 预算感知: 精确控制输出 token 数
- 语义保留: 基于困惑度的重要性评分
- 多层次压缩: document-level 和 context-level
- 查询感知: 保留与查询相关的内容 (可选)

依赖:
- llmlingua>=0.2.0
- transformers>=4.35.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummaryStrategy,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMLinguaConfig:
    """LLMLingua 压缩配置"""

    # 压缩率控制
    target_ratio: float = 0.5  # 目标压缩比例 (0.0-1.0)
    # 迭代压缩参数
    iterative_compression: bool = True
    n_iterations: int = 3
    # 保留关键内容
    keep_first_sentence: bool = True
    keep_last_sentence: bool = True
    # 查询感知 (如果提供 query，则保留相关内容)
    query_aware: bool = False


class LLMLinguaSummarizer:
    """基于 LLMLingua 的语义压缩摘要器

    使用 perplexity-based 重要性评分，在压缩预算内保留最关键信息。

    Example:
        ```python
        summarizer = LLMLinguaSummarizer()
        compressed = summarizer.summarize(
            content=long_document,
            max_tokens=500,
            content_type="text",
        )

        # 查询感知压缩
        compressed = summarizer.summarize_with_query(
            content=long_document,
            query="What is the error message?",
            max_tokens=300,
        )
        ```
    """

    strategy = SummaryStrategy.ORCHESTRATED

    def __init__(
        self,
        config: LLMLinguaConfig | None = None,
        model_name: str = "facebook/bart-large-cnn",
    ) -> None:
        """初始化 LLMLinguaSummarizer

        Args:
            config: LLMLingua 配置
            model_name: 用于重要性评分的模型名称
        """
        self.config = config or LLMLinguaConfig()
        self.model_name = model_name
        self._compressor: Any | None = None

    def _ensure_dependencies(self) -> None:
        """延迟加载 LLMLingua 依赖"""
        try:
            from llmlingua import PromptCompressor  # noqa: F401
        except ImportError as e:
            raise SummarizationError(
                "llmlingua not installed. Run: pip install llmlingua",
                strategy=self.strategy,
            ) from e

    def _get_compressor(self) -> Any | None:
        """获取或初始化 PromptCompressor

        Returns:
            PromptCompressor 实例
        """
        if self._compressor is None:
            self._ensure_dependencies()
            from llmlingua import PromptCompressor

            try:
                self._compressor = PromptCompressor(
                    model_name=self.model_name,
                    use_llmlingua2=False,  # 使用 v1 以兼容更多模型
                )
                logger.info(f"LLMLingua compressor initialized with {self.model_name}")
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Failed to initialize LLMLingua with {self.model_name}: {e}")
                # Fallback to smaller model
                self._compressor = PromptCompressor(
                    model_name="gpt2",  # Fallback to smaller model
                    use_llmlingua2=False,
                )
        return self._compressor

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """生成语义压缩摘要

        Args:
            content: 原始内容
            max_tokens: 目标 token 数
            content_type: 内容类型 (text, code, dialogue)

        Returns:
            压缩后的内容
        """
        if not content or len(content.strip()) < 100:
            return content

        # Code 和 JSON 使用结构化摘要更好
        if content_type in ("code", "json"):
            logger.debug("LLMLingua: code/json content, falling back to simple truncate")
            return self._simple_truncate(content, max_tokens)

        try:
            compressor = self._get_compressor()

            # 计算目标压缩率
            current_tokens = self._estimate_tokens(content)
            if current_tokens <= max_tokens:
                return content

            target_ratio = min(
                self.config.target_ratio,
                max_tokens / max(current_tokens, 1),
            )

            # 使用 LLMLingua 压缩
            assert compressor is not None
            result = compressor.compress_prompt(
                context=content,
                rate=target_ratio,
                iterative_compression=self.config.iterative_compression,
                iterative_size=self.config.n_iterations,
                keep_first_sentence=self.config.keep_first_sentence,
                keep_last_sentence=self.config.keep_last_sentence,
            )

            compressed = result.get("compressed_prompt", content)

            # 验证关键信息是否保留
            if not self._validate_critical_content(content, compressed):
                logger.warning("LLMLingua compressed lost critical content, using fallback")
                return self._simple_truncate(content, max_tokens)

            return compressed

        except (RuntimeError, ValueError) as e:
            logger.warning(f"LLMLingua compression failed: {e}")
            return self._simple_truncate(content, max_tokens)

    def summarize_with_query(
        self,
        content: str,
        query: str,
        max_tokens: int,
    ) -> str:
        """查询感知的语义压缩

        保留与查询最相关的内容。

        Args:
            content: 原始内容
            query: 查询/问题
            max_tokens: 目标 token 数

        Returns:
            压缩后的内容
        """
        if not content or len(content.strip()) < 100:
            return content

        if not query:
            return self.summarize(content, max_tokens)

        try:
            compressor = self._get_compressor()

            current_tokens = self._estimate_tokens(content)
            if current_tokens <= max_tokens:
                return content

            target_ratio = min(
                0.7,  # 查询感知压缩可以更保守
                max_tokens / max(current_tokens, 1),
            )

            # 使用 query-aware 压缩
            assert compressor is not None
            result = compressor.compress_prompt(
                context=content,
                question=query,
                rate=target_ratio,
                iterative_compression=self.config.iterative_compression,
                iterative_size=self.config.n_iterations,
                keep_first_sentence=True,
                keep_last_sentence=True,
            )

            return result.get("compressed_prompt", content)

        except (RuntimeError, ValueError) as e:
            logger.warning(f"LLMLingua query-aware compression failed: {e}")
            return self._simple_truncate(content, max_tokens)

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数

        Args:
            text: 输入文本

        Returns:
            估算的 token 数
        """
        # 粗略估算: 1 token ≈ 4 chars for English, 1 token ≈ 2 chars for Chinese
        char_count = len(text)
        # 混合语言估算
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        english_chars = char_count - chinese_chars

        estimated = (english_chars / 4) + (chinese_chars / 2)
        return int(estimated)

    def _validate_critical_content(self, original: str, compressed: str) -> bool:
        """验证关键内容是否保留

        Args:
            original: 原始内容
            compressed: 压缩后的内容

        Returns:
            True 如果关键内容保留
        """
        # 检查关键错误关键字
        critical_keywords = {
            "error",
            "exception",
            "failed",
            "failure",
            "crash",
            "abort",
            "timeout",
            "deadlock",
            "corruption",
            "traceback",
        }

        original_lower = original.lower()
        compressed_lower = compressed.lower()

        original_has = any(kw in original_lower for kw in critical_keywords)
        compressed_has = any(kw in compressed_lower for kw in critical_keywords)

        # 如果原始内容有关键词但压缩后没有，认为验证失败
        if original_has and not compressed_has:
            return False

        # 检查压缩率是否合理
        if len(compressed) > len(original) * 0.9:
            # 压缩率太低，可能没有实际压缩
            logger.debug("LLMLingua compression ratio too low")

        return True

    def _simple_truncate(self, content: str, max_tokens: int) -> str:
        """简单的行数截断 (fallback)

        Args:
            content: 原始内容
            max_tokens: 目标 token 数

        Returns:
            截断后的内容
        """
        lines = content.split("\n")
        max_lines = max(20, max_tokens // 4)

        if len(lines) <= max_lines:
            return content

        head_lines = int(max_lines * 0.7)
        tail_lines = max_lines - head_lines

        head = lines[:head_lines]
        tail = lines[-tail_lines:] if tail_lines > 0 else []

        return "\n".join([*head, "    // ... (truncated) ...", *tail])

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数

        LLMLingua 通常可以达到 50-70% 的压缩率。

        Args:
            input_tokens: 输入 token 数

        Returns:
            预估输出 token 数
        """
        return int(input_tokens * self.config.target_ratio)

    def is_available(self) -> bool:
        """检查 LLMLingua 是否已安装且可用"""
        try:
            from llmlingua import PromptCompressor  # noqa: F401

            return True
        except ImportError:
            return False
        except (RuntimeError, ValueError) as e:
            logger.debug(f"LLMLingua availability check failed: {e}")
            return False

    def get_compression_stats(self) -> dict[str, Any]:
        """获取压缩统计信息

        Returns:
            压缩统计数据
        """
        return {
            "model_name": self.model_name,
            "target_ratio": self.config.target_ratio,
            "iterative_compression": self.config.iterative_compression,
            "n_iterations": self.config.n_iterations,
            "available": self.is_available(),
        }
