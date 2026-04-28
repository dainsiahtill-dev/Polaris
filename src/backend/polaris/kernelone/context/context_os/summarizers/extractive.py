"""Extractive Summarization - 抽取式摘要实现

基于 sumy 的 TextRank/LexRank 算法实现。
ADR-0067: ContextOS 2.0 摘要策略选型 - Tier 2 安全摘要层

特点:
- 零幻觉: 直接抽取原文句子，不会篡改技术细节
- 极速: CPU 上毫秒级响应
- 轻量: 纯 Python，无 ML 依赖
- 安全: 适合处理日志、错误堆栈等技术内容
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummaryStrategy,
)
from sumy.nlp.tokenizers import Tokenizer  # type: ignore[attr-defined]
from sumy.parsers.plaintext import PlaintextParser  # type: ignore[attr-defined]
from sumy.summarizers.text_rank import TextRankSummarizer  # type: ignore[attr-defined]

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 关键错误关键字 (大小写不敏感)
# 这些关键字在摘要时会优先保留
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
        "caused by",
        "root cause",
    }
)


class SumySummarizer:
    """基于 sumy 的抽取式摘要器

    使用 TextRank 算法选择最重要的句子。
    针对技术内容优化: 优先保留包含错误关键字的句子。

    Example:
        ```python
        summarizer = SumySummarizer()
        summary = summarizer.summarize(
            content=long_log,
            max_tokens=300,
            content_type="log",
        )
        ```
    """

    strategy = SummaryStrategy.EXTRACTIVE

    def __init__(
        self,
        default_sentence_count: int = 5,
        language: str = "chinese",
    ) -> None:
        """初始化 SumySummarizer

        Args:
            default_sentence_count: 默认抽取句子数
            language: 语言 (english, chinese, japanese, etc.)
        """
        self.default_sentence_count = default_sentence_count
        self.language = language
        self._summarizer: Any | None = None
        self._parser_class: Any | None = None

    def _ensure_dependencies(self) -> None:
        """Ensure sumy classes are assigned for summarization."""
        if self._summarizer is not None:
            return
        self._parser_class = PlaintextParser
        self._tokenizer_class = Tokenizer
        self._summarizer_class = TextRankSummarizer

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """生成抽取式摘要

        算法:
        1. 使用 TextRank 计算句子重要性
        2. 优先保留包含关键错误信息的句子
        3. 保持原始顺序 (不重新排序句子)

        Args:
            content: 原始内容
            max_tokens: 目标 token 数
            content_type: 内容类型 (影响句子数计算)

        Returns:
            摘要后的内容
        """
        if not content or len(content.strip()) < 100:
            # 内容太短，无需摘要
            return content

        self._ensure_dependencies()

        # 计算目标句子数
        sentence_count = self._calculate_sentence_count(content, max_tokens, content_type)

        try:
            # 解析文本
            assert self._parser_class is not None and self._tokenizer_class is not None
            parser = self._parser_class.from_string(
                content,
                self._tokenizer_class(self.language),
            )

            # 创建 TextRank 摘要器
            summarizer = self._summarizer_class()

            # 获取摘要句子 (按重要性排序)
            summary_sentences = summarizer(parser.document, sentence_count)

            if not summary_sentences:
                return content[: max_tokens * 4]  # Fallback to truncation

            # 关键增强: 检查是否丢失了重要信息
            summary_text = " ".join(str(s) for s in summary_sentences)
            enhanced_summary = self._ensure_critical_keywords(content, summary_text)

            return enhanced_summary

        except (RuntimeError, ValueError) as e:
            logger.warning(f"Sumy summarization failed: {e}")
            raise SummarizationError(
                f"Failed to generate summary: {e}",
                strategy=self.strategy,
            ) from e

    def _calculate_sentence_count(
        self,
        content: str,
        max_tokens: int,
        content_type: str,
    ) -> int:
        """计算目标句子数

        基于内容类型和 token 预算动态调整。

        Args:
            content: 原始内容
            max_tokens: 目标 token 数
            content_type: 内容类型

        Returns:
            建议抽取的句子数
        """
        # 估算总句子数
        total_sentences = content.count("。") + content.count(".") + content.count("!") + content.count("?")
        total_sentences = max(1, total_sentences)

        # 基于内容类型的调整因子
        type_factors = {
            "log": 0.3,  # 日志通常信息密度高，保留更多句子
            "code": 0.2,  # 代码使用结构化摘要器更好，这里保守抽取
            "dialogue": 0.4,  # 对话信息密度低，保留更多
            "json": 0.25,
            "text": 0.35,
        }
        factor = type_factors.get(content_type, 0.35)

        # 基于 token 预算的计算
        # 假设平均句子长度: 15 tokens
        tokens_per_sentence = 15
        budget_based_count = max_tokens // tokens_per_sentence

        # 综合计算
        suggested_count = min(
            int(total_sentences * factor),
            budget_based_count,
            self.default_sentence_count,
        )

        return max(2, suggested_count)  # 至少保留 2 句

    def _ensure_critical_keywords(self, original: str, summary: str) -> str:
        """确保关键信息不被丢失

        如果原文包含错误关键字但摘要中没有，强制追加。

        Args:
            original: 原始内容
            summary: 生成的摘要

        Returns:
            增强后的摘要
        """
        original_lower = original.lower()
        summary_lower = summary.lower()

        missing_keywords = []
        for keyword in CRITICAL_KEYWORDS:
            if keyword in original_lower and keyword not in summary_lower:
                # 找到包含该关键字的句子
                pattern = rf"[^.!?]*{re.escape(keyword)}[^.!?]*[.!?]"
                matches = re.findall(pattern, original, re.IGNORECASE)
                if matches:
                    missing_keywords.append(matches[0].strip())

        if missing_keywords:
            # 追加关键句子
            enhanced = summary + "\n\n[Critical Info] " + " ".join(missing_keywords[:2])
            return enhanced

        return summary

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数

        抽取式摘要通常保留 20-40% 的内容。

        Args:
            input_tokens: 输入 token 数

        Returns:
            预估输出 token 数
        """
        # 保守估计: 30% 保留率
        return int(input_tokens * 0.3)

    def is_available(self) -> bool:
        """检查 sumy 是否已安装"""
        try:
            return True
        except ImportError:
            return False


class TruncationSummarizer:
    """紧急截断摘要器

    当所有其他摘要器失败时的绝对安全网。
    不是真正的摘要，而是智能截断。

    ADR-0067: Tier 3 紧急回退层
    """

    strategy = SummaryStrategy.TRUNCATION

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """智能截断

        策略:
        1. 保留开头 (70%)
        2. 保留结尾 (30%)
        3. 中间用省略号标记

        针对错误日志: 优先保留包含 "Error" 的行
        """
        if not content:
            return content

        # 估算字符数 (1 token ≈ 4 chars)
        max_chars = max_tokens * 4

        if len(content) <= max_chars:
            return content

        # 检查是否是结构化内容 (如日志)
        lines = content.split("\n")

        if content_type in ("log", "error") and len(lines) > 10:
            return self._smart_log_truncate(lines, max_chars)

        # 默认截断策略
        head_chars = int(max_chars * 0.7)
        tail_chars = max_chars - head_chars - 50  # 50 for marker

        head = content[:head_chars]
        tail = content[-tail_chars:] if tail_chars > 0 else ""

        omitted = len(content) - head_chars - tail_chars
        return f"{head}\n...[TRUNCATED: {omitted} chars]...\n{tail}"

    def _smart_log_truncate(self, lines: list[str], max_chars: int) -> str:
        """智能日志截断

        优先保留:
        1. 前 N 行 (上下文)
        2. 包含错误关键字的行
        3. 最后几行 (结论)
        """
        # 收集关键行 (包含错误关键字)
        critical_lines = []
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in CRITICAL_KEYWORDS):
                critical_lines.append((i, line))

        # 策略: 开头 + 关键行 + 结尾
        head_lines = lines[:5]
        tail_lines = lines[-3:] if len(lines) > 8 else []

        selected = set()
        result_lines = []

        # 添加开头
        for line in head_lines:
            result_lines.append(line)
            selected.add(id(line))

        # 添加关键行 (带上下文)
        for idx, line in critical_lines[:5]:  # 最多 5 个关键行
            # 添加上下文 (前后各 1 行)
            for ctx_idx in range(max(0, idx - 1), min(len(lines), idx + 2)):
                ctx_line = lines[ctx_idx]
                if id(ctx_line) not in selected:
                    result_lines.append(f"  {ctx_line}")  # 缩进表示上下文
                    selected.add(id(ctx_line))
            if line not in selected:
                result_lines.append(line)
                selected.add(id(line))

        # 添加结尾
        for line in tail_lines:
            if id(line) not in selected:
                result_lines.append(line)

        result = "\n".join(result_lines)

        # 如果还是太长，硬截断
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...[TRUNCATED]..."

        return result

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """直接返回目标 token 数"""
        return input_tokens  # 由调用方控制

    def is_available(self) -> bool:
        """总是可用"""
        return True
