"""Compression Quality Metrics - 压缩质量评估指标

ADR-0067: ContextOS 2.0 摘要策略选型

评估压缩质量的多维度指标：
- 语义保留度
- 关键信息覆盖率
- 压缩效率
- 信息密度
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompressionQualityMetrics:
    """压缩质量指标"""

    # 基础指标
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float

    # 语义保留度
    semantic_retention_rate: float  # 0.0-1.0, 句子级别的语义相似度
    key_entity_preservation: float  # 0.0-1.0, 关键实体保留率

    # 信息完整性
    critical_info_coverage: float  # 0.0-1.0, 关键信息覆盖率
    error_keyword_preservation: float  # 0.0-1.0, 错误关键字保留率

    # 效率指标
    information_density: float  # 信息密度 (有效信息 / 总长度)
    compression_gain: float  # 压缩收益 (原始信息量 / 压缩后信息量)

    # 综合评分
    overall_quality_score: float  # 0.0-100.0, 加权综合评分


class CompressionQualityEvaluator:
    """压缩质量评估器

    评估压缩结果的多维度质量指标。

    Example:
        ```python
        evaluator = CompressionQualityEvaluator()
        metrics = evaluator.evaluate(
            original=original_text,
            compressed=compressed_text,
            original_tokens=1000,
            compressed_tokens=300,
        )
        print(f"Quality score: {metrics.overall_quality_score:.1f}")
        ```
    """

    # 关键信息模式
    ERROR_PATTERNS = re.compile(
        r"\b(error|exception|failed|failure|crash|abort|timeout|"
        r"deadlock|corruption|traceback|stacktrace)\b",
        re.IGNORECASE,
    )

    CODE_PATTERNS = re.compile(
        r"\b(def|class|function|import|return|if|else|for|while|"
        r"try|except|with|async|await)\b",
        re.IGNORECASE,
    )

    # 关键实体模式 (文件名、函数名、变量名)
    ENTITY_PATTERN = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")

    def evaluate(
        self,
        original: str,
        compressed: str,
        original_tokens: int,
        compressed_tokens: int,
        content_type: str = "text",
    ) -> CompressionQualityMetrics:
        """评估压缩质量

        Args:
            original: 原始内容
            compressed: 压缩后内容
            original_tokens: 原始 token 数
            compressed_tokens: 压缩后 token 数
            content_type: 内容类型 (text, code, log, json)

        Returns:
            压缩质量指标
        """
        if not original or not compressed:
            return self._default_metrics(original_tokens, compressed_tokens)

        # 基础指标
        compression_ratio = compressed_tokens / max(original_tokens, 1) if original_tokens > 0 else 1.0

        # 语义保留度 (基于关键词重叠)
        semantic_retention = self._calc_semantic_retention(original, compressed)

        # 关键实体保留
        key_entity_preservation = self._calc_entity_preservation(original, compressed)

        # 关键信息覆盖率
        critical_info_coverage = self._calc_critical_coverage(original, compressed)

        # 错误关键字保留率
        error_keyword_preservation = self._calc_error_keyword_preservation(original, compressed)

        # 信息密度
        information_density = self._calc_information_density(compressed, content_type)

        # 压缩收益
        compression_gain = self._calc_compression_gain(original, compressed, compression_ratio)

        # 综合评分
        overall_score = self._calc_overall_score(
            semantic_retention=semantic_retention,
            key_entity_preservation=key_entity_preservation,
            critical_info_coverage=critical_info_coverage,
            error_keyword_preservation=error_keyword_preservation,
            compression_ratio=compression_ratio,
        )

        return CompressionQualityMetrics(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compression_ratio,
            semantic_retention_rate=semantic_retention,
            key_entity_preservation=key_entity_preservation,
            critical_info_coverage=critical_info_coverage,
            error_keyword_preservation=error_keyword_preservation,
            information_density=information_density,
            compression_gain=compression_gain,
            overall_quality_score=overall_score,
        )

    def _calc_semantic_retention(self, original: str, compressed: str) -> float:
        """计算语义保留度

        基于句子级别的关键词重叠率。
        """
        # 提取句子
        original_words = set(original.lower().split())
        compressed_words = set(compressed.lower().split())

        if not original_words:
            return 1.0

        # 计算重叠率
        overlap = original_words & compressed_words
        retention = len(overlap) / max(len(original_words), 1)

        return min(1.0, retention)

    def _calc_entity_preservation(self, original: str, compressed: str) -> float:
        """计算关键实体保留率

        检查变量名、函数名等标识符是否保留。
        """
        original_entities = set(self.ENTITY_PATTERN.findall(original))
        compressed_entities = set(self.ENTITY_PATTERN.findall(compressed))

        if not original_entities:
            return 1.0

        preserved = original_entities & compressed_entities
        preservation_rate = len(preserved) / max(len(original_entities), 1)

        return min(1.0, preservation_rate)

    def _calc_critical_coverage(self, original: str, compressed: str) -> float:
        """计算关键信息覆盖率

        识别并检查关键信息段是否保留。
        """
        # 关键信息模式: 错误信息、警告、重要的条件判断
        key_patterns = [
            self.ERROR_PATTERNS,
            re.compile(r"\b(important|critical|warning|must|required)\b", re.IGNORECASE),
            re.compile(r"\b(if|unless|when|until)\b.*?:", re.IGNORECASE),
        ]

        original_coverage = 0
        compressed_coverage = 0

        for pattern in key_patterns:
            original_matches = pattern.findall(original)
            compressed_matches = pattern.findall(compressed)
            original_coverage += len(original_matches)
            compressed_coverage += len(compressed_matches)

        if original_coverage == 0:
            return 1.0

        return min(1.0, compressed_coverage / original_coverage)

    def _calc_error_keyword_preservation(self, original: str, compressed: str) -> float:
        """计算错误关键字保留率"""
        original_errors = set(self.ERROR_PATTERNS.findall(original.lower()))
        compressed_errors = set(self.ERROR_PATTERNS.findall(compressed.lower()))

        if not original_errors:
            return 1.0

        preserved = original_errors & compressed_errors
        return len(preserved) / max(len(original_errors), 1)

    def _calc_information_density(self, content: str, content_type: str) -> float:
        """计算信息密度

        信息密度 = 有效信息长度 / 总长度
        """
        if not content:
            return 0.0

        # 移除空白和注释
        cleaned = re.sub(r"\s+", " ", content)
        cleaned = re.sub(r"#.*$", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"//.*$", "", cleaned, flags=re.MULTILINE)

        if content_type == "code":
            # 代码: 有效字符包括标识符、关键字、符号
            code_chars = len(re.sub(r"[^a-zA-Z0-9_()\[\]{}=+\-*/<>!.&|]", "", cleaned))
            density = code_chars / max(len(content), 1)
        elif content_type == "log":
            # 日志: 优先保留时间戳、级别、消息
            key_parts = re.findall(r"\d{4}-\d{2}-\d{2}|\[ERROR\]|\[WARN\]|\[INFO\]", cleaned)
            density = len(" ".join(key_parts)) / max(len(content), 1)
        else:
            # 文本: 字母比例
            letter_chars = sum(1 for c in cleaned if c.isalpha())
            density = letter_chars / max(len(content), 1)

        return min(1.0, density)

    def _calc_compression_gain(self, original: str, compressed: str, compression_ratio: float) -> float:
        """计算压缩收益

        综合考虑压缩率和信息保留。
        """
        if compression_ratio >= 1.0:
            return 0.0

        # 语义保留
        semantic_retention = self._calc_semantic_retention(original, compressed)

        # 收益 = 信息保留 * (1 - 压缩率)
        gain = semantic_retention * (1.0 - compression_ratio)

        return gain

    def _calc_overall_score(
        self,
        semantic_retention: float,
        key_entity_preservation: float,
        critical_info_coverage: float,
        error_keyword_preservation: float,
        compression_ratio: float,
    ) -> float:
        """计算综合质量评分

        加权平均:
        - 语义保留: 30%
        - 实体保留: 15%
        - 关键信息覆盖: 25%
        - 错误关键字保留: 20%
        - 压缩效率: 10%
        """
        # 压缩效率评分 (压缩率越低分数越高)
        efficiency_score = max(0.0, 1.0 - compression_ratio)

        score = (
            semantic_retention * 0.30
            + key_entity_preservation * 0.15
            + critical_info_coverage * 0.25
            + error_keyword_preservation * 0.20
            + efficiency_score * 0.10
        )

        return score * 100.0  # 转换为 0-100

    def _default_metrics(self, original_tokens: int, compressed_tokens: int) -> CompressionQualityMetrics:
        """返回默认指标 (当无法评估时)"""
        return CompressionQualityMetrics(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=1.0,
            semantic_retention_rate=0.0,
            key_entity_preservation=0.0,
            critical_info_coverage=0.0,
            error_keyword_preservation=0.0,
            information_density=0.0,
            compression_gain=0.0,
            overall_quality_score=0.0,
        )


# 全局评估器实例
_evaluator: CompressionQualityEvaluator | None = None


def get_evaluator() -> CompressionQualityEvaluator:
    """获取全局评估器实例"""
    global _evaluator
    if _evaluator is None:
        _evaluator = CompressionQualityEvaluator()
    return _evaluator
