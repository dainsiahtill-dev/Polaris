"""Polaris AI Platform - Unified Token Estimator

统一 Token 估算器，支持多种策略和模型特定的 tokenizer。
"""

from __future__ import annotations

import json
import logging
from typing import Any

# Core层契约导入
from polaris.kernelone.llm.toolkit.contracts import TokenEstimatorPort

_logger = logging.getLogger(__name__)


class TokenEstimator:
    """统一 Token 估算器。

    支持多种估算策略：
    - char_estimate: 基于字符数的启发式估算（默认）
    - cl100k_base: OpenAI GPT-4/GPT-3.5 系列
    - o200k_base: OpenAI GPT-4o 系列
    - anthropic: Claude 系列（近似）
    """

    # 启发式参数
    CHARS_PER_TOKEN = 4
    CJK_CHARS_PER_TOKEN = 2  # 中日韩字符通常占用更多 token
    CODE_CHARS_PER_TOKEN = 3  # 代码通常字符/token 比较低

    @classmethod
    def estimate(
        cls,
        text: str,
        *,
        content_type: str = "general",
        tokenizer_hint: str | None = None,
    ) -> int:
        """估算文本的 token 数量。

        Args:
            text: 输入文本
            content_type: 内容类型 (general/code/conversation/cjk)
            tokenizer_hint: 特定 tokenizer 提示 (如 "cl100k_base", "o200k_base")

        Returns:
            估算的 token 数量
        """
        if not text:
            return 0

        # 优先使用真实 tokenizer（如果可用且指定）
        if tokenizer_hint:
            real_count = cls._estimate_with_real_tokenizer(text, tokenizer_hint)
            if real_count is not None:
                return real_count
            _logger.warning(
                "token_estimator_fallback_to_heuristic: tokenizer_hint=%r text_len=%d",
                tokenizer_hint,
                len(text),
            )

        # 启发式估算
        return cls._heuristic_estimate(text, content_type)

    @classmethod
    def _heuristic_estimate(cls, text: str, content_type: str) -> int:
        """启发式估算。"""
        text_len = len(text)

        if text_len == 0:
            return 0

        # 检测 CJK 比例
        cjk_count = sum(
            1
            for c in text
            if "\u4e00" <= c <= "\u9fff"  # CJK 统一表意文字
            or "\u3040" <= c <= "\u309f"  # 平假名
            or "\u30a0" <= c <= "\u30ff"  # 片假名
            or "\uac00" <= c <= "\ud7af"  # 韩文音节
        )
        cjk_ratio = cjk_count / text_len

        # 根据内容类型选择参数
        chars_per_token: float
        if content_type == "code":
            chars_per_token = float(cls.CODE_CHARS_PER_TOKEN)
        elif content_type == "cjk" or cjk_ratio > 0.3:
            chars_per_token = float(cls.CJK_CHARS_PER_TOKEN)
        else:
            chars_per_token = float(cls.CHARS_PER_TOKEN)

        # 对于混合文本，使用加权平均
        if 0.05 < cjk_ratio < 0.3:
            chars_per_token = cjk_ratio * cls.CJK_CHARS_PER_TOKEN + (1 - cjk_ratio) * cls.CHARS_PER_TOKEN

        return max(1, int(text_len / chars_per_token))

    @classmethod
    def _estimate_with_real_tokenizer(cls, text: str, tokenizer_hint: str) -> int | None:
        """尝试使用真实 tokenizer 估算。"""
        try:
            if tokenizer_hint in ("cl100k_base", "gpt-4", "gpt-3.5", "gpt-4-turbo"):
                import tiktoken

                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))

            elif tokenizer_hint in ("o200k_base", "gpt-4o"):
                import tiktoken

                enc = tiktoken.get_encoding("o200k_base")
                return len(enc.encode(text))

        except ImportError as exc:
            # tiktoken 未安装，回退到启发式
            _logger.warning(
                "token_estimator_import_error: tiktoken unavailable, falling back to heuristic: %s",
                exc,
            )
        except (ValueError, TypeError) as exc:
            # 任何错误都回退到启发式
            _logger.warning(
                "token_estimator_estimation_failed: hint=%r falling back to heuristic: %s",
                tokenizer_hint,
                exc,
            )

        return None

    @classmethod
    def estimate_messages(
        cls,
        messages: list[dict[str, Any]],
        *,
        content_type: str = "conversation",
        tokenizer_hint: str | None = None,
    ) -> int:
        """估算消息列表的 token 数量。

        这是 RoleContextCompressor 专用方法，考虑消息格式开销。
        """
        if not messages:
            return 0

        # 消息格式开销估算（role, content 等字段）
        format_overhead = len(messages) * 4  # 每个消息约 4 tokens 格式开销

        # 序列化内容估算
        text_content = json.dumps(messages, default=str, ensure_ascii=False)
        content_tokens = cls.estimate(text_content, content_type=content_type, tokenizer_hint=tokenizer_hint)

        return format_overhead + content_tokens

    @classmethod
    def get_stats(cls, text: str) -> dict[str, Any]:
        """获取文本的详细统计信息（用于调试）。"""
        text_len = len(text)

        cjk_count = sum(
            1
            for c in text
            if "\u4e00" <= c <= "\u9fff"
            or "\u3040" <= c <= "\u309f"
            or "\u30a0" <= c <= "\u30ff"
            or "\uac00" <= c <= "\ud7af"
        )

        code_indicators = sum(1 for c in text if c in "{};()[]=<>+-*/%&|^~!")

        return {
            "char_count": text_len,
            "cjk_count": cjk_count,
            "cjk_ratio": round(cjk_count / text_len, 4) if text_len > 0 else 0,
            "code_indicators": code_indicators,
            "code_ratio": round(code_indicators / text_len, 4) if text_len > 0 else 0,
            "estimate_general": cls.estimate(text, content_type="general"),
            "estimate_code": cls.estimate(text, content_type="code"),
            "estimate_cjk": cls.estimate(text, content_type="cjk"),
        }


# 向后兼容的便捷函数
def estimate_tokens(text: str) -> int:
    """便捷函数：使用默认策略估算。"""
    return TokenEstimator.estimate(text)


# ═══════════════════════════════════════════════════════════════════
# Core层适配器 - 实现TokenEstimatorPort，解决循环依赖
# ═══════════════════════════════════════════════════════════════════


class TokenEstimatorAdapter(TokenEstimatorPort):
    """TokenEstimator适配器

    将app层的TokenEstimator适配为core层的TokenEstimatorPort，
    消除core层对app层的直接依赖，解决循环依赖问题。
    """

    def estimate_tokens(self, text: str, model: str | None = None) -> int:
        """估算文本的token数量"""
        tokenizer_hint = None
        if model:
            # 简单的模型到tokenizer的映射
            if "gpt-4o" in model.lower():
                tokenizer_hint = "o200k_base"
            elif "gpt-4" in model.lower() or "gpt-3.5" in model.lower():
                tokenizer_hint = "cl100k_base"
            elif "claude" in model.lower():
                tokenizer_hint = "anthropic"
        return TokenEstimator.estimate(text, tokenizer_hint=tokenizer_hint)

    def estimate_messages_tokens(self, messages: list[dict[str, str]], model: str | None = None) -> int:
        """估算消息列表的token数量"""
        tokenizer_hint = None
        if model:
            if "gpt-4o" in model.lower():
                tokenizer_hint = "o200k_base"
            elif "gpt-4" in model.lower() or "gpt-3.5" in model.lower():
                tokenizer_hint = "cl100k_base"
            elif "claude" in model.lower():
                tokenizer_hint = "anthropic"
        return TokenEstimator.estimate_messages(messages, tokenizer_hint=tokenizer_hint)


def ensure_token_estimator_registered() -> None:
    """Register the default token estimator explicitly when bootstrap needs it."""
    from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

    if ServiceLocator.get_token_estimator() is None:
        ServiceLocator.register_token_estimator(TokenEstimatorAdapter())


__all__ = [
    "TokenEstimator",
    "TokenEstimatorAdapter",
    "ensure_token_estimator_registered",
    "estimate_tokens",
]
