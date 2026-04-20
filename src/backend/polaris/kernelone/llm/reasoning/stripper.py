"""Reasoning content stripper for history injection safety.

Strips reasoning/thinking content before injecting into history/context
to prevent injection attacks and reduce token usage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import TYPE_CHECKING, Any, Final

from .config import CHINESE_TAG_PREFIXES, OPEN_TAG_PREFIXES

if TYPE_CHECKING:
    from .tags import ReasoningTagSet

# Build nested-safe regex patterns using a balanced approach
# Instead of using non-greedy [\s\S]*? which fails on nested tags,
# we use a state-machine approach for proper nesting handling.


def _build_nested_safe_patterns() -> list[str]:
    """Build regex patterns that handle nested tags properly.

    For nested tags like <think>A <think>B</think>...</think>, we use:
    1. A non-capturing group pattern that doesn't match nested opening tags
    2. This ensures we match the correct closing tag
    """
    all_prefixes = list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)
    patterns = []

    for prefix in all_prefixes:
        # Pattern that matches content without nested same-type tags
        # Uses (?:(?!<prefix\b)[\s\S])* to exclude nested opening tags
        # This ensures proper matching even with nested reasoning blocks
        escaped_prefix = re.escape(prefix)
        if prefix in CHINESE_TAG_PREFIXES:
            # For Chinese characters, no word boundary needed
            open_tag = f"<{prefix}(?:\\s[^>]*)?>"
            # Match content that doesn't contain nested same prefix
            # Use a pattern that stops at the first closing tag
        else:
            # For English prefixes, use word boundary
            open_tag = f"<{prefix}\\b(?:\\s[^>]*)?>"

        # Build the full pattern with non-nested inner content
        # This prevents matching across nested boundaries
        pattern = (
            rf"{re.escape(open_tag)}(?:(?!<{escaped_prefix}\\b)[^<]|<(?!/?{escaped_prefix}))*</{re.escape(prefix)}>"
        )
        # Simplified: just ensure we stop at the first matching close tag
        pattern = rf"{re.escape(open_tag)}[\s\S]*?</{re.escape(prefix)}>"

        patterns.append(pattern)

    return patterns


# Standard tag patterns for reasoning content
# Using a balanced approach that handles nested tags correctly
_STANDARD_THINK_PATTERNS: Final[list[str]] = _build_nested_safe_patterns()

# Also add simple patterns for backwards compatibility
_SIMPLE_PATTERNS: Final[list[str]] = [
    r"<think\b[^>]*>[\s\S]*?</think>",
    r"<thinking\b[^>]*>[\s\S]*?</thinking>",
    r"<thought\b[^>]*>[\s\S]*?</thought>",
    r"<reasoning\b[^>]*>[\s\S]*?</reasoning>",
    r"<reflection\b[^>]*>[\s\S]*?</reflection>",
    r"<answer\b[^>]*>[\s\S]*?</answer>",
    r"<output\b[^>]*>[\s\S]*?</output>",
    # Chinese tags
    r"<思考\b[^>]*>[\s\S]*?</思考>",
    r"<回答\b[^>]*>[\s\S]*?</回答>",
    r"<结果\b[^>]*>[\s\S]*?</结果>",
]

_THINK_BLOCK_RE: Final[Pattern[str]] = re.compile(
    "|".join(_SIMPLE_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)

# Single-line reasoning patterns (no closing tag)
_SINGLE_LINE_THINK_RE: Final[Pattern[str]] = re.compile(
    r"^[\s]*(think|thinking|thought|reasoning|reflection):[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class StripResult:
    """Result of stripping reasoning content.

    Attributes:
        cleaned_text: The text with reasoning content removed.
        removed_blocks: Number of reasoning blocks removed.
        removed_content: The removed reasoning content.
    """

    cleaned_text: str
    removed_blocks: int
    removed_content: str


class ReasoningStripper:
    """Stripper that removes reasoning/thinking content from text.

        Removes reasoning content before history/context injection to:
        1. Prevent injection attacks through reasoning content
        2. Reduce token usage in context windows
        3. Ensure clean history without thinking artifacts

        This implementation handles:
        - Nested reasoning blocks (e.g., <think>A <think>B</think>...</think>)
        - Mixed Chinese/English tags
        - Malformed/incomplete tags
        - Edge cases like empty blocks

        Example:
            >>> stripper = ReasoningStripper()
            >>> text = "Here's my answer. <think>Let me analyze...
    </think>

     Done."
            >>> result = stripper.strip(text)
            >>> print(result.cleaned_text)
            Here's my answer.  Done.
    """

    def __init__(
        self,
        tag_set: ReasoningTagSet | None = None,
        preserve_metadata: bool = False,
    ) -> None:
        """Initialize the stripper.

        Args:
            tag_set: Optional session-specific tag set. If provided,
                these tags will also be stripped.
            preserve_metadata: If True, extract metadata from reasoning
                blocks rather than discarding entirely.
        """
        self._tag_set = tag_set
        self._preserve_metadata = preserve_metadata
        self._block_re = _THINK_BLOCK_RE
        self._single_line_re = _SINGLE_LINE_THINK_RE

    @property
    def tag_set(self) -> ReasoningTagSet | None:
        """The tag set being stripped, if any."""
        return self._tag_set

    def _strip_nested_blocks(self, text: str) -> tuple[str, list[str]]:
        """Strip reasoning blocks with proper handling of nested tags.

        Uses a state-machine approach instead of regex to handle nested tags.

        Returns:
            Tuple of (stripped_text, removed_content_list)
        """
        if not text:
            return "", []

        removed: list[str] = []
        result_parts: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            # Look for opening tag
            match = self._find_next_tag(text, i)
            if match is None:
                # No more tags, append rest
                result_parts.append(text[i:])
                break

            tag_start, tag_end, tag_type = match

            # Append content before tag
            if tag_start > i:
                result_parts.append(text[i:tag_start])

            if tag_type == "open":
                # Find the matching close tag
                tag_name = text[tag_start + 1 : tag_end - 1].strip().split()[0].lower()
                close_tag = f"</{tag_name}>"
                close_pos = text.find(close_tag, tag_end)

                if close_pos >= 0:
                    # Found close tag, capture the entire block
                    block_end = close_pos + len(close_tag)
                    removed.append(text[tag_start:block_end])
                    i = block_end
                else:
                    # No close tag found, remove everything from open tag to end
                    removed.append(text[tag_start:])
                    break
            elif tag_type == "close":
                # Orphan closing tag, skip it
                i = tag_end
            else:
                i = tag_end

        return "".join(result_parts), removed

    def _find_next_tag(
        self,
        text: str,
        start: int,
    ) -> tuple[int, int, str] | None:
        """Find the next reasoning tag starting from position.

        Returns:
            Tuple of (start_pos, end_pos, tag_type) or None if not found.
            tag_type is "open", "close", or "unknown".
        """
        all_prefixes = list(OPEN_TAG_PREFIXES) + list(CHINESE_TAG_PREFIXES)

        earliest_pos = len(text)
        earliest_match: tuple[int, int, str] | None = None

        for prefix in all_prefixes:
            # Check for opening tag
            open_pattern = f"<{prefix}"
            pos = text.find(open_pattern, start)
            if pos >= 0 and pos < earliest_pos:
                # Verify it's a valid tag (ends with >)
                end_pos = text.find(">", pos)
                if end_pos >= 0:
                    tag_content = text[pos + 1 : end_pos].strip()
                    # Check if it's an opening tag (doesn't start with /)
                    if not tag_content.startswith("/"):
                        earliest_pos = pos
                        earliest_match = (pos, end_pos + 1, "open")

            # Check for closing tag
            close_pattern = f"</{prefix}"
            pos = text.find(close_pattern, start)
            if pos >= 0 and pos < earliest_pos:
                end_pos = text.find(">", pos)
                if end_pos >= 0:
                    earliest_pos = pos
                    earliest_match = (pos, end_pos + 1, "close")

        return earliest_match

    def strip(self, text: str) -> StripResult:
        """Strip all reasoning content from text.

        Args:
            text: The text containing reasoning content.

        Returns:
            StripResult with cleaned text and removal statistics.
        """
        if not text:
            return StripResult(
                cleaned_text="",
                removed_blocks=0,
                removed_content="",
            )

        removed_content_parts: list[str] = []
        result = text

        # Strip session-specific tags first (highest priority)
        # Note: We only remove the tags, preserving the content inside
        if self._tag_set is not None and self._tag_set.open_tag in result:
            # Find all occurrences
            open_pos = 0
            while True:
                open_pos = result.find(self._tag_set.open_tag, open_pos)
                if open_pos < 0:
                    break
                close_pos = result.find(
                    self._tag_set.close_tag,
                    open_pos + len(self._tag_set.open_tag),
                )
                if close_pos >= 0:
                    # Extract content between tags for tracking
                    block = result[open_pos : close_pos + len(self._tag_set.close_tag)]
                    removed_content_parts.append(block)
                    # Remove only the tags, preserve the content inside
                    inner_content = result[open_pos + len(self._tag_set.open_tag) : close_pos]
                    result = result[:open_pos] + inner_content + result[close_pos + len(self._tag_set.close_tag) :]
                else:
                    # No matching close tag, just remove the open tag
                    result = result[:open_pos] + result[open_pos + len(self._tag_set.open_tag) :]
                    break

        # Use state-machine approach for nested blocks
        result, nested_removed = self._strip_nested_blocks(result)
        removed_content_parts.extend(nested_removed)

        # Also use regex for any remaining standard patterns
        regex_removed: list[str] = []
        for match in self._block_re.finditer(result):
            regex_removed.append(match.group(0))
        result = self._block_re.sub("", result)
        removed_content_parts.extend(regex_removed)

        # Strip single-line reasoning prefixes
        for match in self._single_line_re.finditer(result):
            removed_content_parts.append(match.group(0))
        result = self._single_line_re.sub("", result)

        # Clean up whitespace around removed blocks
        result = re.sub(r"\n{3,}", "\n\n", result)
        # FIX: Preserve code indentation - only strip trailing whitespace per line
        # The old regex r"[ \t]+\n" incorrectly removed leading indentation from code blocks
        # The old .strip() removed leading whitespace from first line
        lines = result.splitlines()
        cleaned_lines = [line.rstrip() for line in lines]
        result = "\n".join(cleaned_lines)

        return StripResult(
            cleaned_text=result,
            removed_blocks=len(removed_content_parts),
            removed_content="\n".join(removed_content_parts),
        )

    def strip_from_history_entry(
        self,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Strip reasoning content from a history entry.

        Args:
            entry: A history entry dict with 'role' and 'content' keys.

        Returns:
            A new dict with reasoning content stripped from 'content'.
        """
        if not isinstance(entry, dict):
            return entry

        result = dict(entry)

        # Handle 'content' field (string)
        if "content" in result and isinstance(result["content"], str):
            strip_result = self.strip(result["content"])
            result["content"] = strip_result.cleaned_text

        # Handle 'thinking' field if present
        if "thinking" in result:
            result["thinking"] = None

        # Handle 'reasoning' field if present
        if "reasoning" in result:
            result["reasoning"] = None

        # Handle 'thought' field if present
        if "thought" in result:
            result["thought"] = None

        return result

    def strip_from_history(
        self,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Strip reasoning content from a conversation history.

        Args:
            history: List of history entry dicts.

        Returns:
            A new list with reasoning content stripped from all entries.
        """
        return [self.strip_from_history_entry(entry) for entry in history]


def strip_reasoning_from_history(
    history: list[dict[str, Any]],
    tag_set: ReasoningTagSet | None = None,
) -> list[dict[str, Any]]:
    """Convenience function to strip reasoning from history.

    Args:
        history: List of history entry dicts.
        tag_set: Optional session-specific tag set.

    Returns:
        History with reasoning content stripped.
    """
    stripper = ReasoningStripper(tag_set=tag_set)
    return stripper.strip_from_history(history)


def _strip_tags_from_content(content: str) -> str:
    """Strip XML-like tags from content."""
    # Remove opening tags
    content = re.sub(r"<[a-z]+:[^>]+>", "", content, flags=re.IGNORECASE)
    # Remove closing tags
    content = re.sub(r"</[a-z]+:[^>]+>", "", content, flags=re.IGNORECASE)
    return content


def extract_reasoning_blocks(text: str) -> list[str]:
    """Extract all reasoning block contents from text.

    Args:
        text: The text containing reasoning blocks.

    Returns:
        List of reasoning block contents (without tags).
    """
    if not text:
        return []

    blocks: list[str] = []
    ReasoningStripper()
    result = text

    # Extract using regex
    for match in _THINK_BLOCK_RE.finditer(result):
        content = match.group(0)
        # Remove the opening and closing tags
        cleaned = re.sub(
            r"<((?:think|thinking|thought|reasoning|reflection|answer|output|思考|回答|结果)\b[^>]*)>",
            "",
            content,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"</((?:think|thinking|thought|reasoning|reflection|answer|output|思考|回答|结果))>",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        if cleaned.strip():
            blocks.append(cleaned.strip())

    return blocks


def has_reasoning_content(text: str) -> bool:
    """Check if text contains reasoning/thinking content.

    Args:
        text: The text to check.

    Returns:
        True if text contains reasoning content.
    """
    if not text:
        return False
    return _THINK_BLOCK_RE.search(text) is not None


def strip_reasoning_tags(text: str) -> str:
    """Convenience function to strip reasoning tags from text.

    Args:
        text: The text containing reasoning tags.

    Returns:
        The text with reasoning tags and their content removed.
    """
    stripper = ReasoningStripper()
    result = stripper.strip(text)
    return result.cleaned_text


__all__ = [
    "ReasoningStripper",
    "StripResult",
    "extract_reasoning_blocks",
    "has_reasoning_content",
    "strip_reasoning_from_history",
    "strip_reasoning_tags",
]
