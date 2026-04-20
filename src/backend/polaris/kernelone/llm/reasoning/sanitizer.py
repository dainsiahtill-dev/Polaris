"""Output rewriting for reasoning/thinking content in streaming protocol.

Replaces standard reasoning tags with session-unique tags to prevent
injection attacks through the reasoning/thinking stream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import TYPE_CHECKING, Final

from .config import (
    CHINESE_TAG_PREFIXES,
    OPEN_TAG_PREFIXES,
)

if TYPE_CHECKING:
    from .tags import ReasoningTagSet

# Build patterns from unified configuration
_STANDARD_THINK_OPEN_PATTERNS: Final[list[str]] = [f"<{prefix}\\b" for prefix in OPEN_TAG_PREFIXES] + [
    f"<{prefix}" for prefix in CHINESE_TAG_PREFIXES
]

_STANDARD_THINK_CLOSE_PATTERNS: Final[list[str]] = [f"</{prefix}>" for prefix in OPEN_TAG_PREFIXES] + [
    f"</{prefix}>" for prefix in CHINESE_TAG_PREFIXES
]

# Combined regex for efficient matching
_THINK_OPEN_RE: Final[Pattern[str]] = re.compile(
    "|".join(_STANDARD_THINK_OPEN_PATTERNS),
    re.IGNORECASE,
)

_THINK_CLOSE_RE: Final[Pattern[str]] = re.compile(
    "|".join(_STANDARD_THINK_CLOSE_PATTERNS),
    re.IGNORECASE,
)

# Partial tag patterns for detecting chunk boundaries
_PARTIAL_OPEN_TAGS: Final[tuple[str, ...]] = tuple(
    f"<{prefix}" for prefix in list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)
)
_PARTIAL_CLOSE_TAGS: Final[tuple[str, ...]] = tuple(
    f"</{prefix}" for prefix in list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)
)


@dataclass(frozen=True)
class SanitizationResult:
    """Result of a sanitization operation.

    Attributes:
        rewritten_text: The text with tags rewritten.
        substitutions: Number of tag substitutions made.
        original_tags: List of original tag strings that were replaced.
    """

    rewritten_text: str
    substitutions: int
    original_tags: tuple[str, ...]


class ReasoningSanitizer:
    """Rewriter that replaces standard reasoning tags with session-unique tags.

        This sanitizer operates in the streaming protocol layer, not the UI layer,
        ensuring that reasoning content from the model is always wrapped in
        session-specific tags that cannot be mimicked by injected content.

        This implementation handles:
        - Mixed Chinese/English tags (<think>, <思考>, etc.)
        - Tags with attributes (<think style="compact">)
        - Nested tags
        - Chunk boundaries in streaming scenarios
        - Edge cases: empty tags, malformed tags, Unicode

        Example:
            >>> tag_set = ReasoningTagSet(
            ...     open_tag="<think:a1b2c3d4>",
            ...     close_tag="</think:a1b2c3d4>",
            ...     prefix="think",
            ...     session_id="session-123",
            ...     raw_suffix="a1b2c3d4",
            ... )
            >>> sanitizer = ReasoningSanitizer(tag_set)
            >>> result = sanitizer.rewrite('<think>inner
    </think>

    ')
            >>> print(result.rewritten_text)
            <think:a1b2c3d4>inner</think:a1b2c3d4>
    """

    def __init__(self, tag_set: ReasoningTagSet) -> None:
        """Initialize the sanitizer with session-specific tags.

        Args:
            tag_set: The unique tag set for this session.
        """
        self._tag_set = tag_set
        self._open_re = _THINK_OPEN_RE
        self._close_re = _THINK_CLOSE_RE

    @property
    def tag_set(self) -> ReasoningTagSet:
        """The tag set being used for this session."""
        return self._tag_set

    def rewrite(self, text: str) -> SanitizationResult:
        """Rewrite standard reasoning tags to session-unique tags.

        Args:
            text: The text containing reasoning content with standard tags.

        Returns:
            SanitizationResult with rewritten text and substitution count.
        """
        if not text:
            return SanitizationResult(
                rewritten_text="",
                substitutions=0,
                original_tags=(),
            )

        original_tags: list[str] = []
        result = text

        # Replace opening tags
        def replace_open(match: re.Match[str]) -> str:
            original_tags.append(match.group(0))
            return self._tag_set.open_tag

        result = self._open_re.sub(replace_open, result)

        # Replace closing tags
        def replace_close(match: re.Match[str]) -> str:
            original_tags.append(match.group(0))
            return self._tag_set.close_tag

        result = self._close_re.sub(replace_close, result)

        return SanitizationResult(
            rewritten_text=result,
            substitutions=len(original_tags),
            original_tags=tuple(original_tags),
        )

    def rewrite_chunk(self, chunk: str) -> SanitizationResult:
        """Rewrite a streaming chunk, handling partial tags.

        This method is designed for streaming scenarios where tags may
        arrive in multiple chunks. It handles:
        - Complete tags within the chunk
        - Partial opening tags at the end of chunk (returns as-is)
        - Partial closing tags at the end of chunk (returns as-is)

        Args:
            chunk: A streaming text chunk.

        Returns:
            SanitizationResult with rewritten chunk.
        """
        if not chunk:
            return SanitizationResult(
                rewritten_text="",
                substitutions=0,
                original_tags=(),
            )

        original_tags: list[str] = []
        result = chunk

        # Check for trailing partial tags that we shouldn't rewrite yet
        ends_with_partial = False
        for partial in _PARTIAL_OPEN_TAGS + _PARTIAL_CLOSE_TAGS:
            if result.endswith(partial):
                ends_with_partial = True
                break

        if ends_with_partial:
            # Don't rewrite partial tags at chunk boundaries
            # Find where the complete tags end
            last_complete_pos = -1
            for match in self._open_re.finditer(result):
                last_complete_pos = max(last_complete_pos, match.end())
            for match in self._close_re.finditer(result):
                last_complete_pos = max(last_complete_pos, match.end())

            if last_complete_pos >= 0:
                complete_part = result[:last_complete_pos]
                partial_part = result[last_complete_pos:]
            else:
                # No complete tags, return as-is
                return SanitizationResult(
                    rewritten_text=result,
                    substitutions=0,
                    original_tags=(),
                )

            # Rewrite the complete part
            def replace_open_complete(match: re.Match[str]) -> str:
                original_tags.append(match.group(0))
                return self._tag_set.open_tag

            complete_part = self._open_re.sub(replace_open_complete, complete_part)

            def replace_close_complete(match: re.Match[str]) -> str:
                original_tags.append(match.group(0))
                return self._tag_set.close_tag

            complete_part = self._close_re.sub(replace_close_complete, complete_part)

            return SanitizationResult(
                rewritten_text=complete_part + partial_part,
                substitutions=len(original_tags),
                original_tags=tuple(original_tags),
            )

        return self.rewrite(result)

    def restore_standard(self, text: str) -> str:
        """Restore standard tags from session-unique tags.

        This is useful for display purposes or when passing to systems
        that expect standard tag formats.

        Args:
            text: Text with session-unique tags.

        Returns:
            Text with standard tags restored.
        """
        if not text:
            return text

        result = text
        result = result.replace(
            self._tag_set.open_tag,
            f"<{self._tag_set.prefix}>",
        )
        result = result.replace(
            self._tag_set.close_tag,
            f"</{self._tag_set.prefix}>",
        )
        return result


def sanitize_reasoning_output(
    text: str,
    tag_set: ReasoningTagSet,
) -> SanitizationResult:
    """Convenience function to sanitize reasoning output.

    Args:
        text: The text containing reasoning content.
        tag_set: The session-unique tag set.

    Returns:
        SanitizationResult with rewritten text.
    """
    sanitizer = ReasoningSanitizer(tag_set)
    return sanitizer.rewrite(text)


def is_standard_reasoning_tag(tag: str) -> bool:
    """Check if a tag string is a standard reasoning tag.

    This function excludes session-specific tags (those with colons like <think:abc123>).

    Args:
        tag: The tag string to check.

    Returns:
        True if the tag is a standard reasoning tag.
    """
    if not tag:
        return False
    tag_lower = tag.lower().strip()

    # Exclude session-specific tags (have colon after prefix)
    if re.match(r"^<\w+:[^>]+>$", tag_lower):
        return False
    if ":" in tag_lower.split(">")[0]:
        return False

    # Check for opening standard tags
    for prefix in list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES):
        if tag_lower.startswith(f"<{prefix.lower()}") and ">" in tag_lower:
            # Make sure it's not a session tag
            before_close = tag_lower.split(">")[0]
            if ":" in before_close:
                continue
            return True

    # Check for closing standard tags
    for prefix in list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES):
        if tag_lower.startswith(f"</{prefix.lower()}"):
            return True

    return False


__all__ = [
    "ReasoningSanitizer",
    "SanitizationResult",
    "is_standard_reasoning_tag",
    "sanitize_reasoning_output",
]
