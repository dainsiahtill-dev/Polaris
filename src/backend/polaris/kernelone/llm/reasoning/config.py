"""Unified reasoning tag configuration for consistent parsing across modules.

This module provides a single source of truth for all reasoning/thinking tags
used by LLM providers, ensuring consistent matching and handling.
"""

from __future__ import annotations

from typing import Final

# All supported opening tag prefixes (case-insensitive matching)
OPEN_TAG_PREFIXES: Final[tuple[str, ...]] = (
    "think",
    "thinking",
    "thought",
    "reasoning",
    "reflection",
    "answer",
    "output",
)

# Chinese tag equivalents
CHINESE_TAG_PREFIXES: Final[tuple[str, ...]] = (
    "思考",
    "thoughts",
    "回答",
    "结果",
)

# All supported tag prefixes combined
ALL_TAG_PREFIXES: Final[tuple[str, ...]] = OPEN_TAG_PREFIXES + CHINESE_TAG_PREFIXES

# Tag suffixes that indicate answer/output sections
ANSWER_PREFIXES: Final[tuple[str, ...]] = (
    "answer",
    "output",
    "回答",
    "结果",
)

# Tag suffixes that indicate thinking/reasoning sections
THINK_PREFIXES: Final[tuple[str, ...]] = (
    "think",
    "thinking",
    "thought",
    "reasoning",
    "reflection",
    "思考",
    "thoughts",
)


def is_answer_tag(tag_name: str) -> bool:
    """Check if a tag name represents an answer/output section."""
    return tag_name.lower() in tuple(p.lower() for p in ANSWER_PREFIXES)


def is_think_tag(tag_name: str) -> bool:
    """Check if a tag name represents a thinking/reasoning section."""
    return tag_name.lower() in tuple(p.lower() for p in THINK_PREFIXES)


def normalize_tag_name(tag_name: str) -> str:
    """Normalize a tag name to lowercase for consistent comparison."""
    return tag_name.strip().lower()


# Build regex patterns dynamically from configuration
def build_open_tag_pattern() -> str:
    """Build regex pattern for opening tags."""
    # Build a pattern that matches <prefix> or <prefix attr="value">
    # Support both English and Chinese tags
    all_prefixes = list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)
    prefix_group = "|".join(all_prefixes)
    # Match: <prefix> or <prefix any="attributes">
    # Use word boundary for English, but not for Chinese characters
    return rf"<({prefix_group})(?:\s[^>]*)?>"


def build_close_tag_pattern() -> str:
    """Build regex pattern for closing tags."""
    all_prefixes = list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)
    prefix_group = "|".join(all_prefixes)
    return rf"</({prefix_group})>"


# Singleton patterns (compiled once)
OPEN_TAG_PATTERN: Final[str] = build_open_tag_pattern()
CLOSE_TAG_PATTERN: Final[str] = build_close_tag_pattern()


__all__ = [
    "ALL_TAG_PREFIXES",
    "ANSWER_PREFIXES",
    "CHINESE_TAG_PREFIXES",
    "CLOSE_TAG_PATTERN",
    "OPEN_TAG_PATTERN",
    "OPEN_TAG_PREFIXES",
    "THINK_PREFIXES",
    "build_close_tag_pattern",
    "build_open_tag_pattern",
    "is_answer_tag",
    "is_think_tag",
    "normalize_tag_name",
]
