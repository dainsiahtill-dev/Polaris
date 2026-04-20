"""Canonical token estimator for ContextOS — single source of truth."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate token count with CJK awareness.

    Uses the formula: ascii_chars/4 + cjk_chars*1.5
    This is the canonical implementation referenced by all ContextOS modules.
    """
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    cjk_chars = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4) + int(cjk_chars * 1.5))


__all__ = ["estimate_tokens"]
