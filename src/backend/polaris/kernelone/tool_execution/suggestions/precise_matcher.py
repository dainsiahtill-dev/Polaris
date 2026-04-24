"""Precise matcher with fuzzy fallback for LLM-generated search strings.

Handles common LLM character-level hallucinations:
- Missing spaces: 'return0' -> 'return 0'
- Wrong indentation: '  return' vs '    return'
- Missing newlines: 'if x:\nreturn' vs 'if x:\n    return'
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for comparison while preserving structure."""
    lines = text.split("\n")
    normalized_lines = []
    for line in lines:
        # Replace tabs with 4 spaces
        line = line.replace("\t", "    ")
        # Normalize multiple spaces to single space (but preserve leading spaces for indent)
        stripped = line.lstrip()
        leading = line[: len(line) - len(stripped)]
        # Normalize internal whitespace
        stripped = " ".join(stripped.split())
        normalized_lines.append(leading + stripped)
    return "\n".join(normalized_lines)


def _fix_common_syntax_errors(text: str) -> str:
    """Fix common LLM syntax errors in search strings."""
    fixes = [
        # Missing space between keyword and value
        (r"\breturn0\b", "return 0"),
        (r"\breturn1\b", "return 1"),
        (r"\breturnNone\b", "return None"),
        (r"\breturnTrue\b", "return True"),
        (r"\breturnFalse\b", "return False"),
        (r"\bprint0\b", "print(0)"),
        (r"\bprint1\b", "print(1)"),
        # Missing space in control flow
        (r"\bif\(", "if ("),
        (r"\bfor\(", "for ("),
        (r"\bwhile\(", "while ("),
        (r"\belif\(", "elif ("),
        (r"\bdef\(", "def ("),
        # Missing space around operators
        (r"\bif\s+\w+==", lambda m: m.group(0).replace("==", " == ")),
        (r"\bif\s+\w+!=", lambda m: m.group(0).replace("!=", " != ")),
    ]

    result = text
    for pattern, replacement in fixes:
        result = re.sub(pattern, replacement, result)
    return result


def _similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def find_best_match(
    content: str,
    search: str,
    threshold: float = 0.75,
) -> dict[str, Any] | None:
    """Find the best fuzzy match for search string in content.

    Args:
        content: Full file content
        search: The search string (may have errors)
        threshold: Minimum similarity threshold (0.0-1.0)

    Returns:
        Dict with matched_text, start_pos, end_pos, similarity, fixes_applied
        or None if no good match found
    """
    if not content or not search:
        return None

    # First try exact match
    if search in content:
        start = content.index(search)
        return {
            "matched_text": search,
            "start_pos": start,
            "end_pos": start + len(search),
            "similarity": 1.0,
            "fixes_applied": [],
            "exact": True,
        }

    fixes_applied = []

    # Try fixing common syntax errors
    fixed_search = _fix_common_syntax_errors(search)
    if fixed_search != search:
        fixes_applied.append(f"Fixed syntax: {search!r} -> {fixed_search!r}")
        if fixed_search in content:
            start = content.index(fixed_search)
            return {
                "matched_text": fixed_search,
                "start_pos": start,
                "end_pos": start + len(fixed_search),
                "similarity": 0.95,
                "fixes_applied": fixes_applied,
                "exact": False,
            }

    # SAFE whitespace-insensitive matching
    # Only allow whitespace compression when structure is preserved
    def _normalize_indent(text: str) -> str:
        """Normalize indentation to 4-space blocks while preserving structure."""
        lines = text.split("\n")
        result = []
        for line in lines:
            # Replace tabs with 4 spaces
            line = line.replace("\t", "    ")
            # Normalize multiple leading spaces to 4-space blocks
            stripped = line.lstrip()
            leading = line[: len(line) - len(stripped)]
            # Round leading spaces to nearest 4-space block
            if leading:
                space_count = len(leading)
                normalized_spaces = "    " * max(1, round(space_count / 4))
                result.append(normalized_spaces + stripped)
            else:
                result.append(stripped)
        return "\n".join(result)

    normalized_search = _normalize_indent(fixed_search)
    normalized_content = _normalize_indent(content)

    # Only use normalized matching if search has valid structure (non-empty, has content)
    if normalized_search.strip() and len(normalized_search.strip()) >= 10 and normalized_search in normalized_content:
        # Find the position in normalized content
        start = normalized_content.index(normalized_search)
        end = start + len(normalized_search)

        # Map back to original content using character mapping
        # This is approximate but safer than line-based mapping
        content_lines = content.split("\n")
        normalized_lines = normalized_content.split("\n")

        # Find which lines in normalized content correspond to the match
        char_count = 0
        start_line_idx = 0
        end_line_idx = 0

        for i, line in enumerate(normalized_lines):
            line_len = len(line) + 1  # +1 for newline
            if char_count <= start < char_count + line_len:
                start_line_idx = i
            if char_count < end <= char_count + line_len:
                end_line_idx = i + 1
                break
            char_count += line_len

        # Calculate byte positions in original content
        start_pos = sum(len(line) + 1 for line in content_lines[:start_line_idx])
        end_pos = sum(len(line) + 1 for line in content_lines[:end_line_idx]) - 1

        matched_text = "\n".join(content_lines[start_line_idx:end_line_idx])

        # SAFETY CHECK: Ensure the matched text has similar structure
        # (same number of non-empty lines, similar content)
        original_non_empty = [line.strip() for line in matched_text.split("\n") if line.strip()]
        search_non_empty = [line.strip() for line in search.split("\n") if line.strip()]

        if len(original_non_empty) == len(search_non_empty):
            return {
                "matched_text": matched_text,
                "start_pos": start_pos,
                "end_pos": end_pos,
                "similarity": 0.85,
                "fixes_applied": [*fixes_applied, "indentation normalization"],
                "exact": False,
            }

    # NOTE: Sliding window similarity matching is DISABLED because it can match
    # wrong locations in the file and destroy file structure.
    # Only exact matches and syntax fixes are allowed for safety.
    # This prevents cases where 'if not values:' matches a comment line or
    # content in a completely different function.

    return None


def fuzzy_replace(
    content: str,
    search: str,
    replace: str,
) -> tuple[str, dict[str, Any]]:
    """Perform fuzzy search and replace with indentation preservation.

    Args:
        content: Full file content
        search: The search string (may have errors)
        replace: Replacement text

    Returns:
        Tuple of (new_content, metadata)
        metadata contains: success, fixes_applied, similarity, original_matched
    """
    match = find_best_match(content, search)

    if not match:
        return content, {
            "success": False,
            "fixes_applied": [],
            "similarity": 0.0,
            "error": "No match found",
        }

    # Perform replacement
    start = match["start_pos"]
    end = match["end_pos"]
    matched_text = match["matched_text"]

    # Handle indentation preservation for multi-line replacements
    def _adjust_indentation(matched: str, replacement: str) -> str:
        """Adjust replacement indentation to match matched text structure."""
        matched_lines = matched.split("\n")
        replacement_lines = replacement.split("\n")

        if len(replacement_lines) == 1:
            # Single line replacement: use as-is
            return replacement

        # Multi-line case: preserve relative indentation
        # Get base indentation from first line of matched text
        first_matched = matched_lines[0]
        matched_base_indent = first_matched[: len(first_matched) - len(first_matched.lstrip())]

        # Get base indentation from first line of replacement
        first_replace = replacement_lines[0]
        replace_base_indent = first_replace[: len(first_replace) - len(first_replace.lstrip())]

        # Calculate the indentation difference
        # We need to adjust replacement so its structure matches the matched text
        adjusted_lines = []

        for _i, line in enumerate(replacement_lines):
            if not line.strip():
                # Empty line: keep as-is
                adjusted_lines.append(line)
                continue

            # Get this line's relative indentation within the replacement
            line_indent = line[: len(line) - len(line.lstrip())]
            relative_indent = (
                line_indent[len(replace_base_indent) :] if line_indent.startswith(replace_base_indent) else line_indent
            )

            # Apply matched base indent + relative indent
            new_indent = matched_base_indent + relative_indent
            adjusted_lines.append(new_indent + line.lstrip())

        return "\n".join(adjusted_lines)

    adjusted_replace = _adjust_indentation(matched_text, replace)
    new_content = content[:start] + adjusted_replace + content[end:]

    return new_content, {
        "success": True,
        "fixes_applied": match["fixes_applied"],
        "similarity": match["similarity"],
        "original_matched": matched_text,
        "exact": match.get("exact", False),
    }


__all__ = ["find_best_match", "fuzzy_replace"]
