"""Context gateway token estimation - CJK-aware token counting.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from .constants import is_cjk_char


class TokenEstimator:
    """Estimates token counts with CJK-aware heuristics."""

    MESSAGE_OVERHEAD = 4

    def estimate(self, messages: list[dict[str, str]]) -> int:
        """Estimate token count for a list of messages.

        Args:
            messages: List of message dicts with 'content' key.

        Returns:
            Estimated token count (minimum 1).
        """
        total_tokens = 0

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                total_tokens += self.MESSAGE_OVERHEAD
                continue

            ascii_chars = 0
            cjk_chars = 0
            other_chars = 0

            for char in content:
                if ord(char) < 128:
                    ascii_chars += 1
                elif is_cjk_char(char):
                    cjk_chars += 1
                else:
                    other_chars += 1

            ascii_tokens = ascii_chars / 4.0
            cjk_tokens = cjk_chars * 1.5
            other_tokens = other_chars / 2.0

            content_tokens = ascii_tokens + cjk_tokens + other_tokens
            total_tokens += int(content_tokens) + self.MESSAGE_OVERHEAD

        return max(1, int(total_tokens))

    @staticmethod
    def compute_adaptive_threshold(budget_tokens: int, used_tokens: int) -> float:
        """Compute dynamic compression threshold based on token usage ratio.

        Args:
            budget_tokens: Maximum token budget.
            used_tokens: Currently used tokens.

        Returns:
            Threshold ratio between 0.5 and 0.9.
        """
        usage_ratio = used_tokens / budget_tokens if budget_tokens > 0 else 0.0
        if usage_ratio > 0.8:
            return 0.5
        if usage_ratio > 0.6:
            return 0.7
        return 0.9


__all__ = ["TokenEstimator"]
